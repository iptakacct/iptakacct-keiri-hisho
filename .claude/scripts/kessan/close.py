"""決算整理（kessan-close）の枠組み：候補を作る → 承認する → adjustments.csv に登録する。

項目ごとのモジュール（ITEMS の順に呼ぶ）は次の形にする:
- `collect(ctx: CloseContext) -> tuple[list[Candidate], list[Note]]`：候補（1候補＝1伝票）と所見を返す。帳簿には書かない
- `after_post(year_dir, posted_source_ids: list[str]) -> None`（任意）：候補が登録された後に呼ばれる（証憑の状態を変える等）。
  渡されるIDには他の項目の候補も含まれるので、自分の `close:<項目>:` のIDだけを扱う。**何度呼ばれても同じ結果になるように書く**
  （close-post の後に加えて、close-candidates のたびに adjustments.csv に登録済みの `close:` のIDすべてで呼び直し、
  前回の失敗を直すため）。
- `after_unpost(year_dir, source_ids: list[str]) -> None`（任意）：close-unpost で登録を取り消す前に呼ばれる（after_post で
  変えた状態を戻す）。自分の `close:<項目>:` のIDだけを扱い、何度呼ばれても同じ結果になるように書く。

collect は ITEMS の順に呼ばれ、前の項目が同じ回に出した候補は `ctx.pending` に入っている（消費税の概算に含めるため）。

候補はすべて「要確認」で adjustments-staging.csv に入れ、自動承認しない。取り込み元IDは `close:<項目>:<キー>`。
同じ取り込み元IDが登録済み（adjustments.csv）なら作り直さない。承認済み（staging）は内容が同じなら据え置き、
再計算で内容が変わったら最新の内容に置き換えて承認を外す（古い額のまま登録しない）。未承認なら最新の内容で置き換え、
もう出なくなった未承認の候補は消す。staging 内の伝票番号は C1, C2…、登録すると adjustments.csv で A1, A2…。
候補を1件だけ帳簿に足すとBS科目がマイナスになる・税区分が検算でNGになるものは staging に入れず、所見にする。
"""
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from pathlib import Path

import yaml

from accounts import normal_delta
from check import check_negative_balances, check_tax, has_ng, run_checks, unresolved_evidence, write_report
from common import (
    JOURNAL_COLUMNS, STAGING_COLUMNS, KessanError, add_reasons, cannot_write, ensure_writable, load_period, parse_amount, project,
    read_rows, replace_rows,
)
from evidence import read_evidence
from ledger import compute_balances, entry_rows, load_opening
from post import _group, _validate
from tax import load_tax_settings

STAGING_FILE = "adjustments-staging.csv"
ADJUSTMENTS_FILE = "adjustments.csv"
REPORT_FILE = "close-report.md"
OVERRIDES_FILE = "close-overrides.yaml"
OVERRIDE_ITEMS = ("消費税", "法人税等")
POSTED_KIND = "決算整理"
NEEDS_REVIEW = "要確認"
SOURCE_PREFIX = "close:"
TAX_ITEM = "消費税"
TAX_PREFIX = f"close:{TAX_ITEM}:"
CORP_PREFIX = "close:法人税等:"  # 法人税等は消費税の額を変えないので、消費税の登録順の判定では無視する
REJECTED_KEY = "rejected"
TAX_UNAPPROVED_REASON = "他の候補の却下で額が変わる（close-candidates を再実行）"


@dataclass(frozen=True)
class Candidate:
    """決算整理仕訳の候補（1候補＝1伝票）。

    lines の各行は JOURNAL_COLUMNS の列だけを持つ dict（借方科目・借方補助・借方金額・貸方科目・貸方補助・貸方金額・
    摘要・取引先・税の列など）。伝票番号・日付・取り込み元IDは枠組みが入れる。
    """
    item: str
    key: str
    date: str
    lines: list
    reason: str = ""

    @property
    def source_id(self):
        return f"{SOURCE_PREFIX}{self.item}:{self.key}"


@dataclass(frozen=True)
class Note:
    """仕訳にはしない所見（確かめてほしいこと）。"""
    item: str
    message: str


@dataclass
class CloseContext:
    """各項目の collect に渡す材料。company_dir はインスタンスの context/company（年度フォルダが外にあれば None）。"""
    year_dir: Path
    accounts: dict
    period: tuple
    tax_settings: object
    entries: list
    opening: dict
    evidence: list
    next_year_dir: Path | None = None
    prev_year_dir: Path | None = None
    payees: dict = field(default_factory=dict)
    abbreviations: list = field(default_factory=list)
    sources: dict = field(default_factory=dict)
    company_dir: Path | None = None
    pending: list = field(default_factory=list)  # この回に前の項目が出した候補（Candidate）

    @cached_property
    def balances(self):
        """科目・補助科目別の残高（ledger.compute_balances）。最初に使ったときに1回だけ計算する。"""
        return compute_balances(self.year_dir, self.accounts)

    def balance(self, name, sub=None):
        """科目の期末残高。sub を省くと補助科目をすべて足す。"""
        return sum(v["期末残高"] for (n, s), v in self.balances.items() if n == name and (sub is None or s == sub))

    def pending_entries(self):
        """ctx.pending のうち未登録の候補を、journal と同じ列の行にしたもの（登録済みの候補は帳簿の側で数える）。"""
        posted = {r["取り込み元ID"].strip() for r in self.entries}
        return [row for c in self.pending if c.source_id not in posted for row in _as_entries(c, STAGING_FILE)]


