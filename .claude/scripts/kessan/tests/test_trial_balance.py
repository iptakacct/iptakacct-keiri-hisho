from common import JOURNAL_COLUMNS, OPENING_COLUMNS, read_rows, write_rows
from helpers import line
from ledger import compute_balances
from trial_balance import write_trial_balance

BANK = ("普通預金", "サンプル銀行")


def setup_books(year_dir):
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"},
        {"科目": "資本金", "補助": "", "残高": "1000000"},
    ])
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, [
        line("1", "2025-04-01", debit=(*BANK, 100000), credit=("売上高", "", 100000)),
        line("2", "2025-04-05", debit=("支払手数料", "", 3300), credit=(*BANK, 3300)),
    ])
    write_rows(year_dir / "adjustments.csv", JOURNAL_COLUMNS, [
        line("A1", "2026-03-31", debit=("法人税等", "", 70000), credit=("未払法人税等", "", 70000)),
    ])


def test_compute_balances(year_dir, accounts):
    setup_books(year_dir)
    balances = compute_balances(year_dir, accounts)
    assert balances[BANK] == {"期首残高": 1000000, "借方発生": 100000, "貸方発生": 3300, "期末残高": 1096700}
    assert balances[("売上高", "")]["期末残高"] == 100000
    assert balances[("未払法人税等", "")]["期末残高"] == 70000


def test_compute_balances_until_date(year_dir, accounts):
    setup_books(year_dir)
    assert compute_balances(year_dir, accounts, until="2025-04-01")[BANK]["期末残高"] == 1100000
    assert compute_balances(year_dir, accounts, until="2025-03-31")[BANK]["期末残高"] == 1000000


def test_unknown_account_is_excluded(year_dir, accounts):
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS,
               [line("1", "2025-04-01", debit=("謎の科目", "", 1), credit=("資本金", "", 1))])
    balances = compute_balances(year_dir, accounts)
    assert ("謎の科目", "") not in balances
    assert balances[("資本金", "")]["期末残高"] == 1


def test_write_trial_balance(year_dir, accounts):
    setup_books(year_dir)
    path = write_trial_balance(year_dir, accounts)
    assert path == year_dir / "output" / "trial-balance.csv"
    rows = read_rows(path)
    assert [r["科目"] for r in rows] == ["普通預金", "未払法人税等", "資本金", "売上高", "支払手数料", "法人税等"]
    assert rows[0] == {"科目": "普通預金", "補助": "サンプル銀行", "区分": "資産",
                       "期首残高": "1000000", "借方発生": "100000", "貸方発生": "3300", "期末残高": "1096700"}
