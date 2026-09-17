"""F2: 資料1件分の取り込みの取り消し（unimport）と、未承認行の破棄（discard）。"""
from datetime import datetime

import pytest

from accounts_update import approve, set_accounts
from bank_import import import_bank
from common import STAGING_COLUMNS, KessanError, read_rows, write_rows
from extracted_import import import_extracted
from helpers import OVERLAP_CSV, cash_book, passbook, receipt, staging_row, write_bank_csv, write_document, write_sources
from post import post_approved
from undo import discard, unimport

NOW = datetime(2026, 9, 17, 10, 0, 0)
FILES = ("staging.csv", "evidence.csv", "statement-balances.csv", "import-log.csv", "journal.csv", "discard-log.csv")
PAYMENT = {("普通預金", "サンプル銀行")}


def run(year_dir, tmp_path, accounts, *documents):
    paths = [write_document(year_dir, d) for d in documents]
    return import_extracted(year_dir, write_sources(tmp_path), accounts, paths, now=NOW)


def snapshot(year_dir):
    return {name: (year_dir / name).read_bytes() for name in FILES if (year_dir / name).exists()}


def by_description(year_dir):
    return {r["摘要"]: r for r in read_rows(year_dir / "staging.csv")}


# --- unimport ---

def test_unimport_passbook_removes_its_rows_and_reimport_works(year_dir, tmp_path, accounts):
    run(year_dir, tmp_path, accounts, passbook(), cash_book())
    result = unimport(year_dir, "inbox/通帳-2025-04.pdf")
    assert (result.staging, result.balances, result.evidence, result.import_log) == (3, 3, 0, 1)
    rows = read_rows(year_dir / "staging.csv")
    assert {r["証憑ファイル"] for r in rows} == {"inbox/出納帳.xlsx"}
    assert {b["ファイル名"] for b in read_rows(year_dir / "statement-balances.csv")} == {"inbox/出納帳.xlsx"}
    assert [r["ファイル名"] for r in read_rows(year_dir / "import-log.csv")] == ["inbox/出納帳.xlsx"]

    again = run(year_dir, tmp_path, accounts, passbook())
    assert (again.imported, again.added) == (["通帳-2025-04.pdf.json"], 3)


@pytest.mark.parametrize("source", ["2025-04.csv", "inbox/2025-04.csv"])
def test_unimport_bank_csv_by_basename_or_inbox_path(year_dir, tmp_path, source):
    csv_path = write_bank_csv(year_dir / "inbox")
    import_bank(year_dir, write_sources(tmp_path), "main", csv_path, now=NOW)
    result = unimport(year_dir, source)
    assert (result.staging, result.balances, result.import_log) == (2, 2, 1)
    assert read_rows(year_dir / "staging.csv") == []


def test_unimport_refuses_when_a_row_is_approved(year_dir, tmp_path, accounts):
    run(year_dir, tmp_path, accounts, passbook())
    fee = by_description(year_dir)["テスウリヨウ"]
    set_accounts(year_dir, accounts, {fee["取り込み元ID"]: {"借方科目": "支払手数料"}}, PAYMENT)
    approve(year_dir, accounts, [fee["取り込み元ID"]])
    before = snapshot(year_dir)
    with pytest.raises(KessanError, match="承認済み.*修正メモ"):
        unimport(year_dir, "inbox/通帳-2025-04.pdf")
    assert snapshot(year_dir) == before


def test_unimport_refuses_when_a_row_is_posted(year_dir, tmp_path, accounts):
    run(year_dir, tmp_path, accounts, passbook())
    fee = by_description(year_dir)["テスウリヨウ"]
    set_accounts(year_dir, accounts, {fee["取り込み元ID"]: {"借方科目": "支払手数料"}}, PAYMENT)
    approve(year_dir, accounts, [fee["取り込み元ID"]])
    post_approved(year_dir, accounts, now=NOW)
    before = snapshot(year_dir)
    with pytest.raises(KessanError, match="登録済み"):
        unimport(year_dir, "inbox/通帳-2025-04.pdf")
    assert snapshot(year_dir) == before