def _as_entries(candidate, file):
    """候補の行を、帳簿（entry_rows）と同じ形の行にする。伝票番号は取り込み元ID。"""
    rows = []
    for line in candidate.lines:
        row = dict.fromkeys(JOURNAL_COLUMNS, "")
        row.update({k: "" if v is None else str(v) for k, v in line.items()})
        row.update({"伝票番号": candidate.source_id, "日付": candidate.date, "取り込み元ID": candidate.source_id, "_file": file})
        rows.append(row)
    return rows


@dataclass(frozen=True)
class CloseResult:
    candidates_added: int  # 新しく入れた・最新の内容で置き換えた候補の数
    candidates_kept: int   # 承認済み・登録済みのため作り直さなかった候補の数
    candidates_removed: int  # もう出なくなったため消した未承認の候補の数
    notes: list
    report_path: Path
    candidates_rejected: int = 0  # close-reject で却下済みのため出さなかった候補の数


# ---- 検算・材料 ----

def _require_clean_check(year_dir, accounts, prev_year_dir, action):
    findings = run_checks(year_dir, accounts, prev_year_dir)
    write_report(year_dir, findings)
    if has_ng(findings):
        ng = sum(f.level == "NG" for f in findings)
        raise KessanError(f"検算にNGが{ng}件あるため{action}（output/check-result.md を確認）")


def _load_abbreviations():
    from match import load_abbreviations  # 略語表は未払・未収の照合で使う
    return load_abbreviations()


def build_context(year_dir, accounts, *, next_year_dir=None, prev_year_dir=None, sources=None, payees=None,
                  company_dir=None):
    year_dir = Path(year_dir)
    return CloseContext(
        year_dir=year_dir, accounts=accounts, period=load_period(year_dir), tax_settings=load_tax_settings(year_dir),
        entries=entry_rows(year_dir), opening=load_opening(year_dir), evidence=read_evidence(year_dir),
        next_year_dir=Path(next_year_dir) if next_year_dir else None,
        prev_year_dir=Path(prev_year_dir) if prev_year_dir else None,
        payees=payees or {}, abbreviations=_load_abbreviations(), sources=sources or {},
        company_dir=Path(company_dir) if company_dir else None,
    )


# ---- 候補 → staging の行 ----

def _candidate_rows(candidate, number):
    rows = []
    for i, line in enumerate(candidate.lines, start=1):
        unknown = set(line) - set(JOURNAL_COLUMNS)
        if unknown:
            raise KessanError(f"{candidate.source_id} {i}行目: 帳簿に無い列 {'・'.join(sorted(unknown))}")
        row = dict.fromkeys(STAGING_COLUMNS, "")
        row.update({k: "" if v is None else str(v) for k, v in line.items()})
        row.update({"伝票番号": number, "日付": candidate.date, "取り込み元ID": candidate.source_id,
                    "判定": NEEDS_REVIEW, "要確認理由": candidate.reason, "承認": ""})
        rows.append(row)
    return rows


def _validate_candidates(candidates, accounts, period):
    """候補を post と同じ規則（日付が期間内・貸借一致・科目がマスタにある）で検証する。1件でも不正なら何も書かない。"""
    seen, errors = set(), []
    for c in candidates:
        if c.source_id in seen:
            errors.append(f"{c.source_id}: 同じ取り込み元IDの候補が2つある")
        seen.add(c.source_id)
        if not c.lines:
            errors.append(f"{c.source_id}: 行の無い候補")
    if errors:
        raise KessanError("決算整理の候補が不正です（何も書いていません）:\n" + "\n".join(errors))
    rows = []
    for i, c in enumerate(candidates):
        rows += _candidate_rows(c, f"{c.source_id}#{i}")
    errors = _validate(_group(rows), accounts, [], period)
    if errors:
        raise KessanError("決算整理の候補が不正です（何も書いていません）:\n" + "\n".join(errors))


def _shape(rows, tax=False):
    """内容の比較用：日付・科目・補助・金額・摘要を並べたもの。登録済みとの比較では税の列を含めない（tax=False。set-tax で直した仕訳を「違う」としない）。"""
    def amount(v):
        try:
            return parse_amount(v, "金額")
        except KessanError:
            return v
    return sorted((r["日付"].strip(), r["借方科目"].strip(), r["借方補助"].strip(), str(amount(r["借方金額"])),
                   r["借方税区分"].strip() if tax else "", r["貸方科目"].strip(), r["貸方補助"].strip(),
                   str(amount(r["貸方金額"])), r["貸方税区分"].strip() if tax else "", r["摘要"].strip()) for r in rows)


def _candidate_issues(ctx, candidate):
    """候補を1件だけ帳簿に足したときの問題（BS科目がマイナスになる・税区分が検算でNG）のメッセージ。無ければ空。"""
    rows = _as_entries(candidate, ADJUSTMENTS_FILE)
    moves = defaultdict(lambda: [0, 0])  # (科目, 補助) -> [借方, 貸方]
    for r in rows:
        for i, side in enumerate(("借方", "貸方")):
            name = r[f"{side}科目"].strip()
            if name:
                moves[(name, r[f"{side}補助"].strip())][i] += parse_amount(r[f"{side}金額"], candidate.source_id)
    issues = []
    for (name, sub), (debit, credit) in moves.items():
        account = ctx.accounts.get(name)
        if account is None or account.category not in ("資産", "負債"):
            continue
        before = ctx.balances.get((name, sub), {}).get("期末残高", 0)
        after = before + normal_delta(account, debit, credit)
        if after < 0 and after < before:
            issues.append(f"{_account_label(name, sub)}の期末残高が{after:,}円（マイナス）になる")
    issues += [f.message for f in check_tax(rows, ctx.accounts, ctx.tax_settings) if f.level == "NG"]
    return issues


STALE_REASON = "今回の再計算では出ていない（不要なら削除、必要なら承認し直す）"
CHANGED_REASON = "再計算で内容が変わった（確認して承認し直す）"


