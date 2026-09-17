from datetime import datetime

import pytest

from common import JOURNAL_COLUMNS, STAGING_COLUMNS, KessanError, read_rows, write_rows
from evidence import make_evidence_id
from extracted_import import import_extracted
from helpers import line, passbook, receipt, staging_row, write_document, write_sources

NOW = datetime(2026, 9, 17, 10, 0, 0)
CARD = """  - id: card
    科目: 未払金
    補助: テストカード
"""


def run(year_dir, tmp_path, accounts, *documents, extra=""):
    paths = [write_document(year_dir, d) for d in documents]
    return import_extracted(year_dir, write_sources(tmp_path, extra), accounts, paths, now=NOW)


def bank_line(source_id, date, amount=5500, subject="普通預金", sub="サンプル銀行"):
    return staging_row(日付=date, 借方金額=str(amount), 貸方科目=subject, 貸方補助=sub, 貸方金額=str(amount),
                       摘要="カード テストブングテン", 取り込み元ID=source_id, 判定="要確認", 要確認理由="相手科目未設定")


def write_staging(year_dir, rows):
    write_rows(year_dir / "staging.csv", STAGING_COLUMNS, rows)


def test_evidence_id_ignores_width_and_spaces():
    assert make_evidence_id("2025-04-10", 5500, "ﾃｽﾄ 文具店") == make_evidence_id("2025-04-10", 5500, "テスト文具店")
    assert make_evidence_id("2025-04-10", 5501, "テスト文具店") != make_evidence_id("2025-04-10", 5500, "テスト文具店")


def test_receipt_matching_one_staging_line_fills_counter_account(year_dir, tmp_path, accounts):
    run(year_dir, tmp_path, accounts, passbook())
    result = run(year_dir, tmp_path, accounts, receipt())
    assert (result.evidence, result.added) == ({"明細に対応": 1}, 0)
    rows = read_rows(year_dir / "staging.csv")
    assert len(rows) == 3
    card = rows[2]
    assert (card["借方科目"], card["取引先"], card["要確認理由"], card["承認"]) == ("消耗品費", "テスト文具店", "証憑と一致", "")
    (ev,) = read_rows(year_dir / "evidence.csv")
    assert (ev["証憑ID"], ev["証憑ファイル"], ev["状態"], ev["取り込み元ID"]) == (
        make_evidence_id("2025-04-10", 5500, "テスト文具店"), "inbox/領収書-0001.jpg", "明細に対応", card["取り込み元ID"])
    log = read_rows(year_dir / "import-log.csv")
    assert (log[-1]["ファイル名"], log[-1]["口座ID"], log[-1]["件数"]) == ("inbox/領収書-0001.jpg", "領収書", "0")


def test_receipt_matching_journal_line_does_not_touch_journal(year_dir, tmp_path, accounts):
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, [
        line("1", "2025-04-12", debit=("消耗品費", "", 5500), credit=("普通預金", "サンプル銀行", 5500), 取り込み元ID="bank:j1"),
    ])
    before = (year_dir / "journal.csv").read_bytes()
    result = run(year_dir, tmp_path, accounts, receipt())
    assert result.evidence == {"明細に対応": 1}
    assert (year_dir / "journal.csv").read_bytes() == before
    assert read_rows(year_dir / "staging.csv") == []
    assert read_rows(year_dir / "evidence.csv")[0]["取り込み元ID"] == "bank:j1"


@pytest.mark.parametrize("line_date, state", [
    ("2025-04-03", "明細に対応"),
    ("2025-04-17", "明細に対応"),
    ("2025-04-02", "新規仕訳"),
    ("2025-04-18", "新規仕訳"),
])
def test_date_window_is_seven_days_inclusive(year_dir, tmp_path, accounts, line_date, state):
    write_staging(year_dir, [bank_line("bank:a", line_date)])
    assert run(year_dir, tmp_path, accounts, receipt()).evidence == {state: 1}


def test_card_account_is_a_payment_account(year_dir, tmp_path, accounts):
    write_staging(year_dir, [bank_line("bank:c", "2025-04-10", subject="未払金", sub="テストカード")])
    assert run(year_dir, tmp_path, accounts, receipt(), extra=CARD).evidence == {"明細に対応": 1}


def test_multiple_candidates_create_nothing(year_dir, tmp_path, accounts):
    write_staging(year_dir, [bank_line("bank:a", "2025-04-09"), bank_line("bank:b", "2025-04-11")])
    before = (year_dir / "staging.csv").read_bytes()
    result = run(year_dir, tmp_path, accounts, receipt())
    assert (result.evidence, result.added) == ({"複数候補": 1}, 0)
    assert (year_dir / "staging.csv").read_bytes() == before
    (ev,) = read_rows(year_dir / "evidence.csv")
    assert (ev["状態"], ev["取り込み元ID"]) == ("複数候補", "bank:a;bank:b")