def test_unimport_matched_receipt_restores_the_statement_line(year_dir, tmp_path, accounts):
    run(year_dir, tmp_path, accounts, passbook())
    card_before = by_description(year_dir)["カード テストブングテン"]
    run(year_dir, tmp_path, accounts, receipt(取引先="テストブングテン"))  # 取引先名が摘要にある（理由は「証憑と一致」だけ）
    assert by_description(year_dir)["カード テストブングテン"]["借方科目"] == "消耗品費"

    result = unimport(year_dir, "inbox/領収書-0001.jpg")
    assert (result.staging, result.evidence, result.import_log, result.restored) == (0, 1, 1, [card_before["取り込み元ID"]])
    card = by_description(year_dir)["カード テストブングテン"]
    assert (card["借方科目"], card["借方補助"], card["取引先"], card["要確認理由"]) == ("", "", "", "相手科目未設定")
    assert read_rows(year_dir / "evidence.csv") == []
    assert len(read_rows(year_dir / "staging.csv")) == 3

    again = run(year_dir, tmp_path, accounts, receipt(取引先="テストブングテン"))
    assert again.evidence == {"明細に対応": 1}


def test_unimport_matched_receipt_keeps_accounts_changed_afterwards(year_dir, tmp_path, accounts):
    run(year_dir, tmp_path, accounts, passbook())
    run(year_dir, tmp_path, accounts, receipt(取引先="テストブングテン"))
    card = by_description(year_dir)["カード テストブングテン"]
    set_accounts(year_dir, accounts, {card["取り込み元ID"]: {"借方科目": "雑費"}}, PAYMENT)
    unimport(year_dir, "inbox/領収書-0001.jpg")
    card = by_description(year_dir)["カード テストブングテン"]
    assert (card["借方科目"], card["要確認理由"]) == ("雑費", "証憑の取り消し後（科目を確認）")


def test_unimport_new_entry_receipt_removes_its_row(year_dir, tmp_path, accounts):
    run(year_dir, tmp_path, accounts, receipt(支払方法の推定="立替"), cash_book())
    result = unimport(year_dir, "inbox/領収書-0001.jpg")
    assert (result.staging, result.evidence, result.import_log) == (1, 1, 1)
    assert {r["証憑ファイル"] for r in read_rows(year_dir / "staging.csv")} == {"inbox/出納帳.xlsx"}


def test_unimport_refuses_when_a_receipt_is_attached_to_the_document_lines(year_dir, tmp_path, accounts):
    run(year_dir, tmp_path, accounts, passbook())
    run(year_dir, tmp_path, accounts, receipt())
    before = snapshot(year_dir)
    with pytest.raises(KessanError, match="inbox/領収書-0001.jpg"):
        unimport(year_dir, "inbox/通帳-2025-04.pdf")
    assert snapshot(year_dir) == before


def test_unimport_unknown_source_raises(year_dir):
    with pytest.raises(KessanError, match="取り込まれていません"):
        unimport(year_dir, "inbox/無い.pdf")


def test_unimport_write_failure_is_kessan_error(year_dir, tmp_path, accounts, monkeypatch):
    import undo
    run(year_dir, tmp_path, accounts, passbook())
    original = undo.replace_rows

    def failing(path, columns, rows):
        if str(path).endswith("import-log.csv"):
            raise OSError(13, "Permission denied")
        return original(path, columns, rows)

    monkeypatch.setattr(undo, "replace_rows", failing)
    with pytest.raises(KessanError, match="import-log.csv"):
        unimport(year_dir, "inbox/通帳-2025-04.pdf")


# --- discard ---

def test_discard_removes_rows_and_records_log(year_dir, tmp_path, accounts):
    run(year_dir, tmp_path, accounts, passbook(), receipt(資料="inbox/領収書-0002.jpg", 日付="2025-06-01", 支払方法の推定="立替"))
    rows = by_description(year_dir)
    fee_id = rows["テスウリヨウ"]["取り込み元ID"]
    receipt_id = rows["文房具"]["取り込み元ID"]
    count = discard(year_dir, [fee_id, receipt_id], "同じ明細の二重取り込み（オーナー確認済み）", now=NOW)
    assert count == 2
    assert fee_id not in {r["取り込み元ID"] for r in read_rows(year_dir / "staging.csv")}
    log = read_rows(year_dir / "discard-log.csv")
    assert [(r["日時"], r["取り込み元ID"], r["日付"], r["金額"], r["摘要"], r["理由"]) for r in log] == [
        ("2026-09-17T10:00:00", fee_id, "2025-04-05", "3300", "テスウリヨウ", "同じ明細の二重取り込み（オーナー確認済み）"),
        ("2026-09-17T10:00:00", receipt_id, "2025-06-01", "5500", "文房具", "同じ明細の二重取り込み（オーナー確認済み）"),
    ]
    (ev,) = read_rows(year_dir / "evidence.csv")
    assert ev["状態"] == "破棄"