def _item_of(source_id):
    """close:<項目>:<キー> の項目。"""
    return source_id[len(SOURCE_PREFIX):].split(":", 1)[0] if source_id.startswith(SOURCE_PREFIX) else source_id


def _c_number(no):
    no = no.strip()
    return int(no[1:]) if no.startswith("C") and no[1:].isdigit() else 0


def _by_source(rows):
    out = {}
    for r in rows:
        out.setdefault(r["取り込み元ID"].strip(), []).append(r)
    return out


def _reconcile(year_dir, candidates):
    """staging の既存行と突き合わせる。

    (新しい staging の行, 状態 {取り込み元ID: 状態}, 所見, 消した数, 報告に載せる行 {取り込み元ID: staging か登録済みの行})。
    """
    existing = [project(r, STAGING_COLUMNS) for r in read_rows(Path(year_dir) / STAGING_FILE)]
    posted = _by_source(read_rows(Path(year_dir) / ADJUSTMENTS_FILE))
    current = _by_source(existing)
    approved_ids = {sid for sid, rows in current.items() if any(r["承認"].strip() == "済" for r in rows)}
    wanted = {c.source_id for c in candidates}

    kept_rows = [r for r in existing
                 if r["取り込み元ID"].strip() not in posted and r["取り込み元ID"].strip() in approved_ids]
    removed = len({sid for sid in current if sid not in posted and sid not in approved_ids and sid not in wanted})
    next_no = max((_c_number(r["伝票番号"]) for r in kept_rows), default=0) + 1

    notes = []
    # 承認済みなのに今回出なかった候補は、行を残して承認を外す（再承認するまで登録しない）
    for sid in [x for x in dict.fromkeys(r["取り込み元ID"].strip() for r in kept_rows) if x not in wanted]:
        for r in kept_rows:
            if r["取り込み元ID"].strip() == sid:
                r.update({"承認": "", "判定": NEEDS_REVIEW, "要確認理由": add_reasons(r["要確認理由"], STALE_REASON)})
        notes.append(Note(_item_of(sid), f"{sid} は承認済みでしたが今回の再計算では出ていないため、承認を外しました"
                                         f"（不要なら close-reject で却下、必要なら承認し直す）"))
    # 承認済みでも再計算で内容が変わった候補は、最新の内容に置き換えて承認を外す（古い額のまま登録しない）
    by_id = {c.source_id: c for c in candidates}
    changed = {sid for sid, c in by_id.items()
               if sid in approved_ids and sid not in posted and _shape(current[sid], True) != _shape(_candidate_rows(c, ""), True)}
    new_rows, states, shown = [], {}, {}
    for sid, rows in _by_source(kept_rows).items():
        if sid in changed:
            c = by_id[sid]
            rows = _candidate_rows(c, rows[0]["伝票番号"])
            for r in rows:
                r["要確認理由"] = add_reasons(c.reason, CHANGED_REASON)
        shown[sid] = rows
        new_rows += rows
    for c in candidates:
        sid = c.source_id
        fresh = _candidate_rows(c, "")
        if sid in posted:
            states[sid] = "登録済み"
            shown[sid] = posted[sid]
            if _shape(posted[sid]) != _shape(fresh):
                notes.append(Note(c.item, f"{sid} は登録済みの内容と再計算の結果が違います（直すなら close-unpost --ids {sid} で"
                                          "登録を取り消してから close-candidates をやり直す）"))
        elif sid in changed:
            states[sid] = "変更"
            notes.append(Note(c.item, f"{sid} は承認済みでしたが再計算で内容が変わったため、最新の内容に置き換えて承認を外しました"
                                      "（確認して承認し直す）"))
        elif sid in approved_ids:
            states[sid] = "承認済み"
        else:
            states[sid] = "新規"
            for r in fresh:
                r["伝票番号"] = f"C{next_no}"
            next_no += 1
            new_rows += fresh
            shown[sid] = fresh
    return new_rows, states, notes, removed, shown


# ---- 報告 ----

def _cell(text):
    return str(text or "").replace("|", "｜").replace("\n", " ")


def _account_label(name, sub):
    return f"{name}（{sub}）" if sub else name


def _table_rows(lines, where):
    """行（候補の lines か、staging・adjustments.csv の行）を (借方, 借方税区分, 貸方, 貸方税区分, 金額) の並びにする。"""
    def side(line, name):
        return (str(line.get(f"{name}科目") or "").strip(), str(line.get(f"{name}補助") or "").strip(),
                parse_amount(line.get(f"{name}金額", ""), where), str(line.get(f"{name}税区分") or "").strip())

    out = []
    for line in lines:
        debit, credit = side(line, "借方"), side(line, "貸方")
        if debit[0] and credit[0] and debit[2] == credit[2]:
            out.append((_account_label(*debit[:2]), debit[3], _account_label(*credit[:2]), credit[3], debit[2]))
            continue
        if debit[0]:
            out.append((_account_label(*debit[:2]), debit[3], "", "", debit[2]))
        if credit[0]:
            out.append(("", "", _account_label(*credit[:2]), credit[3], credit[2]))
    return out


TABLE_HEADER = ["| 日付 | 借方 | 借方税区分 | 貸方 | 貸方税区分 | 金額 | 要確認理由 |", "|---|---|---|---|---|---|---|"]


def _table(date, lines, where, reason):
    out = list(TABLE_HEADER)
    for i, (debit, debit_tax, credit, credit_tax, amount) in enumerate(_table_rows(lines, where)):
        out.append(f"| {date} | {_cell(debit)} | {_cell(debit_tax)} | {_cell(credit)} | {_cell(credit_tax)} | {amount:,} "
                   f"| {_cell(reason if i == 0 else '')} |")
    return out


