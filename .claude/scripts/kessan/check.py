"""帳簿の検算。NGが1件でもあれば、決算の工程（kessan-close）に進まない。"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from common import parse_amount, read_rows
from evidence import evidence_by_source, read_evidence
from tax import (
    ALLOCATION_VALUES, FIXED_ASSET_SECTIONS, INVOICE_VALUES, TAX_CATEGORIES, load_tax_settings, load_transitions,
    transition_label_error,
)
from ledger import compute_balances, entry_rows, entry_where, load_opening

BS_CATEGORIES = ("資産", "負債", "純資産")

# 科目の区分ごとに付けてよい税区分の種類（空は別に判定する）
_PURCHASE_KINDS = ("課税仕入", "課税仕入返還")
_ALLOWED_KINDS = {
    "収益": ("課税売上", "免税売上", "非課税売上", "非課税資産輸出", "課税売上返還", "貸倒回収", "対象外"),
    "費用": ("課税仕入", "課税仕入返還", "非課税仕入", "貸倒", "対象外"),
}
_ALLOWED_KINDS.update({c: ("課税仕入", "課税仕入返還", "非課税仕入", "課税売上", "非課税売上", "対象外")
                       for c in BS_CATEGORIES})


@dataclass(frozen=True)
class Finding:
    level: str
    item: str
    message: str


def _sub_label(sub):
    return sub or "補助なし"


def _voucher_label(file, no):
    return f"伝票{no}" if file == "journal.csv" else f"{file} 伝票{no}"


def check_voucher_balance(entries):
    totals = defaultdict(lambda: [0, 0])
    for r in entries:
        key = (r.get("_file", "journal.csv"), r["伝票番号"])
        totals[key][0] += parse_amount(r["借方金額"], entry_where(r))
        totals[key][1] += parse_amount(r["貸方金額"], entry_where(r))
    return [
        Finding("NG", "貸借一致", f"{_voucher_label(file, no)}: 借方{debit}≠貸方{credit}")
        for (file, no), (debit, credit) in totals.items() if debit != credit
    ]


def check_voucher_numbers(entries):
    """journal.csv は数字の連番、adjustments.csv は A1, A2… とし、両方で同じ番号を使わない。"""
    numbers = defaultdict(list)
    for r in entries:
        no = r["伝票番号"].strip()
        file = r.get("_file", "journal.csv")
        if no not in numbers[file]:
            numbers[file].append(no)
    findings = [
        Finding("NG", "伝票番号", f"journal.csv: 伝票番号「{no}」が数字ではない")
        for no in numbers["journal.csv"] if not no.isdigit()
    ]
    findings += [
        Finding("NG", "伝票番号", f"adjustments.csv: 伝票番号「{no}」がAで始まっていない（決算整理仕訳は A1, A2…）")
        for no in numbers["adjustments.csv"] if not no.startswith("A")
    ]
    findings += [
        Finding("NG", "伝票番号", f"伝票番号「{no}」が journal.csv と adjustments.csv の両方にある")
        for no in numbers["adjustments.csv"] if no in numbers["journal.csv"]
    ]
    return findings


def check_accounts_exist(year_dir, entries, accounts):
    findings = []
    for r in entries:
        for side in ("借方", "貸方"):
            name = r[f"{side}科目"].strip()
            if (name or parse_amount(r[f"{side}金額"], entry_where(r))) and name not in accounts:
                findings.append(Finding("NG", "科目マスタ", f"伝票{r['伝票番号']}: {side}科目「{name}」が科目マスタに無い"))
    for r in read_rows(Path(year_dir) / "opening-balances.csv"):
        if r["科目"].strip() not in accounts:
            findings.append(Finding("NG", "科目マスタ", f"期首残高: 科目「{r['科目']}」が科目マスタに無い"))
    return findings


def check_statement_balances(year_dir, accounts):
    last = {}
    for r in read_rows(Path(year_dir) / "statement-balances.csv"):
        last[(r["科目"], r["補助"], r["日付"])] = parse_amount(
            r["残高"], f"statement-balances.csv {r['科目']}（{_sub_label(r['補助'])}） {r['日付']}")
    mismatches = defaultdict(list)
    for (name, sub, date), expected in sorted(last.items(), key=lambda kv: kv[0][2]):
        book = compute_balances(year_dir, accounts, until=date).get((name, sub), {}).get("期末残高", 0)
        if book != expected:
            mismatches[(name, sub)].append((date, book, expected))
    findings = []
    for (name, sub), items in mismatches.items():
        date, book, expected = items[0]
        findings.append(Finding(
            "NG", "口座残高",
            f"{name}（{_sub_label(sub)}）: {date}時点で帳簿{book}≠明細{expected}"
            f"（不一致{len(items)}日。未登録の明細行・登録誤りを確認）",
        ))
    return findings


def check_duplicates(entries):
    by_id = defaultdict(set)
    by_key = defaultdict(set)
    for r in entries:
        if r["取り込み元ID"]:
            by_id[r["取り込み元ID"]].add(r["伝票番号"])
        who = r["取引先"].strip() or r["摘要"].strip()
        amount = parse_amount(r["借方金額"], entry_where(r)) or parse_amount(r["貸方金額"], entry_where(r))
        if who and amount:
            by_key[(r["日付"], amount, who)].add(r["伝票番号"])
    findings = [
        Finding("NG", "重複", f"取り込み元ID {source_id} が伝票{'・'.join(sorted(nos))}に重複")
        for source_id, nos in by_id.items() if len(nos) > 1
    ]
    findings += [
        Finding("WARN", "重複候補", f"{date} {amount}円 {who}: 伝票{'・'.join(sorted(nos))}")
        for (date, amount, who), nos in by_key.items() if len(nos) > 1
    ]
    return findings


def check_negative_balances(year_dir, accounts):
    return [
        Finding("NG", "マイナス残高", f"{name}（{_sub_label(sub)}）: 期末残高{v['期末残高']}")
        for (name, sub), v in compute_balances(year_dir, accounts).items()
        if accounts[name].category in ("資産", "負債") and v["期末残高"] < 0
    ]


def check_opening(year_dir, accounts, prev_year_dir=None):
    opening = load_opening(year_dir)
    if not opening and prev_year_dir is None:
        return [Finding("WARN", "期首残高",
                        "期首残高が未登録（設立初年度でなければ opening-balances.csv に前期決算書の期末残高を入れる）")]
    findings = []
    diff = sum(
        amount if accounts[name].normal_side == "借" else -amount
        for (name, _), amount in opening.items() if name in accounts
    )
    if diff:
        findings.append(Finding("NG", "期首残高", f"期首残高の貸借が{abs(diff)}円ずれている"))

    if prev_year_dir is not None:
        prev = compute_balances(prev_year_dir, accounts)
        expected = {k: v["期末残高"] for k, v in prev.items() if accounts[k[0]].category in BS_CATEGORIES}
        profit = (sum(v["期末残高"] for k, v in prev.items() if accounts[k[0]].category == "収益")
                  - sum(v["期末残高"] for k, v in prev.items() if accounts[k[0]].category == "費用"))
        retained = ("繰越利益剰余金", "")
        expected[retained] = expected.get(retained, 0) + profit
        bs_opening_keys = {k for k in opening if k[0] in accounts and accounts[k[0]].category in BS_CATEGORIES}
        for key in sorted(set(expected) | bs_opening_keys):
            actual, want = opening.get(key, 0), expected.get(key, 0)
            if actual != want:
                findings.append(Finding("NG", "期首残高",
                                        f"{key[0]}（{_sub_label(key[1])}）: 期首{actual}≠前期末{want}"))
    return findings


def _bs_tax_warning(label, side, name, tax, kind, account):
    """資産・負債・純資産の科目に付いた税区分の注意。固定資産の取得（借方）と売却（貸方の課税売上）以外は確かめてもらう。"""
    if account is None or account.category not in BS_CATEGORIES:
        return None
    if kind in _PURCHASE_KINDS and side == "貸方":
        return Finding("WARN", "税区分", f"{label}: 貸方 {name} に税区分「{tax}」（資産の売却は売却価額を課税売上で計上する）")
    if account.section not in FIXED_ASSET_SECTIONS and kind != "対象外":
        return Finding("WARN", "税区分", f"{label}: {side} {name}（固定資産以外の資産・負債・純資産）に税区分「{tax}」")
    return None


def _registration_findings(row, label, ev):
    """1行の課税仕入の側について、インボイス区分と証憑の登録番号の食い違いを返す。"""
    has_registration = bool(ev["登録番号"].strip())
    findings = []
    for side in ("借方", "貸方"):
        category = TAX_CATEGORIES.get(row.get(f"{side}税区分", "").strip())
        if category is None or category.kind not in _PURCHASE_KINDS:
            continue
        invoice = row.get(f"{side}インボイス", "").strip()
        if invoice == "適格" and not has_registration:
            findings.append(Finding("NG", "税区分", f"{label}: {side} インボイスが適格だが証憑（{ev['証憑ファイル']}）に"
                                                   "登録番号が無い"))
        elif has_registration and (invoice.startswith("経過") or invoice == "なし"):
            findings.append(Finding("WARN", "税区分", f"{label}: {side} インボイスが「{invoice}」だが証憑（{ev['証憑ファイル']}）に"
                                                      "登録番号がある（適格ではないか確認）"))
    return findings


def _check_evidence_rates(entries, evidence, settings):
    """証憑の税率（8／10／混在）と帳簿の税区分の税率が合っているか。証憑が1つに決まった行だけを見る。

    原則課税のときは、課税仕入の側のインボイスと証憑の登録番号の食い違い（適格なのに番号が無い・番号があるのに適格でない）も見る。
    """
    by_source = evidence_by_source(evidence)
    rated = defaultdict(int)  # 取り込み元ID -> 税率を持つ側の数
    first_label = {}
    findings = []
    for r in entries:
        source_id = r["取り込み元ID"].strip()
        ev = by_source.get(source_id)
        if ev is None:
            continue
        label = _voucher_label(r.get("_file", "journal.csv"), r["伝票番号"])
        if settings.method == "原則":
            findings += _registration_findings(r, label, ev)
        if not ev["税率"].strip():
            continue
        first_label.setdefault(source_id, label)
        rate = ev["税率"].strip()
        for side in ("借方", "貸方"):
            category = TAX_CATEGORIES.get(r.get(f"{side}税区分", "").strip())
            if category is None or category.rate is None:
                continue
            rated[source_id] += 1
            if rate in ("8", "10") and category.rate != int(rate):
                findings.append(Finding("NG", "税区分", f"{label}: 証憑（{ev['証憑ファイル']}）の税率は{rate}%なのに"
                                                       f"{side}税区分が「{category.name}」"))
    for source_id, label in first_label.items():
        ev = by_source[source_id]
        if ev["税率"].strip() == "混在" and rated[source_id] < 2:
            findings.append(Finding("NG", "税区分", f"{label}: 証憑（{ev['証憑ファイル']}）の税率が混在なのに"
                                                   "税率ごとに行を分けていない（登録前なら split-tax で分ける）"))
    return findings


def check_tax(entries, accounts, settings, evidence=(), transitions=None):
    """課税の年度だけ、各行・各側の税区分・インボイス・個別対応を検算する。免税の年度は何も返さない。

    evidence（evidence.csv の行）があれば証憑の税率と税区分の税率を突き合わせる。インボイスは原則課税のときだけ必須。
    """
    if not settings.taxable:
        return []
    if transitions is None:
        transitions = load_transitions()
    invoice_required = settings.method == "原則"
    findings = []
    for r in entries:
        label = _voucher_label(r.get("_file", "journal.csv"), r["伝票番号"])
        for side in ("借方", "貸方"):
            name = r[f"{side}科目"].strip()
            if not (name or parse_amount(r[f"{side}金額"], entry_where(r))):
                continue
            tax = r.get(f"{side}税区分", "").strip()
            invoice = r.get(f"{side}インボイス", "").strip()
            allocation = r.get(f"{side}個別対応", "").strip()
            account = accounts.get(name)
            if tax and tax not in TAX_CATEGORIES:
                findings.append(Finding("NG", "税区分", f"{label}: {side}税区分「{tax}」は使えない値"))
                continue
            if not tax:
                if account and account.category in ("収益", "費用") and parse_amount(r[f"{side}金額"], entry_where(r)):
                    findings.append(Finding("NG", "税区分", f"{label}: {side} {name} の税区分が空"))
                continue
            kind = TAX_CATEGORIES[tax].kind
            if account and kind not in _ALLOWED_KINDS.get(account.category, ()):
                findings.append(Finding("NG", "税区分", f"{label}: {side} {name} に税区分「{tax}」は使えない"))
                continue
            warning = _bs_tax_warning(label, side, name, tax, kind, account)
            if warning:
                findings.append(warning)
            if kind in _PURCHASE_KINDS:
                if not invoice:
                    if invoice_required:
                        findings.append(Finding("NG", "税区分", f"{label}: {side} インボイスが空（課税仕入には必須）"))
                elif invoice not in INVOICE_VALUES:
                    findings.append(Finding("NG", "税区分", f"{label}: {side} インボイス「{invoice}」は使えない値"))
                else:
                    message = transition_label_error(invoice, r["日付"].strip(), transitions)
                    if message:
                        findings.append(Finding("WARN", "税区分", f"{label}: {side} インボイス「{invoice}」: {message}"))
                if settings.allocation == "個別対応":
                    if not allocation:
                        findings.append(Finding("NG", "税区分", f"{label}: {side} 個別対応が空（個別対応方式の課税仕入には必須）"))
                    elif allocation not in ALLOCATION_VALUES:
                        findings.append(Finding("NG", "税区分", f"{label}: {side} 個別対応「{allocation}」は使えない値"))
            else:
                for title, value in (("インボイス", invoice), ("個別対応", allocation)):
                    if value:
                        findings.append(Finding("WARN", "税区分", f"{label}: {side} {title}「{value}」は課税仕入以外には付けない"))
    return findings + _check_evidence_rates(entries, evidence, settings)


def run_checks(year_dir, accounts, prev_year_dir=None):
    entries = entry_rows(year_dir)
    return (
        check_voucher_balance(entries)
        + check_voucher_numbers(entries)
        + check_accounts_exist(year_dir, entries, accounts)
        + check_statement_balances(year_dir, accounts)
        + check_duplicates(entries)
        + check_negative_balances(year_dir, accounts)
        + check_opening(year_dir, accounts, prev_year_dir)
        + check_tax(entries, accounts, load_tax_settings(year_dir), read_evidence(year_dir))
    )


def has_ng(findings):
    return any(f.level == "NG" for f in findings)


def write_report(year_dir, findings, now=None):
    now = now or datetime.now()
    ng = [f for f in findings if f.level == "NG"]
    warn = [f for f in findings if f.level == "WARN"]
    lines = [f"# 検算結果（{now:%Y-%m-%d %H:%M}）", "", f"NG: {len(ng)}件 / 警告: {len(warn)}件", ""]
    for title, group in (("NG", ng), ("警告", warn)):
        if group:
            lines += [f"## {title}", ""] + [f"- [{f.item}] {f.message}" for f in group] + [""]
    path = Path(year_dir) / "output" / "check-result.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