def test_discard_refuses_approved_rows(year_dir, accounts):
    write_rows(year_dir / "staging.csv", STAGING_COLUMNS, [
        staging_row(日付="2025-04-05", 借方科目="雑費", 借方金額="500", 貸方科目="現金", 貸方金額="500", 取り込み元ID="bank:a", 承認="済"),
    ])
    before = snapshot(year_dir)
    with pytest.raises(KessanError, match="承認済み"):
        discard(year_dir, ["bank:a"], "重複", now=NOW)
    assert snapshot(year_dir) == before


def test_discard_refuses_posted_rows(year_dir, accounts):
    write_rows(year_dir / "staging.csv", STAGING_COLUMNS, [
        staging_row(日付="2025-04-05", 借方科目="雑費", 借方金額="500", 貸方科目="現金", 貸方金額="500", 取り込み元ID="bank:a", 承認="済"),
    ])
    post_approved(year_dir, accounts, now=NOW)
    before = snapshot(year_dir)
    with pytest.raises(KessanError, match="登録済み"):
        discard(year_dir, ["bank:a"], "重複", now=NOW)
    assert snapshot(year_dir) == before


def test_discard_refuses_statement_line_with_attached_receipt(year_dir, tmp_path, accounts):
    run(year_dir, tmp_path, accounts, passbook())
    run(year_dir, tmp_path, accounts, receipt())
    card_id = by_description(year_dir)["カード テストブングテン"]["取り込み元ID"]
    before = snapshot(year_dir)
    with pytest.raises(KessanError, match="証憑"):
        discard(year_dir, [card_id], "重複", now=NOW)
    assert snapshot(year_dir) == before


@pytest.mark.parametrize("ids, reason, message", [
    (["bank:none"], "重複", "staging.csv にありません"),
    (["bank:a"], " ", "理由"),
    ([], "重複", "取り込み元ID"),
])
def test_discard_rejects_invalid_input(year_dir, ids, reason, message):
    write_rows(year_dir / "staging.csv", STAGING_COLUMNS, [staging_row(日付="2025-04-05", 取り込み元ID="bank:a")])
    before = snapshot(year_dir)
    with pytest.raises(KessanError, match=message):
        discard(year_dir, ids, reason, now=NOW)
    assert snapshot(year_dir) == before


def test_init_creates_discard_log(year_dir):
    assert (year_dir / "discard-log.csv").exists()
    assert (year_dir / "discard-log.csv").read_text(encoding="utf-8-sig").strip() == "日時,取り込み元ID,日付,金額,摘要,理由"


# --- M1：記録した資料名と完全に一致するものだけを取り消す ---

def _same_basename_documents(year_dir, tmp_path, accounts):
    """銀行CSV inbox/x.csv と出納帳 inbox/other/x.csv（ファイル名が同じ別の資料）を取り込む。"""
    csv_path = write_bank_csv(year_dir / "inbox", name="x.csv")
    import_bank(year_dir, write_sources(tmp_path), "main", csv_path, now=NOW)
    book = cash_book()
    book["資料"] = "inbox/other/x.csv"
    run(year_dir, tmp_path, accounts, book)


def test_import_bank_records_inbox_relative_path(year_dir, tmp_path):
    csv_path = write_bank_csv(year_dir / "inbox", name="x.csv")
    import_bank(year_dir, write_sources(tmp_path), "main", csv_path, now=NOW)
    assert [r["ファイル名"] for r in read_rows(year_dir / "import-log.csv")] == ["inbox/x.csv"]
    assert {r["証憑ファイル"] for r in read_rows(year_dir / "staging.csv")} == {"inbox/x.csv"}
    assert {r["ファイル名"] for r in read_rows(year_dir / "statement-balances.csv")} == {"inbox/x.csv"}


def test_import_bank_outside_inbox_keeps_basename(year_dir, tmp_path):
    csv_path = write_bank_csv(tmp_path, name="x.csv")
    import_bank(year_dir, write_sources(tmp_path), "main", csv_path, now=NOW)
    assert [r["ファイル名"] for r in read_rows(year_dir / "import-log.csv")] == ["x.csv"]