STATE_LABELS = {"新規": "要確認（未承認）", "変更": "要確認（再計算で内容が変わったため承認を外した）",
                "承認済み": "承認済み（作り直していません）", "登録済み": "登録済み"}
NEEDS_APPROVAL = ("新規", "変更")


def _write_report(year_dir, candidates, notes, states, removed, now, rejected=(), shown=None):
    """shown：候補ごとに載せる行（承認済みは staging の行、登録済みは adjustments.csv の行）。無ければ候補の行。"""
    shown = shown or {}
    items = list(dict.fromkeys([c.item for c in candidates] + [n.item for n in notes]))
    added = sum(states[c.source_id] in NEEDS_APPROVAL for c in candidates)
    lines = [f"# 決算整理の候補（{now:%Y-%m-%d %H:%M}）", "",
             f"候補 {len(candidates)}件（要確認 {added}件 / 承認済み・登録済みのため据え置き {len(candidates) - added}件）"
             f" / 所見 {len(notes)}件 / 出なくなったため消した候補 {removed}件", "",
             "内容を確かめて close-approve --ids <取り込み元ID>（全件なら --all。ただし消費税の概算の候補は --all では承認せず残す）"
             "→ close-post で登録します。", ""]
    if not items:
        lines += ["候補・所見はありません。", ""]
    for item in items:
        lines += [f"## {item}", ""]
        for c in (c for c in candidates if c.item == item):
            sid = c.source_id
            rows = shown.get(sid) or c.lines
            # 候補の行ならその理由、staging・adjustments.csv の行ならその行の理由（再計算の理由を混ぜない）
            reason = c.reason if rows is c.lines else str(rows[0].get("要確認理由") or "")
            lines += [f"### {sid}（{STATE_LABELS[states[sid]]}）", ""] + _table(c.date, rows, sid, reason) + [""]
            if rows is not c.lines and _shape(rows) != _shape(_candidate_rows(c, "")):
                lines += ["再計算の結果（上の内容と違う。直すなら close-unpost で登録を取り消してから close-candidates）:", ""]
                lines += _table(c.date, c.lines, sid, c.reason) + [""]
        item_notes = [n for n in notes if n.item == item]
        if item_notes:
            lines += [f"- {n.message}" for n in item_notes] + [""]
    if rejected:
        lines += ["## 却下した候補（close-reject）", ""]
        lines += [f"- {sid}：{_cell(reason)}" for sid, reason in rejected] + [""]
    path = Path(year_dir) / "output" / REPORT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        raise KessanError(f"output/{REPORT_FILE} に書き込めません（開いていたら閉じてから再実行）") from None
    return path


# ---- コマンド本体 ----

def run_close(year_dir, accounts, *, next_year_dir=None, prev_year_dir=None, sources=None, payees=None,
              company_dir=None, items=None, now=None):
    """決算整理の候補を作り直す（adjustments-staging.csv と output/close-report.md）。"""
    year_dir = Path(year_dir)
    modules = ITEMS if items is None else items
    _require_clean_check(year_dir, accounts, prev_year_dir, "決算整理の候補を作りません")
    # 前回の close-post で登録後の処理（after_post）が止まっていても、ここで直す（after_post は何度呼んでもよい）
    posted_ids = list(dict.fromkeys(
        r["取り込み元ID"].strip() for r in read_rows(year_dir / ADJUSTMENTS_FILE)
        if r["取り込み元ID"].strip().startswith(SOURCE_PREFIX)))
    if posted_ids:
        failures = _run_hooks(modules, "after_post", year_dir, posted_ids)
        if failures:
            raise KessanError("登録済みの決算整理仕訳の後処理（after_post）で止まりました:\n" + "\n".join(failures))
    unresolved = unresolved_evidence(year_dir, entry_rows(year_dir))
    if unresolved:
        raise KessanError(f"片づいていない証憑が{len(unresolved)}件あるため決算整理の候補を作りません"
                          "（resolve-evidence で解決してから再実行）:\n" + "\n".join(unresolved))
    ctx = build_context(year_dir, accounts, next_year_dir=next_year_dir, prev_year_dir=prev_year_dir,
                        sources=sources, payees=payees, company_dir=company_dir)
    rejected = load_rejected(year_dir)
    candidates, notes, dropped = [], [], []
    for module in modules:
        found, found_notes = module.collect(ctx)
        dropped += [(c.source_id, rejected[c.source_id]) for c in found if c.source_id in rejected]
        found = [c for c in found if c.source_id not in rejected]  # 却下済みは staging にも ctx.pending にも入れない
        notes += found_notes
        usable = []
        for c in found:
            # 1件だけ足してもBS科目がマイナス・税区分がNGになる候補は入れない（登録すると検算がNGになり先へ進めなくなる）。
            # 登録済みの候補は帳簿に入っているので、足し直して数えない
            issues = [] if c.source_id in posted_ids else _candidate_issues(ctx, c)
            if issues:
                notes.append(Note(c.item, f"{c.source_id} は候補にしません：{'。'.join(issues)}"
                                          "（元の仕訳・台帳・登録簿・証憑を確かめて直してから close-candidates をやり直す）"))
            else:
                usable.append(c)
        candidates += usable
        ctx.pending.extend(usable)
    _validate_candidates(candidates, accounts, ctx.period)

    rows, states, extra_notes, removed, shown = _reconcile(year_dir, candidates)
    notes += extra_notes
    ensure_writable(year_dir, [STAGING_FILE])
    try:
        replace_rows(year_dir / STAGING_FILE, STAGING_COLUMNS, rows)
    except OSError:
        raise cannot_write(STAGING_FILE) from None
    report = _write_report(year_dir, candidates, notes, states, removed, now or datetime.now(), dropped, shown)
    added = sum(s in NEEDS_APPROVAL for s in states.values())
    return CloseResult(candidates_added=added, candidates_kept=len(states) - added, candidates_removed=removed,
                       notes=notes, report_path=report, candidates_rejected=len(dropped))


