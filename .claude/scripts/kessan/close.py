"""決算整理（kessan-close）の枠組み：候補を作る → 承認する → adjustments.csv に登録する。

項目ごとのモジュール（ITEMS の順に呼ぶ）は次の形にする:
- `collect(ctx: CloseContext) -> tuple[list[Candidate], list[Note]]`：候補（1候補＝1伝票）と所見を返す。帳簿には書かない
- `after_post(year_dir, posted_source_ids: list[str]) -> None`（任意）：候補が登録された後に呼ばれる（証憑の状態を変える等）。
  渡されるIDには他の項目の候補も含まれるので、自分の `close:<項目>:` のIDだけを扱う。**何度呼ばれても同じ結果になるように書く**
  （close-post の後に加えて、close-candidates のたびに adjustments.csv に登録済みの `close:` のIDすべてで呼び直し、
  前回の失敗を直すため）。

collect は ITEMS の順に呼ばれ、前の項目が同じ回に出した候補は `ctx.pending` に入っている（消費税の概算に含めるため）。

候補はすべて「要確認」で adjustments-staging.csv に入れ、自動承認しない。取り込み元IDは `close:<項目>:<キー>`。
同じ取り込み元IDが承認済み（staging）か登録済み（adjustments.csv）なら作り直さない。未承認なら最新の内容で置き換え、
もう出なくなった未承認の候補は消す。staging 内の伝票番号は C1, C2…、登録すると adjustments.csv で A1, A2…。
"""
import os
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from pathlib import Path

import yaml

from check import has_ng, run_checks, unresolved_evidence, write_report
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
TAX_PREFIX = "close:消費税:"
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
        rows = []
        for c in self.pending:
            if c.source_id in posted:
                continue
            for line in c.lines:
                row = dict.fromkeys(JOURNAL_COLUMNS, "")
                row.update({k: "" if v is None else str(v) for k, v in line.items()})
                row.update({"伝票番号": c.source_id, "日付": c.date, "取り込み元ID": c.source_id, "_file": STAGING_FILE})
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


def _shape(rows):
    """内容の比較用：日付・科目・補助・金額・税区分・摘要を並べたもの。"""
    def amount(v):
        try:
            return parse_amount(v, "金額")
        except KessanError:
            return v
    return sorted((r["日付"].strip(), r["借方科目"].strip(), r["借方補助"].strip(), str(amount(r["借方金額"])),
                   r["借方税区分"].strip(), r["貸方科目"].strip(), r["貸方補助"].strip(), str(amount(r["貸方金額"])),
                   r["貸方税区分"].strip(), r["摘要"].strip()) for r in rows)


STALE_REASON = "今回の再計算では出ていない（不要なら削除、必要なら承認し直す）"


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
    """staging の既存行と突き合わせる。(新しい staging の行, 状態 {取り込み元ID: 状態}, 所見, 消した数)。"""
    existing = read_rows(Path(year_dir) / STAGING_FILE)
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
                                         f"（不要なら {STAGING_FILE} から削除、必要なら承認し直す）"))
    new_rows, states = list(kept_rows), {}
    for c in candidates:
        sid = c.source_id
        fresh = _candidate_rows(c, "")
        if sid in posted:
            states[sid] = "登録済み"
            if _shape(posted[sid]) != _shape(fresh):
                notes.append(Note(c.item, f"{sid} は登録済みの内容と再計算の結果が違います"
                                          "（adjustments.csv を確かめ、直すなら登録済みの伝票を手で直す）"))
        elif sid in approved_ids:
            states[sid] = "承認済み"
            if _shape(current[sid]) != _shape(fresh):
                notes.append(Note(c.item, f"{sid} は承認済みのため作り直していませんが、再計算の結果と違います"
                                          f"（作り直すなら {STAGING_FILE} の承認欄を空にしてから close-candidates）"))
        else:
            states[sid] = "新規"
            for r in fresh:
                r["伝票番号"] = f"C{next_no}"
            next_no += 1
            new_rows += fresh
    return new_rows, states, notes, removed


# ---- 報告 ----

def _cell(text):
    return str(text or "").replace("|", "｜").replace("\n", " ")


def _account_label(name, sub):
    return f"{name}（{sub}）" if sub else name


def _table_rows(candidate):
    out = []
    for line in candidate.lines:
        debit = (str(line.get("借方科目", "")).strip(), str(line.get("借方補助", "")).strip(),
                 parse_amount(line.get("借方金額", ""), candidate.source_id))
        credit = (str(line.get("貸方科目", "")).strip(), str(line.get("貸方補助", "")).strip(),
                  parse_amount(line.get("貸方金額", ""), candidate.source_id))
        if debit[0] and credit[0] and debit[2] == credit[2]:
            out.append((_account_label(*debit[:2]), _account_label(*credit[:2]), debit[2]))
            continue
        if debit[0]:
            out.append((_account_label(*debit[:2]), "", debit[2]))
        if credit[0]:
            out.append(("", _account_label(*credit[:2]), credit[2]))
    return out


STATE_LABELS = {"新規": "要確認（未承認）", "承認済み": "承認済み（作り直していません）", "登録済み": "登録済み"}