def test_line_with_evidence_is_not_matched_again(year_dir, tmp_path, accounts):
    write_staging(year_dir, [bank_line("bank:a", "2025-04-10")])
    run(year_dir, tmp_path, accounts, receipt())
    other = receipt(資料="inbox/領収書-0002.jpg", 取引先="サンプル商店")
    assert run(year_dir, tmp_path, accounts, other).evidence == {"新規仕訳": 1}


@pytest.mark.parametrize("method, credit, state", [
    ("立替", "役員借入金", "新規仕訳"),
    ("現金", "現金", "新規仕訳"),
    ("口座", "", "新規仕訳"),
    ("不明", "", "新規仕訳"),
    ("後払い", None, "未払候補"),
])
def test_no_match_uses_payment_estimate(year_dir, tmp_path, accounts, method, credit, state):
    result = run(year_dir, tmp_path, accounts, receipt(支払方法の推定=method))
    assert result.evidence == {state: 1}
    rows = read_rows(year_dir / "staging.csv")
    if credit is None:
        assert rows == []
        assert read_rows(year_dir / "evidence.csv")[0]["取り込み元ID"] == ""
    else:
        (row,) = rows
        assert row["貸方科目"] == credit
        assert row["要確認理由"].startswith("明細に該当なし")


def test_new_row_from_receipt(year_dir, tmp_path, accounts):
    result = run(year_dir, tmp_path, accounts, receipt(支払方法の推定="立替"))
    assert result.added == 1
    (row,) = read_rows(year_dir / "staging.csv")
    evidence_id = make_evidence_id("2025-04-10", 5500, "テスト文具店")
    assert row == staging_row(
        日付="2025-04-10", 借方科目="消耗品費", 借方金額="5500", 貸方科目="役員借入金", 貸方金額="5500",
        摘要="文房具", 取引先="テスト文具店", 証憑ファイル="inbox/領収書-0001.jpg", 取り込み元ID=f"receipt:{evidence_id}",
        読み取り信頼度="高", 判定="要確認", 要確認理由="明細に該当なし",
    )
    (log,) = read_rows(year_dir / "import-log.csv")
    assert (log["口座ID"], log["対象期間"], log["件数"], log["出金合計"]) == ("領収書", "2025-04-10〜2025-04-10", "1", "5500")


def test_unknown_method_uses_receipt_default(year_dir, tmp_path, accounts):
    run(year_dir, tmp_path, accounts, receipt(), extra="receipt_default: 立替\n")
    assert read_rows(year_dir / "staging.csv")[0]["貸方科目"] == "役員借入金"


def test_invalid_receipt_default_raises(year_dir, tmp_path, accounts):
    with pytest.raises(KessanError, match="receipt_default"):
        run(year_dir, tmp_path, accounts, receipt(), extra="receipt_default: カード\n")


def test_same_receipt_photographed_twice_is_not_imported(year_dir, tmp_path, accounts):
    run(year_dir, tmp_path, accounts, receipt(支払方法の推定="立替"))
    again = receipt(資料="inbox/領収書-0002.jpg", 取引先=" ﾃｽﾄ文具店", 支払方法の推定="立替")
    result = run(year_dir, tmp_path, accounts, again)
    assert (result.receipt_duplicates, result.added, result.imported) == (1, 0, ["領収書-0002.jpg.json"])
    assert len(read_rows(year_dir / "staging.csv")) == 1
    assert len(read_rows(year_dir / "evidence.csv")) == 1
    assert len(read_rows(year_dir / "import-log.csv")) == 2


def test_same_date_and_amount_with_different_partner_is_flagged(year_dir, tmp_path, accounts):
    run(year_dir, tmp_path, accounts, receipt(支払方法の推定="立替"))
    other = receipt(資料="inbox/領収書-0002.jpg", 取引先="テスト文房具店", 支払方法の推定="立替")
    run(year_dir, tmp_path, accounts, other)
    first, second = read_rows(year_dir / "staging.csv")
    first_id = make_evidence_id("2025-04-10", 5500, "テスト文具店")
    assert second["要確認理由"] == f"証憑の重複の疑い（証憑ID {first_id} と日付・金額が一致）"


def test_receipt_row_already_staged_is_not_added_again(year_dir, tmp_path, accounts):
    evidence_id = make_evidence_id("2025-04-10", 5500, "テスト文具店")
    write_staging(year_dir, [staging_row(日付="2025-04-10", 取り込み元ID=f"receipt:{evidence_id}")])
    result = run(year_dir, tmp_path, accounts, receipt(支払方法の推定="立替"))
    assert (result.added, result.evidence) == (0, {"新規仕訳": 1})
    assert len(read_rows(year_dir / "staging.csv")) == 1