def candidate_ids(year_dir):
    """adjustments-staging.csv にある候補の取り込み元ID（順序どおり、重複なし）。"""
    return list(dict.fromkeys(r["取り込み元ID"].strip() for r in read_rows(Path(year_dir) / STAGING_FILE)
                              if r["取り込み元ID"].strip()))


OVERRIDE_ORIGIN = "上書き額"
STALE_AMOUNT_REASON = "close-set-amount で額を入れたので close-candidates をやり直す"
HOLD_ESTIMATE = ("消費税の額が概算のため。達人の額を close-set-amount --item 消費税 で入れて close-candidates を流し直してから承認する。"
                 "概算のまま登録するなら --ids で指定して承認する")


def _hold_stale(item):
    return (f"close-set-amount で入れた{item}の額がこの候補にまだ入っていないため（古い額のまま）。"
            "close-candidates を流し直してから承認する")


def tax_year_reason(annual, origin):
    """消費税の候補の要確認理由の頭（年税額と出どころ）。承認の判定もこの形で照合する。"""
    return f"消費税の年税額 {annual:,}円：{origin}"


def corp_unpaid_reason(unpaid):
    """法人税等の候補の要確認理由に入れる上書き額の部分。承認の判定もこの形で照合する。"""
    return f"期末の未払額 {unpaid:,}円：{OVERRIDE_ORIGIN}（達人で計算した額。マイナスは還付）"


def _override_mark(item, amount):
    """候補が今の上書き額で作られたことを示す、要確認理由の中の文字列。"""
    return tax_year_reason(amount, OVERRIDE_ORIGIN) if item == TAX_ITEM else corp_unpaid_reason(amount)


def _hold_reason(item, rows, overrides):
    """上書き額で決まる候補（消費税・法人税等。同じ取り込み元IDの行）を承認してはいけない理由。承認してよければ None。

    消費税で上書き額が無ければ概算。上書き額があっても、候補が今の上書き額で作られていなければ古い額
    （close-set-amount の後に close-candidates を流していない）。
    """
    reasons = " ".join(r["要確認理由"] or "" for r in rows)
    if item not in overrides:
        return HOLD_ESTIMATE if item == TAX_ITEM else _hold_stale(item)
    if STALE_AMOUNT_REASON in reasons or _override_mark(item, overrides[item]) not in reasons:
        return _hold_stale(item)
    return None


def _override_rows(rows):
    """上書き額で決まる候補の行：{取り込み元ID: (項目, [行])}。"""
    by_id = {}
    for r in rows:
        sid = r["取り込み元ID"].strip()
        for item in OVERRIDE_ITEMS:
            if sid.startswith(f"{SOURCE_PREFIX}{item}:"):
                by_id.setdefault(sid, (item, []))[1].append(r)
    return by_id


def _all_approved(rows):
    return all(r["承認"].strip() == "済" for r in rows)


def approve_all_ids(year_dir):
    """--all で承認する取り込み元IDと、承認せずに残す候補 [(取り込み元ID, 理由)]。

    消費税・法人税等の候補は、概算（消費税で上書き額が無い）か、今の上書き額で作り直していないときは残す
    （承認済みの行はそのまま）。
    """
    year_dir = Path(year_dir)
    rows = read_rows(year_dir / STAGING_FILE)
    overrides = load_overrides(year_dir)
    held = []
    for sid, (item, item_rows) in _override_rows(rows).items():
        if _all_approved(item_rows):
            continue
        why = _hold_reason(item, item_rows, overrides)
        if why:
            held.append((sid, why))
    held_ids = {sid for sid, _ in held}
    return [s for s in candidate_ids(year_dir) if s not in held_ids], held


def approve_candidates(year_dir, source_ids):
    """取り込み元IDの候補を 承認=済 にする。staging に無いIDが1つでもあれば何も変えない。承認した行数を返す。"""
    year_dir = Path(year_dir)
    ids = list(dict.fromkeys(s.strip() for s in source_ids if s and s.strip()))
    if not ids:
        raise KessanError("承認する候補がありません（close-candidates で候補を作ってから）")
    rows = read_rows(year_dir / STAGING_FILE)
    present = {r["取り込み元ID"].strip() for r in rows}
    missing = [s for s in ids if s not in present]
    if missing:
        raise KessanError(f"{STAGING_FILE} に無い取り込み元ID（何も承認していません）: " + "、".join(missing))
    # 消費税の概算は --ids で明示すれば承認できる。ただし上書き額を入れた後の古い候補（消費税・法人税等）は承認させない
    overrides = load_overrides(year_dir)
    for sid, (item, item_rows) in _override_rows(rows).items():
        if sid not in ids or _all_approved(item_rows):
            continue
        why = _hold_reason(item, item_rows, overrides)
        if why and why != HOLD_ESTIMATE:
            raise KessanError(f"{sid} は承認できません（何も承認していません）: {why}")
    count = 0
    for r in rows:
        if r["取り込み元ID"].strip() in ids:
            r["承認"] = "済"
            count += 1
    ensure_writable(year_dir, [STAGING_FILE])
    try:
        replace_rows(year_dir / STAGING_FILE, STAGING_COLUMNS, [project(r, STAGING_COLUMNS) for r in rows])
    except OSError:
        raise cannot_write(STAGING_FILE) from None
    return count


