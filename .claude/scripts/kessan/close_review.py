"""決算整理の項目：役員報酬・残高の所見（候補は作らず、確かめてほしいことだけを返す）。

- 役員報酬：当期の借方を月ごとに合計し、額が2種類以上あれば所見（定期同額給与から外れていないか）
- 仮払金：期末残高が残っていれば所見（精算漏れ・科目の振替漏れ）
- 役員借入金・役員貸付金：期首と期末の残高が100万円以上動いていれば所見
- 役員貸付金：期末残高があれば、認定利息の要否の所見（monthly-closing-checklist.md 項目15）
"""
from collections import defaultdict

import close
from common import parse_amount

ITEM_PAY = "役員報酬"
ITEM_BALANCE = "残高"
PAY_ACCOUNT = "役員報酬"
SUSPENSE = "仮払金"
LOAN_IN = "役員借入金"
LOAN_OUT = "役員貸付金"
LARGE_MOVE = 1000000


def _yen(n):
    return f"{n:,}円"


def _director_pay(ctx):
    monthly = defaultdict(int)
    for r in ctx.entries:
        if r["借方科目"].strip() == PAY_ACCOUNT and r["借方金額"].strip():
            amount = parse_amount(r["借方金額"], f"{r.get('_file', 'journal.csv')} 伝票{r['伝票番号']}")
            monthly[r["日付"].strip()[:7]] += amount
    if len(set(monthly.values())) < 2:
        return []
    detail = "、".join(f"{month}: {_yen(amount)}" for month, amount in sorted(monthly.items()))
    return [close.Note(ITEM_PAY, f"役員報酬の月額が一定でない（{detail}）。期首から3か月以内の改定か確認")]


def _suspense(ctx):
    remaining = sorted((sub, v["期末残高"]) for (name, sub), v in ctx.balances.items()
                       if name == SUSPENSE and v["期末残高"])
    if not remaining:
        return []
    detail = "、".join(f"{sub or '補助なし'}: {_yen(amount)}" for sub, amount in remaining)
    return [close.Note(ITEM_BALANCE, f"仮払金が残っている（{detail}）")]


def _director_loans(ctx):
    notes = []
    for name in (LOAN_IN, LOAN_OUT):
        opening = sum(amount for (n, _), amount in ctx.opening.items() if n == name)
        closing = ctx.balance(name)
        if abs(closing - opening) >= LARGE_MOVE:
            notes.append(close.Note(ITEM_BALANCE, f"{name}が {_yen(opening)} → {_yen(closing)}"))
    if ctx.balance(LOAN_OUT):
        notes.append(close.Note(ITEM_BALANCE, "役員貸付金があるので認定利息の要否を確認（monthly-closing-checklist.md 項目15）"))
    return notes


def collect(ctx):
    return [], _director_pay(ctx) + _suspense(ctx) + _director_loans(ctx)
