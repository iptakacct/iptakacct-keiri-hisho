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


def test_reimporting_the_same_receipt_file_adds_nothing(year_dir, tmp_path, accounts):
    """C2: 同じ資料ファイルの再取り込みは、これまで通り import-log.csv で止める。"""
    doc = receipt(支払方法の推定="立替")
    path = write_document(year_dir, doc)
    sources = write_sources(tmp_path)
    import_extracted(year_dir, sources, accounts, [path])
    result = import_extracted(year_dir, sources, accounts, [path])
    assert result.already_imported == ["領収書-0001.jpg.json"]
    assert result.imported == []
    assert len(read_rows(year_dir / "staging.csv")) == 1
    assert len(read_rows(year_dir / "evidence.csv")) == 1


def test_two_different_files_with_identical_content_are_both_imported_and_flagged(year_dir, tmp_path, accounts):
    """C2: 別の資料ファイルが同じ内容（日付・金額・取引先）でも、取り込みを止めず要確認で入れる。"""
    run(year_dir, tmp_path, accounts, receipt(支払方法の推定="立替"))
    again = receipt(資料="inbox/領収書-0002.jpg", 取引先=" ﾃｽﾄ文具店", 支払方法の推定="立替")
    result = run(year_dir, tmp_path, accounts, again)
    base_id = make_evidence_id("2025-04-10", 5500, "テスト文具店")

    assert result.added == 1
    assert result.imported == ["領収書-0002.jpg.json"]
    assert result.receipt_duplicates == [("inbox/領収書-0002.jpg", base_id)]

    evidence_rows = read_rows(year_dir / "evidence.csv")
    assert [r["証憑ID"] for r in evidence_rows] == [base_id, f"{base_id}-2"]
    assert len(read_rows(year_dir / "import-log.csv")) == 2

    first, second = read_rows(year_dir / "staging.csv")
    assert first["取り込み元ID"] == f"receipt:{base_id}"
    assert second["取り込み元ID"] == f"receipt:{base_id}-2"
    assert second["要確認理由"] == f"明細に該当なし／証憑の重複の疑い（証憑ID {base_id}）"


def test_same_date_and_amount_with_different_partner_is_flagged(year_dir, tmp_path, accounts):
    """m6: 重複の疑いの理由は、既存の要確認理由を消さずに／で連結する。"""
    run(year_dir, tmp_path, accounts, receipt(支払方法の推定="立替"))
    other = receipt(資料="inbox/領収書-0002.jpg", 取引先="テスト文房具店", 支払方法の推定="立替")
    run(year_dir, tmp_path, accounts, other)
    first, second = read_rows(year_dir / "staging.csv")
    first_id = make_evidence_id("2025-04-10", 5500, "テスト文具店")
    assert second["要確認理由"] == f"明細に該当なし／証憑の重複の疑い（証憑ID {first_id} と日付・金額が一致）"


def test_receipt_row_already_staged_is_not_added_again(year_dir, tmp_path, accounts):
    evidence_id = make_evidence_id("2025-04-10", 5500, "テスト文具店")
    write_staging(year_dir, [staging_row(日付="2025-04-10", 取り込み元ID=f"receipt:{evidence_id}")])
    result = run(year_dir, tmp_path, accounts, receipt(支払方法の推定="立替"))
    assert (result.added, result.evidence) == (0, {"新規仕訳": 1})
    assert len(read_rows(year_dir / "staging.csv")) == 1


# --- Fix round 1 ---

def test_statement_documents_are_processed_before_receipts_in_the_same_call(year_dir, tmp_path, accounts):
    """C1: 同じ回の取り込みでは、証憑より先に明細（通帳・出納帳）を処理する（渡された順序に関わらず）。"""
    result = run(year_dir, tmp_path, accounts, receipt(支払方法の推定="立替"), passbook())
    assert result.evidence == {"明細に対応": 1}
    rows = read_rows(year_dir / "staging.csv")
    assert len(rows) == 3
    assert not any(r["貸方科目"] == "役員借入金" for r in rows)
    card = rows[2]
    assert (card["借方科目"], card["要確認理由"]) == ("消耗品費", "証憑と一致")


def test_invoice_with_unknown_method_is_unpaid_not_receipt_default(year_dir, tmp_path, accounts):
    """I4: 請求書は支払方法が不明でも receipt_default を使わず、後払いと同じ未払候補にする。"""
    invoice = receipt(種類="請求書", 支払方法の推定="不明")
    result = run(year_dir, tmp_path, accounts, invoice, extra="receipt_default: 立替\n")
    assert result.evidence == {"未払候補": 1}
    assert read_rows(year_dir / "staging.csv") == []


def test_receipt_with_unknown_method_still_uses_receipt_default(year_dir, tmp_path, accounts):
    """I4 対比: 領収書はこれまで通り receipt_default を使う（請求書だけ挙動を変える）。"""
    result = run(year_dir, tmp_path, accounts, receipt(支払方法の推定="不明"), extra="receipt_default: 立替\n")
    assert result.evidence == {"新規仕訳": 1}
    assert read_rows(year_dir / "staging.csv")[0]["貸方科目"] == "役員借入金"


def test_low_confidence_receipt_marks_matched_staging_line(year_dir, tmp_path, accounts):
    """m8: 自信度が低い証憑が明細に一致したら、その明細行の読み取り信頼度も低にする。"""
    write_staging(year_dir, [bank_line("bank:a", "2025-04-10")])
    run(year_dir, tmp_path, accounts, receipt(自信度="低"))
    row = read_rows(year_dir / "staging.csv")[0]
    assert row["読み取り信頼度"] == "低"