def _write_report(year_dir, candidates, notes, states, removed, now, rejected=()):
    items = list(dict.fromkeys([c.item for c in candidates] + [n.item for n in notes]))
    added = sum(states[c.source_id] == "新規" for c in candidates)
    lines = [f"# 決算整理の候補（{now:%Y-%m-%d %H:%M}）", "",
             f"候補 {len(candidates)}件（要確認 {added}件 / 承認済み・登録済みのため据え置き {len(candidates) - added}件）"
             f" / 所見 {len(notes)}件 / 出なくなったため消した候補 {removed}件", "",
             "内容を確かめて close-approve --ids <取り込み元ID>（全件なら --all）→ close-post で登録します。", ""]
    if not items:
        lines += ["候補・所見はありません。", ""]
    for item in items:
        lines += [f"## {item}", ""]
        for c in (c for c in candidates if c.item == item):
            lines += [f"### {c.source_id}（{STATE_LABELS[states[c.source_id]]}）", "",
                      "| 日付 | 借方 | 貸方 | 金額 | 要確認理由 |", "|---|---|---|---|---|"]
            for i, (debit, credit, amount) in enumerate(_table_rows(c)):
                reason = c.reason if i == 0 else ""
                lines.append(f"| {c.date} | {_cell(debit)} | {_cell(credit)} | {amount:,} | {_cell(reason)} |")
            lines.append("")
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
        failures = _run_after_post(modules, year_dir, posted_ids)
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
        candidates += found
        notes += found_notes
        ctx.pending.extend(found)
    _validate_candidates(candidates, accounts, ctx.period)

    rows, states, extra_notes, removed = _reconcile(year_dir, candidates)
    notes += extra_notes
    ensure_writable(year_dir, [STAGING_FILE])
    try:
        replace_rows(year_dir / STAGING_FILE, STAGING_COLUMNS, rows)
    except OSError:
        raise cannot_write(STAGING_FILE) from None
    report = _write_report(year_dir, candidates, notes, states, removed, now or datetime.now(), dropped)
    added = sum(s == "新規" for s in states.values())
    return CloseResult(candidates_added=added, candidates_kept=len(states) - added, candidates_removed=removed,
                       notes=notes, report_path=report, candidates_rejected=len(dropped))


def candidate_ids(year_dir):
    """adjustments-staging.csv にある候補の取り込み元ID（順序どおり、重複なし）。"""
    return list(dict.fromkeys(r["取り込み元ID"].strip() for r in read_rows(Path(year_dir) / STAGING_FILE)
                              if r["取り込み元ID"].strip()))


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
    failures += _run_after_post(ITEMS if items is None else items, year_dir, list(dict.fromkeys(posted_ids)))
    if failures:
        raise KessanError(f"決算整理仕訳の登録は完了済み（{', '.join(numbers)}）ですが、登録後の処理で止まったものがあります"
                          "（次の close-candidates で後処理をやり直します）:\n" + "\n".join(failures))
    return numbers


def _run_after_post(modules, year_dir, posted_ids):
    """各項目の after_post を呼ぶ。1つが失敗しても残りは呼び、失敗のメッセージの一覧を返す。"""
    failures = []
    for module in modules:
        hook = getattr(module, "after_post", None)
        if hook is None:
            continue
        try:
            hook(year_dir, posted_ids)
        except Exception as e:  # noqa: BLE001  登録は確定済みなので、どの失敗でも他の後処理を止めない
            failures.append(f"{getattr(module, '__name__', type(module).__name__)}: {e}")
    return failures


# ---- 上書き額（達人で計算した額） ----

def _check_amount(item, amount, where):
    if item not in OVERRIDE_ITEMS:
        raise KessanError(f"{where}: 項目は {'／'.join(OVERRIDE_ITEMS)} のどちらかにしてください（現在: {item}）")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
        raise KessanError(f"{where}: {item}の金額は0以上の整数にしてください（現在: {amount!r}）")


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


def set_amount(year_dir, item, amount):
    """達人で計算した額を close-overrides.yaml に保存する（次の close-candidates で候補に使う）。"""
    _check_amount(item, amount, "close-set-amount")
    load_overrides(year_dir)
    load_rejected(year_dir)
    data = _load_raw(year_dir)
    data[item] = amount
    _save_raw(year_dir, data)


def reject_candidates(year_dir, source_ids, reason):
    """候補を却下する：以後の close-candidates はその取り込み元IDの候補を出さない（close-report.md に理由つきで載せる）。

    staging にある行は消す。登録済み（adjustments.csv）のIDは却下できない（登録済みの伝票は手で直す）。
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
        raise KessanError("登録済みの候補は却下できません（登録済みの伝票を手で直す）: " + "、".join(done))
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


# 項目の順：未払・未収 → 前払 → 減価償却 → 役員報酬・残高の所見（候補なし） → 消費税・法人税等
import close_payable  # noqa: E402  （項目は close の型を使うので、型を定義した後で読む）
import close_prepaid  # noqa: E402
import close_depreciation  # noqa: E402
import close_review  # noqa: E402
import close_tax  # noqa: E402

ITEMS = [close_payable, close_prepaid, close_depreciation, close_review, close_tax]
