"""勘定科目マスタ。科目の区分と、残高が正常に積み上がる側（借/貸）を持つ。"""
from dataclasses import dataclass
from pathlib import Path

from common import KessanError, read_rows

MASTER_PATH = Path(__file__).with_name("accounts-master.csv")
CATEGORIES = ("資産", "負債", "純資産", "収益", "費用")


@dataclass(frozen=True)
class Account:
    name: str
    category: str
    normal_side: str
    section: str


def load_accounts(path=MASTER_PATH):
    accounts = {}
    for r in read_rows(path):
        acc = Account(r["科目"].strip(), r["区分"].strip(), r["正常残高"].strip(), r["表示区分"].strip())
        if acc.category not in CATEGORIES or acc.normal_side not in ("借", "貸"):
            raise KessanError(f"科目マスタの値が不正です: {r}")
        accounts[acc.name] = acc
    return accounts


def normal_delta(account, debit, credit):
    return debit - credit if account.normal_side == "借" else credit - debit