def test_evidence_write_failure_is_wrapped_with_kessan_error(year_dir, tmp_path, accounts, monkeypatch):
    """I5: evidence.csv への書き込み失敗は、何が書けたか・再実行が安全かが分かる KessanError にする。"""
    import extracted_import

    def failing(*args, **kwargs):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(extracted_import, "append_evidence", failing)
    with pytest.raises(KessanError, match="staging.csv は更新済み.*evidence.csv.*再実行しても二重には登録されません"):
        run(year_dir, tmp_path, accounts, receipt(支払方法の推定="立替"))
    assert len(read_rows(year_dir / "staging.csv")) == 1
    assert read_rows(year_dir / "evidence.csv") == []


def test_import_log_write_failure_is_wrapped_with_kessan_error(year_dir, tmp_path, accounts, monkeypatch):
    """I5: import-log.csv への書き込み失敗も同様に、何が書けたかが分かる KessanError にする。"""
    import extracted_import
    original_append_rows = extracted_import.append_rows

    def wrapper(path, columns, rows):
        if str(path).endswith("import-log.csv"):
            raise OSError(13, "Permission denied")
        return original_append_rows(path, columns, rows)

    monkeypatch.setattr(extracted_import, "append_rows", wrapper)
    with pytest.raises(KessanError, match="staging.csv・evidence.csv は更新済み.*import-log.csv.*再実行しても二重には登録されません"):
        run(year_dir, tmp_path, accounts, receipt(支払方法の推定="立替"))
    assert len(read_rows(year_dir / "staging.csv")) == 1
    assert len(read_rows(year_dir / "evidence.csv")) == 1


# --- Fix round 2 ---

def _fail_import_log_once(monkeypatch):
    """import-log.csv への append_rows を1回だけ失敗させる（2回目以降は本来の動きに戻す）。"""
    import extracted_import
    original_append_rows = extracted_import.append_rows
    state = {"fail": True}

    def wrapper(path, columns, rows):
        if state["fail"] and str(path).endswith("import-log.csv"):
            state["fail"] = False
            raise OSError(13, "Permission denied")
        return original_append_rows(path, columns, rows)

    monkeypatch.setattr(extracted_import, "append_rows", wrapper)


def test_resume_after_import_log_failure_does_not_duplicate_new_entry(year_dir, tmp_path, accounts, monkeypatch):
    """C2/I5 fix round 2, case (1): 新規仕訳を作った直後の import-log 失敗→再実行で X-2 を作らない。"""
    _fail_import_log_once(monkeypatch)
    with pytest.raises(KessanError):
        run(year_dir, tmp_path, accounts, receipt(支払方法の推定="立替"))
    staging_before = read_rows(year_dir / "staging.csv")
    evidence_before = read_rows(year_dir / "evidence.csv")
    assert len(staging_before) == 1
    assert len(evidence_before) == 1
    assert read_rows(year_dir / "import-log.csv") == []

    result = run(year_dir, tmp_path, accounts, receipt(支払方法の推定="立替"))
    assert read_rows(year_dir / "staging.csv") == staging_before
    assert read_rows(year_dir / "evidence.csv") == evidence_before
    assert result.resumed == ["inbox/領収書-0001.jpg"]
    (log,) = read_rows(year_dir / "import-log.csv")
    assert (log["ファイル名"], log["件数"]) == ("inbox/領収書-0001.jpg", "0")


def test_resume_after_import_log_failure_does_not_duplicate_matched_receipt(year_dir, tmp_path, accounts, monkeypatch):
    """fix round 2, case (2): 明細に対応した直後の import-log 失敗→再実行で余分な新規仕訳を作らない。"""
    run(year_dir, tmp_path, accounts, passbook())
    _fail_import_log_once(monkeypatch)
    with pytest.raises(KessanError):
        run(year_dir, tmp_path, accounts, receipt())
    staging_before = read_rows(year_dir / "staging.csv")
    evidence_before = read_rows(year_dir / "evidence.csv")
    assert len(staging_before) == 3
    assert len(evidence_before) == 1
    assert evidence_before[0]["状態"] == "明細に対応"

    result = run(year_dir, tmp_path, accounts, receipt())
    assert read_rows(year_dir / "staging.csv") == staging_before
    assert read_rows(year_dir / "evidence.csv") == evidence_before
    assert result.resumed == ["inbox/領収書-0001.jpg"]


def test_resume_after_import_log_failure_does_not_add_further_duplicate_suffix(year_dir, tmp_path, accounts, monkeypatch):
    """fix round 2, case (3): 別ファイルの重複取り込み直後の import-log 失敗→再実行で -3 を作らない。"""
    run(year_dir, tmp_path, accounts, receipt(支払方法の推定="立替"))
    _fail_import_log_once(monkeypatch)
    again = receipt(資料="inbox/領収書-0002.jpg", 取引先=" ﾃｽﾄ文具店", 支払方法の推定="立替")
    with pytest.raises(KessanError):
        run(year_dir, tmp_path, accounts, again)

    staging_before = read_rows(year_dir / "staging.csv")
    evidence_before = read_rows(year_dir / "evidence.csv")
    base_id = make_evidence_id("2025-04-10", 5500, "テスト文具店")
    assert len(staging_before) == 2
    assert [r["証憑ID"] for r in evidence_before] == [base_id, f"{base_id}-2"]

    result = run(year_dir, tmp_path, accounts, again)
    assert read_rows(year_dir / "staging.csv") == staging_before
    assert read_rows(year_dir / "evidence.csv") == evidence_before
    assert result.resumed == ["inbox/領収書-0002.jpg"]


def test_template_receipt_default_is_unset():
    from pathlib import Path
    from bank_import import load_sources
    from match import receipt_default
    template = Path(__file__).resolve().parents[4] / "テンプレート" / "context" / "company" / "kessan-sources.yaml"
    assert receipt_default(load_sources(template)) == ""