def test_unimport_exact_path_removes_only_that_document(year_dir, tmp_path, accounts):
    _same_basename_documents(year_dir, tmp_path, accounts)
    result = unimport(year_dir, "inbox/other/x.csv")
    assert result.sources == ["inbox/other/x.csv"]
    assert {r["証憑ファイル"] for r in read_rows(year_dir / "staging.csv")} == {"inbox/x.csv"}
    assert [r["ファイル名"] for r in read_rows(year_dir / "import-log.csv")] == ["inbox/x.csv"]


def test_unimport_ambiguous_basename_refuses(year_dir, tmp_path, accounts):
    _same_basename_documents(year_dir, tmp_path, accounts)
    before = snapshot(year_dir)
    with pytest.raises(KessanError, match="inbox/other/x.csv.*inbox/x.csv|inbox/x.csv.*inbox/other/x.csv"):
        unimport(year_dir, "x.csv")
    assert snapshot(year_dir) == before


def test_post_fills_voucher_range_for_bank_csv_in_inbox(year_dir, tmp_path, accounts):
    csv_path = write_bank_csv(year_dir / "inbox", name="x.csv")
    import_bank(year_dir, write_sources(tmp_path), "main", csv_path, now=NOW)
    fee = by_description(year_dir)["テスウリヨウ"]
    set_accounts(year_dir, accounts, {fee["取り込み元ID"]: {"借方科目": "支払手数料"}}, PAYMENT)
    approve(year_dir, accounts, [fee["取り込み元ID"]])
    post_approved(year_dir, accounts, now=NOW)
    assert [r["登録伝票番号範囲"] for r in read_rows(year_dir / "import-log.csv")] == ["1-1"]


# --- M2：破棄した行は取り込み直しても入れない ---

def test_discarded_bank_row_is_not_imported_again(year_dir, tmp_path):
    sources = write_sources(tmp_path)
    import_bank(year_dir, sources, "main", write_bank_csv(year_dir / "inbox"), now=NOW)
    fee_id = by_description(year_dir)["テスウリヨウ"]["取り込み元ID"]
    discard(year_dir, [fee_id], "二重取り込み（オーナー確認済み）", now=NOW)

    unimport(year_dir, "inbox/2025-04.csv")
    again = import_bank(year_dir, sources, "main", write_bank_csv(year_dir / "inbox"), now=NOW)
    assert (again.added, again.discarded) == (1, 1)
    assert fee_id not in {r["取り込み元ID"] for r in read_rows(year_dir / "staging.csv")}
    assert len(read_rows(year_dir / "statement-balances.csv")) == 2

    other = import_bank(year_dir, sources, "main", write_bank_csv(year_dir / "inbox", name="2025-04-再DL.csv"), now=NOW)
    assert (other.added, other.duplicates, other.discarded) == (0, 1, 1)
    assert fee_id not in {r["取り込み元ID"] for r in read_rows(year_dir / "staging.csv")}
    assert len(read_rows(year_dir / "statement-balances.csv")) == 3  # 破棄した行の残高は、この資料の分として1回だけ記録

    same = import_bank(year_dir, sources, "main", write_bank_csv(year_dir / "inbox", name="2025-04-再DL.csv"), now=NOW)
    assert (same.added, same.discarded) == (0, 1)
    assert len(read_rows(year_dir / "statement-balances.csv")) == 3  # 記録済みの残高は書き直さない


def test_discarded_passbook_row_is_not_imported_again(year_dir, tmp_path, accounts):
    run(year_dir, tmp_path, accounts, passbook())
    fee_id = by_description(year_dir)["テスウリヨウ"]["取り込み元ID"]
    discard(year_dir, [fee_id], "重複", now=NOW)
    unimport(year_dir, "inbox/通帳-2025-04.pdf")
    again = run(year_dir, tmp_path, accounts, passbook())
    assert (again.added, again.discarded) == (2, 1)
    assert fee_id not in {r["取り込み元ID"] for r in read_rows(year_dir / "staging.csv")}


def test_discarded_receipt_is_not_imported_again(year_dir, tmp_path, accounts):
    run(year_dir, tmp_path, accounts, receipt(支払方法の推定="立替"), cash_book())
    receipt_id = by_description(year_dir)["文房具"]["取り込み元ID"]
    discard(year_dir, [receipt_id], "重複", now=NOW)
    unimport(year_dir, "inbox/領収書-0001.jpg")

    again = run(year_dir, tmp_path, accounts, receipt(支払方法の推定="立替"))
    assert (again.added, again.discarded_receipts) == (0, ["inbox/領収書-0001.jpg"])
    assert receipt_id not in {r["取り込み元ID"] for r in read_rows(year_dir / "staging.csv")}
    (ev,) = read_rows(year_dir / "evidence.csv")
    assert (ev["状態"], ev["取り込み元ID"]) == ("破棄", receipt_id)
    assert "inbox/領収書-0001.jpg" in {r["ファイル名"] for r in read_rows(year_dir / "import-log.csv")}