def unapprove_candidates(year_dir, source_ids):
    """取り込み元IDの候補の承認を外す（承認=空・判定=要確認）。外した行数を返す。

    staging に無いID・登録済み（adjustments.csv）のIDが1つでもあれば何も変えない。
    """
    year_dir = Path(year_dir)
    ids = list(dict.fromkeys(s.strip() for s in source_ids if s and s.strip()))
    if not ids:
        raise KessanError("承認を外す候補がありません")
    posted = {r["取り込み元ID"].strip() for r in read_rows(year_dir / ADJUSTMENTS_FILE)}
    done = [s for s in ids if s in posted]
    if done:
        raise KessanError("登録済みの候補は承認を外せません（close-unpost で登録を取り消してから）: " + "、".join(done))
    rows = read_rows(year_dir / STAGING_FILE)
    present = {r["取り込み元ID"].strip() for r in rows}
    missing = [s for s in ids if s not in present]
    if missing:
        raise KessanError(f"{STAGING_FILE} に無い取り込み元ID（何も変えていません）: " + "、".join(missing))
    count = 0
    for r in rows:
        if r["取り込み元ID"].strip() in ids:
            r.update({"承認": "", "判定": NEEDS_REVIEW})
            count += 1
    ensure_writable(year_dir, [STAGING_FILE])
    try:
        replace_rows(year_dir / STAGING_FILE, STAGING_COLUMNS, [project(r, STAGING_COLUMNS) for r in rows])
    except OSError:
        raise cannot_write(STAGING_FILE) from None
    return count


def _a_number(no):
    no = no.strip()
    return int(no[1:]) if no.startswith("A") and no[1:].isdigit() else 0


def post_candidates(year_dir, accounts, now=None, *, prev_year_dir=None, items=None):
    """承認済みの候補を adjustments.csv に A<n> で登録し、staging から消す。登録した伝票番号の一覧を返す。

    登録前に検算を流し、NGがあれば拒否する。1件でも不正な候補があれば何も登録しない。
    """
    year_dir = Path(year_dir)
    staging = read_rows(year_dir / STAGING_FILE)
    approved = [r for r in staging if r["承認"].strip() == "済"]
    remaining = [r for r in staging if r["承認"].strip() != "済"]
    if not approved:
        return []
    _require_clean_check(year_dir, accounts, prev_year_dir, "決算整理仕訳を登録しません")

    if any(r["取り込み元ID"].strip().startswith(TAX_PREFIX) for r in approved) and any(
            not r["取り込み元ID"].strip().startswith((TAX_PREFIX, CORP_PREFIX)) for r in remaining):
        raise KessanError("消費税の候補は、他の決算整理の候補をすべて承認してから登録する（消費税の額が変わるため）")
    groups = _group(approved)
    errors = _validate(groups, accounts, entry_rows(year_dir), load_period(year_dir))
    errors += [f"伝票{key}: 取り込み元IDが空" for key, lines in groups.items()
               if any(not r["取り込み元ID"].strip() for r in lines)]
    if errors:
        raise KessanError("承認済みの候補を登録できません（何も登録していません）:\n" + "\n".join(errors))
    # 登録した後の帳簿を組み立てて、マイナス残高・税区分の検算を先に流す（登録で検算がNGになる形は書かない）
    extra = [{**project(r, JOURNAL_COLUMNS), "_file": ADJUSTMENTS_FILE} for r in approved]
    findings = check_negative_balances(year_dir, accounts, extra) + check_tax(
        entry_rows(year_dir) + extra, accounts, load_tax_settings(year_dir), read_evidence(year_dir))
    ng = [f.message for f in findings if f.level == "NG"]
    if ng:
        raise KessanError("承認済みの候補を登録すると検算がNGになるため、何も登録していません"
                          "（close-unapprove で外すか、原因を直して close-candidates をやり直す）:\n" + "\n".join(ng))
    ensure_writable(year_dir, [ADJUSTMENTS_FILE, STAGING_FILE])

    adjustments = read_rows(year_dir / ADJUSTMENTS_FILE)
    next_no = max((_a_number(r["伝票番号"]) for r in adjustments), default=0) + 1
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    new_rows, numbers, posted_ids = [], [], []
    for lines in groups.values():
        no = f"A{next_no}"
        next_no += 1
        numbers.append(no)
        for r in lines:
            row = project(r, JOURNAL_COLUMNS)
            row.update({"伝票番号": no, "日付": r["日付"].strip(), "登録区分": POSTED_KIND, "登録日時": stamp})
            new_rows.append(row)
            posted_ids.append(r["取り込み元ID"].strip())
    try:
        replace_rows(year_dir / ADJUSTMENTS_FILE, JOURNAL_COLUMNS,
                     [project(r, JOURNAL_COLUMNS) for r in adjustments] + new_rows)
    except OSError:
        raise cannot_write(ADJUSTMENTS_FILE) from None

    # ここから先は登録が確定済み。staging に残っても、再実行では取り込み元IDが登録済みとして拒否される。
    # 途中で失敗しても残りの処理は続け、失敗はまとめて伝える
    failures = []
    try:
        replace_rows(year_dir / STAGING_FILE, STAGING_COLUMNS, [project(r, STAGING_COLUMNS) for r in remaining])
    except OSError:
        failures.append(f"{STAGING_FILE} の更新に失敗（閉じてから close-candidates を実行すると、登録済みの候補は staging から消えます）")
    failures += _run_hooks(ITEMS if items is None else items, "after_post", year_dir, list(dict.fromkeys(posted_ids)))
    if failures:
        raise KessanError(f"決算整理仕訳の登録は完了済み（{', '.join(numbers)}）ですが、登録後の処理で止まったものがあります"
                          "（次の close-candidates で後処理をやり直します）:\n" + "\n".join(failures))
    return numbers


