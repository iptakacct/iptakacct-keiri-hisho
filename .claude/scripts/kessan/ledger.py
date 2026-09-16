"""期首残高と仕訳（journal.csv・adjustments.csv）から、科目・補助科目別の残高を集計する。"""
from collections import defaultdict
from pathlib import Path

from accounts import normal_delta
from common import parse_amount, read_rows


ENTRY_FILES = ("journal.csv", "adjustments.csv")


def entry_rows(year_dir):
    """仕訳の全行。どのファイルの行かを `_file` に入れる（CSVに書き出す列ではない）。"""
    year_dir = Path(year_dir)
    return [{**r, "_file": name} for name in ENTRY_FILES for r in read_rows(year_dir / name)]


def entry_where(r):
    return f"{r.get('_file', 'journal.csv')} 伝票{r['伝票番号']}"


def load_opening(year_dir):
    opening = defaultdict(int)
    for r in read_rows(Path(year_dir) / "opening-balances.csv"):
        opening[(r["科目"].strip(), r["補助"].strip())] += parse_amount(r["残高"], f"opening-balances.csv {r['科目']}")
    return dict(opening)


def compute_balances(year_dir, accounts, until=None):
    totals = defaultdict(lambda: {"期首残高": 0, "借方発生": 0, "貸方発生": 0})
    for key, amount in load_opening(year_dir).items():
        totals[key]["期首残高"] += amount
    for r in entry_rows(year_dir):
        if until and r["日付"] > until:
            continue
        for side, column in (("借方", "借方発生"), ("貸方", "貸方発生")):
            name = r[f"{side}科目"].strip()
            if name:
                totals[(name, r[f"{side}補助"].strip())][column] += parse_amount(r[f"{side}金額"], entry_where(r))

    balances = {}
    for key, v in totals.items():
        account = accounts.get(key[0])
        if account is None:
            continue
        closing = v["期首残高"] + normal_delta(account, v["借方発生"], v["貸方発生"])
        balances[key] = {**v, "期末残高": closing}
    return balances