# --- M3：同じ口座で期間が重なる別の資料があれば、まとめて指定しない限り取り消さない ---

def _csv_and_passbook(year_dir, tmp_path, accounts):
    """同じ口座（main）の銀行CSV（4/1〜4/20）と通帳（新しく入る行は4/10だけ）を取り込む。"""
    import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(year_dir / "inbox", text=OVERLAP_CSV), now=NOW)
    run(year_dir, tmp_path, accounts, passbook())


def test_unimport_refuses_when_same_account_document_overlaps(year_dir, tmp_path, accounts):
    _csv_and_passbook(year_dir, tmp_path, accounts)
    before = snapshot(year_dir)
    with pytest.raises(KessanError, match="inbox/通帳-2025-04.pdf.*--source に並べて指定"):
        unimport(year_dir, "inbox/2025-04.csv")
    assert snapshot(year_dir) == before


def test_unimport_overlapping_documents_together_then_reimport(year_dir, tmp_path, accounts):
    _csv_and_passbook(year_dir, tmp_path, accounts)
    ids_before = sorted(r["取り込み元ID"] for r in read_rows(year_dir / "staging.csv"))
    result = unimport(year_dir, ["inbox/2025-04.csv", "inbox/通帳-2025-04.pdf"])
    assert result.sources == ["inbox/2025-04.csv", "inbox/通帳-2025-04.pdf"]
    for name in ("staging.csv", "statement-balances.csv", "import-log.csv"):
        assert read_rows(year_dir / name) == []

    _csv_and_passbook(year_dir, tmp_path, accounts)
    assert sorted(r["取り込み元ID"] for r in read_rows(year_dir / "staging.csv")) == ids_before


def test_unimport_multiple_sources_validates_all_first(year_dir, tmp_path, accounts):
    run(year_dir, tmp_path, accounts, cash_book())
    before = snapshot(year_dir)
    with pytest.raises(KessanError, match="取り込まれていません"):
        unimport(year_dir, ["inbox/出納帳.xlsx", "inbox/無い.pdf"])
    assert snapshot(year_dir) == before


def test_unimport_receipts_are_exempt_from_overlap(year_dir, tmp_path, accounts):
    run(year_dir, tmp_path, accounts, receipt(支払方法の推定="立替"),
        receipt(資料="inbox/領収書-0002.jpg", 取引先="別の店", 支払方法の推定="立替"))
    result = unimport(year_dir, "inbox/領収書-0001.jpg")
    assert result.import_log == 1


# --- M4：取り消し後の明細行には必ず理由を残す ---

def test_remove_reasons_removes_only_given_parts():
    from common import remove_reasons
    assert remove_reasons("A／証憑と一致／B／C", "証憑と一致", "C") == "A／B"
    assert remove_reasons("", "A") == ""


def test_unimport_changed_line_gets_reason_and_is_not_auto_approved(year_dir, tmp_path, accounts):
    from patterns import auto_approve
    run(year_dir, tmp_path, accounts, passbook())
    run(year_dir, tmp_path, accounts, receipt(取引先="テストブングテン"))
    card = by_description(year_dir)["カード テストブングテン"]
    set_accounts(year_dir, accounts, {card["取り込み元ID"]: {"借方科目": "雑費"}}, PAYMENT)
    unimport(year_dir, "inbox/領収書-0001.jpg")
    card = by_description(year_dir)["カード テストブングテン"]
    assert (card["借方科目"], card["要確認理由"]) == ("雑費", "証憑の取り消し後（科目を確認）")
    patterns_path = tmp_path / "kessan-patterns.yaml"
    patterns_path.write_text("patterns:\n  - 名前: 文具店\n    入出金: 出金\n    摘要キーワード: テストブングテン\n    科目: 雑費\n",
                             encoding="utf-8")
    from match import load_abbreviations
    from patterns import load_patterns
    r = auto_approve(year_dir, accounts, load_patterns(patterns_path, accounts), PAYMENT, load_abbreviations())
    assert r.approved == 0
    assert by_description(year_dir)["カード テストブングテン"]["承認"] == ""
