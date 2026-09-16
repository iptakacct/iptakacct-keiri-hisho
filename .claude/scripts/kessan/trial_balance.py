"""試算表（科目・補助科目別の期首残高・発生額・期末残高）を output/trial-balance.csv に出す。"""
from pathlib import Path

from common import write_rows
from ledger import compute_balances

TB_COLUMNS = ["科目", "補助", "区分", "期首残高", "借方発生", "貸方発生", "期末残高"]


def build_trial_balance(year_dir, accounts):
    order = {name: i for i, name in enumerate(accounts)}
    balances = compute_balances(year_dir, accounts)
    rows = []
    for (name, sub), v in sorted(balances.items(), key=lambda kv: (order[kv[0][0]], kv[0][1])):
        rows.append({
            "科目": name, "補助": sub, "区分": accounts[name].category,
            **{k: str(v[k]) for k in ("期首残高", "借方発生", "貸方発生", "期末残高")},
        })
    return rows


def write_trial_balance(year_dir, accounts):
    path = Path(year_dir) / "output" / "trial-balance.csv"
    write_rows(path, TB_COLUMNS, build_trial_balance(year_dir, accounts))
    return path
