from datetime import datetime

from check import has_ng, run_checks, write_report
from common import (
    JOURNAL_COLUMNS, OPENING_COLUMNS, STATEMENT_BALANCE_COLUMNS,
    init_year_dir, read_rows, write_rows,
)
from helpers import line

BANK = ("普通預金", "サンプル銀行")


def setup_clean(year_dir, extra_lines=(), balances=(("2025-04-01", "1100000"), ("2025-04-05", "1096700"))):
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"},
        {"科目": "資本金", "補助": "", "残高": "1000000"},
    ])
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, [
        line("1", "2025-04-01", debit=(*BANK, 100000), credit=("売上高", "", 100000), 取り込み元ID="bank:1"),
        line("2", "2025-04-05", debit=("支払手数料", "", 3300), credit=(*BANK, 3300), 取り込み元ID="bank:2"),
        *extra_lines,
    ])
    write_rows(year_dir / "statement-balances.csv", STATEMENT_BALANCE_COLUMNS, [
        {"科目": BANK[0], "補助": BANK[1], "日付": d, "残高": b, "ファイル名": "2025-04.csv"} for d, b in balances
    ])


def ng_items(findings):
    return [f.item for f in findings if f.level == "NG"]


def messages(findings, item):
    return [f.message for f in findings if f.item == item]


def test_clean_books_have_no_findings(year_dir, accounts):
    setup_clean(year_dir)
    assert run_checks(year_dir, accounts) == []


def test_unbalanced_voucher(year_dir, accounts):
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", debit=("支払手数料", "", 500))])
    findings = run_checks(year_dir, accounts)
    assert "貸借一致" in ng_items(findings)
    assert messages(findings, "貸借一致") == ["伝票3: 借方500≠貸方0"]


def test_unknown_account(year_dir, accounts):
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", debit=("謎の科目", "", 500), credit=("現金", "", 500))])
    assert "科目マスタ" in ng_items(run_checks(year_dir, accounts))


def test_statement_balance_mismatch(year_dir, accounts):
    setup_clean(year_dir, balances=(("2025-04-01", "1100000"), ("2025-04-05", "1096000")))
    findings = run_checks(year_dir, accounts)
    assert ng_items(findings) == ["口座残高"]
    (message,) = messages(findings, "口座残高")
    assert "2025-04-05" in message and "帳簿1096700" in message and "明細1096000" in message


def test_last_balance_of_the_day_is_used(year_dir, accounts):
    setup_clean(year_dir, balances=(("2025-04-05", "1100000"), ("2025-04-05", "1096700")))
    assert run_checks(year_dir, accounts) == []


def test_duplicate_source_id(year_dir, accounts):
    dup = dict(debit=("雑費", "", 100), credit=(*BANK, 100), 取り込み元ID="bank:z")
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", **dup), line("4", "2025-04-10", **dup)])
    findings = run_checks(year_dir, accounts)
    assert "重複" in ng_items(findings)


def test_duplicate_candidate_is_warning(year_dir, accounts):
    same = dict(debit=("支払手数料", "", 770), credit=(*BANK, 770), 取引先="テスト商事")
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", **same), line("4", "2025-04-10", **same)])
    findings = run_checks(year_dir, accounts)
    assert ng_items(findings) == []
    assert [f.item for f in findings] == ["重複候補"]


def test_negative_asset_balance(year_dir, accounts):
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", debit=("雑費", "", 5000), credit=("現金", "", 5000))])
    findings = run_checks(year_dir, accounts)
    assert ng_items(findings) == ["マイナス残高"]
    assert messages(findings, "マイナス残高") == ["現金（補助なし）: 期末残高-5000"]


def test_deficit_in_retained_earnings_is_not_flagged(year_dir, accounts):
    setup_clean(year_dir)
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"},
        {"科目": "資本金", "補助": "", "残高": "1200000"},
        {"科目": "繰越利益剰余金", "補助": "", "残高": "-200000"},
    ])
    assert run_checks(year_dir, accounts) == []


def test_unbalanced_opening(year_dir, accounts):
    setup_clean(year_dir)
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS,
               [{"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"}])
    findings = run_checks(year_dir, accounts)
    assert messages(findings, "期首残高") == ["期首残高の貸借が1000000円ずれている"]


def test_missing_opening_is_warning(year_dir, accounts):
    assert [(f.level, f.item) for f in run_checks(year_dir, accounts)] == [("WARN", "期首残高")]


def setup_prev(tmp_path):
    prev = tmp_path / "2025-03期"
    init_year_dir(prev)
    write_rows(prev / "opening-balances.csv", OPENING_COLUMNS, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "500000"},
        {"科目": "資本金", "補助": "", "残高": "500000"},
    ])
    write_rows(prev / "journal.csv", JOURNAL_COLUMNS, [
        line("1", "2024-06-01", debit=(*BANK, 200000), credit=("売上高", "", 200000)),
        line("2", "2024-07-01", debit=("雑費", "", 50000), credit=(*BANK, 50000)),
    ])
    return prev


def write_current_opening(year_dir, retained):
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "650000"},
        {"科目": "資本金", "補助": "", "残高": "500000"},
        {"科目": "繰越利益剰余金", "補助": "", "残高": str(retained)},
    ])


def test_opening_matches_previous_year(year_dir, accounts, tmp_path):
    prev = setup_prev(tmp_path)
    write_current_opening(year_dir, 150000)
    assert run_checks(year_dir, accounts, prev_year_dir=prev) == []


def test_opening_differs_from_previous_year(year_dir, accounts, tmp_path):
    prev = setup_prev(tmp_path)
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "640000"},
        {"科目": "資本金", "補助": "", "残高": "500000"},
        {"科目": "繰越利益剰余金", "補助": "", "残高": "140000"},
    ])
    findings = run_checks(year_dir, accounts, prev_year_dir=prev)
    assert "普通預金（サンプル銀行）: 期首640000≠前期末650000" in messages(findings, "期首残高")
    assert "繰越利益剰余金（補助なし）: 期首140000≠前期末150000" in messages(findings, "期首残高")


def test_write_report(year_dir, accounts):
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", debit=("雑費", "", 5000), credit=("現金", "", 5000))])
    findings = run_checks(year_dir, accounts)
    assert has_ng(findings)
    path = write_report(year_dir, findings, now=datetime(2026, 9, 16, 10, 0))
    text = path.read_text(encoding="utf-8")
    assert path == year_dir / "output" / "check-result.md"
    assert "# 検算結果（2026-09-16 10:00）" in text
    assert "NG: 1件 / 警告: 0件" in text
    assert "- [マイナス残高] 現金（補助なし）: 期末残高-5000" in text


def test_has_ng_false_for_warnings_only(year_dir, accounts):
    assert has_ng(run_checks(year_dir, accounts)) is False