def _run_hooks(modules, name, year_dir, source_ids):
    """各項目の after_post／after_unpost を呼ぶ。1つが失敗しても残りは呼び、失敗のメッセージの一覧を返す。"""
    failures = []
    for module in modules:
        hook = getattr(module, name, None)
        if hook is None:
            continue
        try:
            hook(year_dir, source_ids)
        except Exception as e:  # noqa: BLE001  どの失敗でも他の項目の処理を止めず、まとめて伝える
            failures.append(f"{getattr(module, '__name__', type(module).__name__)}: {e}")
    return failures


def unpost_candidates(year_dir, source_ids, *, items=None):
    """登録済みの決算整理仕訳（close: の取り込み元ID）を adjustments.csv から取り除く。取り除いた伝票番号の一覧を返す。

    他の伝票の番号は振り直さない。先に各項目の after_unpost（証憑の状態を戻す等）を呼び、1つでも失敗したら何も取り除かない
    （戻した状態は、伝票が残っている限り次の close-candidates の after_post で元に戻る）。
    消費税の伝票が登録済みなら、他の項目の伝票は消費税と一緒でないと取り消せない（消費税の額が変わるため）。
    """
    year_dir = Path(year_dir)
    ids = list(dict.fromkeys(s.strip() for s in source_ids if s and s.strip()))
    if not ids:
        raise KessanError("登録を取り消す伝票の取り込み元IDがありません")
    bad = [s for s in ids if not s.startswith(SOURCE_PREFIX)]
    if bad:
        raise KessanError("決算整理の候補（close:…）から登録した伝票ではない取り込み元ID（何も取り消していません。"
                          "手で入れた伝票は手で直す）: " + "、".join(bad))
    adjustments = read_rows(year_dir / ADJUSTMENTS_FILE)
    present = list(dict.fromkeys(r["取り込み元ID"].strip() for r in adjustments))
    missing = [s for s in ids if s not in present]
    if missing:
        raise KessanError(f"{ADJUSTMENTS_FILE} に無い取り込み元ID（何も取り消していません）: " + "、".join(missing))
    posted_tax = [s for s in present if s.startswith(TAX_PREFIX) and s not in ids]
    if posted_tax and any(not s.startswith((TAX_PREFIX, CORP_PREFIX)) for s in ids):
        raise KessanError("消費税の伝票が登録済みのため、他の項目の伝票は消費税と一緒に取り消す（消費税の額が変わるため。"
                          "--ids に " + "、".join(posted_tax) + " も足す）")
    ensure_writable(year_dir, [ADJUSTMENTS_FILE])
    failures = _run_hooks(ITEMS if items is None else items, "after_unpost", year_dir, ids)
    if failures:
        raise KessanError("登録を取り消す前の処理（after_unpost）で止まったため、何も取り消していません"
                          "（原因を直してからもう一度 close-unpost）:\n" + "\n".join(failures))
    numbers = list(dict.fromkeys(r["伝票番号"].strip() for r in adjustments if r["取り込み元ID"].strip() in ids))
    try:
        replace_rows(year_dir / ADJUSTMENTS_FILE, JOURNAL_COLUMNS,
                     [project(r, JOURNAL_COLUMNS) for r in adjustments if r["取り込み元ID"].strip() not in ids])
    except OSError:
        raise cannot_write(ADJUSTMENTS_FILE) from None
    return numbers


# ---- 上書き額（達人で計算した額） ----

def _check_amount(item, amount, where):
    if item not in OVERRIDE_ITEMS:
        raise KessanError(f"{where}: 項目は {'／'.join(OVERRIDE_ITEMS)} のどちらかにしてください（現在: {item}）")
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise KessanError(f"{where}: {item}の金額は整数にしてください（還付はマイナス。現在: {amount!r}）")


def _load_raw(year_dir):
    """close-overrides.yaml の中身（{消費税: 金額, 法人税等: 金額, rejected: {取り込み元ID: 理由}}）。無ければ空。"""
    path = Path(year_dir) / OVERRIDES_FILE
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise KessanError(f"{OVERRIDES_FILE} を読めません: {e}") from None
    except UnicodeDecodeError:
        raise KessanError(f"{OVERRIDES_FILE} が UTF-8 で読めません") from None
    if not isinstance(data, dict):
        raise KessanError(f"{OVERRIDES_FILE} は「消費税: 金額」「法人税等: 金額」の形で書いてください")
    return data


def load_overrides(year_dir):
    """上書き額（{消費税: 金額, 法人税等: 金額}）。無ければ空。"""
    data = {k: v for k, v in _load_raw(year_dir).items() if k != REJECTED_KEY}
    for item, amount in data.items():
        _check_amount(item, amount, OVERRIDES_FILE)
    return data


def load_rejected(year_dir):
    """却下した候補（{取り込み元ID: 理由}）。無ければ空。"""
    rejected = _load_raw(year_dir).get(REJECTED_KEY) or {}
    if not isinstance(rejected, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in rejected.items()):
        raise KessanError(f"{OVERRIDES_FILE} の {REJECTED_KEY} は「取り込み元ID: 理由」の形で書いてください")
    return dict(rejected)


def _save_raw(year_dir, data):
    path = Path(year_dir) / OVERRIDES_FILE
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        tmp_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.replace(str(tmp_path), str(path))
    except OSError:
        raise KessanError(f"{OVERRIDES_FILE} に書き込めません") from None


AMOUNT_CHANGED_REASON = "上書き額を変えた（close-candidates を再実行して承認し直す）"


