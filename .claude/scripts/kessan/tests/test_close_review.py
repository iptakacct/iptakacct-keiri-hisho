"""決算整理：役員報酬・残高の所見。期間 2025-04-01〜2026-03-31（架空のデータ）。"""
import close
import close_review
import close_tax
from common import JOURNAL_COLUMNS, read_rows, write_rows
from helpers import line

BANK = ("普通預金", "サンプル銀行")


def books(year_dir, rows=(), opening=()):
    """期首は普通預金 2,000,000。opening は負債の期首残高 (科目, 補助, 残高) で、差額を資本金にして貸借を合わせる。"""
    liabilities = sum(x[2] for x in opening)
    ob = [("普通預金", "サンプル銀行", 2000000), *opening, ("資本金", "", 2000000 - liabilities)]
    write_rows(year_dir / "opening-balances.csv", ["科目", "補助", "残高"],
               [{"科目": n, "補助": s, "残高": str(a)} for n, s, a in ob])
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, list(rows))


def run(year_dir, accounts):
    return close.run_close(year_dir, accounts, items=[close_review])


def messages(result, item=None):
    return [n.message for n in result.notes if item is None or n.item == item]


def pay(no, date, amount):
    return line(no, date, debit=("役員報酬", "", amount), credit=BANK + (amount,), 摘要="役員報酬")


def test_director_pay_not_fixed(year_dir, accounts):
    books(year_dir, [pay("1", "2025-04-25", 100000), pay("2", "2025-05-25", 100000), pay("3", "2025-06-25", 120000)])
    r = run(year_dir, accounts)
    assert messages(r, "役員報酬") == [
        "役員報酬の月額が一定でない（2025-04: 100,000円、2025-05: 100,000円、2025-06: 120,000円）。期首から3か月以内の改定か確認"]
    assert read_rows(year_dir / "adjustments-staging.csv") == []  # 候補は作らない


def test_director_pay_fixed_or_split_in_a_month(year_dir, accounts):
    books(year_dir, [pay("1", "2025-04-25", 100000), pay("2", "2025-05-10", 50000), pay("3", "2025-05-25", 50000),
                     pay("4", "2025-06-25", 100000)])
    assert messages(run(year_dir, accounts), "役員報酬") == []  # 同じ月の2回分は合計して比べる


def test_suspense_remaining(year_dir, accounts):
    books(year_dir, [
        line("1", "2025-08-01", debit=("仮払金", "田中", 30000), credit=("普通預金", "サンプル銀行", 30000)),
        line("2", "2025-09-01", debit=("仮払金", "田中", 5000), credit=("普通預金", "サンプル銀行", 5000)),
        line("3", "2025-09-02", debit=("仮払金", "", 8000), credit=("普通預金", "サンプル銀行", 8000)),
        line("4", "2025-10-01", debit=("消耗品費", "", 8000), credit=("仮払金", "", 8000)),  # 精算済み
    ])
    assert messages(run(year_dir, accounts), "残高") == ["仮払金が残っている（田中: 35,000円）"]


def test_director_loan_change(year_dir, accounts):
    books(year_dir, [line("1", "2025-12-01", debit=("普通預金", "サンプル銀行", 1000000), credit=("役員借入金", "", 1000000))],
          opening=[("役員借入金", "", 500000)])
    assert messages(run(year_dir, accounts), "残高") == ["役員借入金が 500,000円 → 1,500,000円"]


def test_director_loan_small_change_is_quiet(year_dir, accounts):
    books(year_dir, [line("1", "2025-12-01", debit=("普通預金", "サンプル銀行", 999999), credit=("役員借入金", "", 999999))])
    assert messages(run(year_dir, accounts), "残高") == []


def test_director_lending_interest_note(year_dir, accounts):
    books(year_dir, [line("1", "2025-08-01", debit=("役員貸付金", "", 200000), credit=("普通預金", "サンプル銀行", 200000))])
    assert messages(run(year_dir, accounts), "残高") == [
        "役員貸付金があるので認定利息の要否を確認（monthly-closing-checklist.md 項目15）"]


def test_director_lending_large_change(year_dir, accounts):
    books(year_dir, [line("1", "2025-08-01", debit=("役員貸付金", "", 1000000), credit=("普通預金", "サンプル銀行", 1000000))])
    assert messages(run(year_dir, accounts), "残高") == [
        "役員貸付金が 0円 → 1,000,000円",
        "役員貸付金があるので認定利息の要否を確認（monthly-closing-checklist.md 項目15）"]


def test_review_runs_before_tax_in_default_items():
    assert close.ITEMS.index(close_review) < close.ITEMS.index(close_tax)
    assert close.ITEMS.index(close_review) > close.ITEMS.index(close.close_depreciation)
