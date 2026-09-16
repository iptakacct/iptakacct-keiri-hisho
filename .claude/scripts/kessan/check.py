"""帳簿の検算。NGが1件でもあれば、決算の工程（kessan-close）に進まない。"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from common import parse_amount, read_rows
from ledger import compute_balances, entry_rows, entry_where, load_opening

BS_CATEGORIES = ("資産", "負債", "純資産")


@dataclass(frozen=True)
class Finding:
    level: str
    item: str
    message: str


def _sub_label(sub):
    return sub or "補助なし"


def check_voucher_balance(entries):
    totals = defaultdict(lambda: [0, 0])
    for r in entries:
        totals[r["伝票番号"]][0] += parse_amount(r["借方金額"], entry_where(r))
        totals[r["伝票番号"]][1] += parse_amount(r["貸方金額"], entry_where(r))
    return [
        Finding("NG", "貸借一致", f"伝票{no}: 借方{debit}≠貸方{credit}")
        for no, (debit, credit) in totals.items() if debit != credit
    ]


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


def run_checks(year_dir, accounts, prev_year_dir=None):
    entries = entry_rows(year_dir)
    return (
        check_voucher_balance(entries)
        + check_accounts_exist(year_dir, entries, accounts)
        + check_statement_balances(year_dir, accounts)
        + check_duplicates(entries)
        + check_negative_balances(year_dir, accounts)
        + check_opening(year_dir, accounts, prev_year_dir)
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