def set_amount(year_dir, item, amount):
    """達人で計算した額を close-overrides.yaml に保存する（次の close-candidates で候補に使う）。マイナスは還付。

    その項目の承認済みの候補は承認を外す（古い額のまま登録しない）。
    """
    year_dir = Path(year_dir)
    _check_amount(item, amount, "close-set-amount")
    load_overrides(year_dir)
    load_rejected(year_dir)
    data = _load_raw(year_dir)
    prefix = f"{SOURCE_PREFIX}{item}:"
    staging = read_rows(year_dir / STAGING_FILE)
    targets = [r for r in staging if r["取り込み元ID"].strip().startswith(prefix) and r["承認"].strip() == "済"]
    # 未承認の候補は古い額（消費税なら概算など）のまま。流し直すまで承認させないよう印を付ける
    stale = [r for r in staging if r["取り込み元ID"].strip().startswith(prefix) and r["承認"].strip() != "済"]
    if targets or stale:
        for r in targets:
            r.update({"承認": "", "判定": NEEDS_REVIEW, "要確認理由": add_reasons(r["要確認理由"], AMOUNT_CHANGED_REASON)})
        for r in stale:
            r["要確認理由"] = add_reasons(r["要確認理由"], STALE_AMOUNT_REASON)
        ensure_writable(year_dir, [STAGING_FILE])
        try:
            replace_rows(year_dir / STAGING_FILE, STAGING_COLUMNS, [project(r, STAGING_COLUMNS) for r in staging])
        except OSError:
            raise cannot_write(STAGING_FILE) from None
    data[item] = amount
    _save_raw(year_dir, data)  # staging を先に書く（上書き額だけ変わって承認済みの古い額が残る形にしない）


def reject_candidates(year_dir, source_ids, reason):
    """候補を却下する：以後の close-candidates はその取り込み元IDの候補を出さない（close-report.md に理由つきで載せる）。

    staging にある行は消す。登録済み（adjustments.csv）のIDは却下できない（close-unpost で登録を取り消してから）。
    """
    year_dir = Path(year_dir)
    ids = list(dict.fromkeys(s.strip() for s in source_ids if s and s.strip()))
    reason = (reason or "").strip()
    if not ids:
        raise KessanError("却下する候補の取り込み元IDがありません")
    if not reason:
        raise KessanError("却下の理由を書いてください（--reason）")
    bad = [s for s in ids if not s.startswith(SOURCE_PREFIX)]
    if bad:
        raise KessanError("決算整理の候補（close:…）ではない取り込み元ID（何も却下していません）: " + "、".join(bad))
    posted = {r["取り込み元ID"].strip() for r in read_rows(year_dir / ADJUSTMENTS_FILE)}
    done = [s for s in ids if s in posted]
    if done:
        raise KessanError("登録済みの候補は却下できません（close-unpost で登録を取り消してから）: " + "、".join(done))
    load_overrides(year_dir)
    rejected = load_rejected(year_dir)
    staging = read_rows(year_dir / STAGING_FILE)
    known = {r["取り込み元ID"].strip() for r in staging} | set(rejected)
    unknown = [s for s in ids if s not in known]
    if unknown:
        raise KessanError("候補にないID（何も却下していません。close-candidates で出た取り込み元IDを指定する）: " + "、".join(unknown))
    data = _load_raw(year_dir)
    rejected.update({s: reason for s in ids})
    data[REJECTED_KEY] = rejected
    # 消費税・法人税等以外の候補を却下すると消費税の額が変わるので、承認済みの消費税の候補は承認を外す
    unapprove_tax = any(not s.startswith((TAX_PREFIX, CORP_PREFIX)) for s in ids)
    new_rows, changed = [], False
    for r in staging:
        sid = r["取り込み元ID"].strip()
        if sid in ids:
            changed = True
            continue
        if unapprove_tax and sid.startswith(TAX_PREFIX) and r["承認"].strip() == "済":
            r.update({"承認": "", "判定": NEEDS_REVIEW, "要確認理由": add_reasons(r["要確認理由"], TAX_UNAPPROVED_REASON)})
            changed = True
        new_rows.append(project(r, STAGING_COLUMNS))
    if changed:
        ensure_writable(year_dir, [STAGING_FILE])
        try:
            replace_rows(year_dir / STAGING_FILE, STAGING_COLUMNS, new_rows)
        except OSError:
            raise cannot_write(STAGING_FILE) from None
    _save_raw(year_dir, data)  # staging を先に書く（失敗しても却下だけ記録されて承認済みの行が残る形にならない）


def unreject_candidates(year_dir, source_ids):
    """却下を取り消す（次の close-candidates からまた候補に出る）。却下していないIDが1つでもあれば何も変えない。"""
    year_dir = Path(year_dir)
    ids = list(dict.fromkeys(s.strip() for s in source_ids if s and s.strip()))
    if not ids:
        raise KessanError("却下を取り消す候補の取り込み元IDがありません")
    load_overrides(year_dir)
    rejected = load_rejected(year_dir)
    unknown = [s for s in ids if s not in rejected]
    if unknown:
        raise KessanError("却下していない取り込み元ID（何も変えていません）: " + "、".join(unknown))
    data = _load_raw(year_dir)
    data[REJECTED_KEY] = {k: v for k, v in rejected.items() if k not in ids}
    _save_raw(year_dir, data)


# 項目の順：未払・未収 → 前払 → 減価償却 → 役員報酬・残高の所見（候補なし） → 消費税・法人税等
import close_payable  # noqa: E402  （項目は close の型を使うので、型を定義した後で読む）
import close_prepaid  # noqa: E402
import close_depreciation  # noqa: E402
import close_review  # noqa: E402
import close_tax  # noqa: E402

ITEMS = [close_payable, close_prepaid, close_depreciation, close_review, close_tax]
