import pytest

from extracted import check_passbook_pages, load_document, validate_document
from helpers import cash_book, passbook, receipt, write_document

PERIOD = ("2025-04-01", "2026-03-31")
ACCOUNT_IDS = {"main"}
PAYMENT = {("普通預金", "サンプル銀行"), ("現金", "")}


def errors_of(year_dir, accounts, data):
    write_document(year_dir, data)
    return validate_document(data, accounts, ACCOUNT_IDS, PERIOD, year_dir, PAYMENT)


def test_valid_passbook_has_no_errors(year_dir, accounts):
    assert errors_of(year_dir, accounts, passbook()) == []


def test_valid_receipt_has_no_errors(year_dir, accounts):
    assert errors_of(year_dir, accounts, receipt()) == []


def test_valid_cash_book_has_no_errors(year_dir, accounts):
    assert errors_of(year_dir, accounts, cash_book()) == []


def test_unreadable_document_needs_reason(year_dir, accounts):
    data = {"資料": "inbox/ぼやけた領収書.jpg", "種類": "読めない", "理由": ""}
    assert errors_of(year_dir, accounts, data) == ["「理由」が空です"]


def _set(data, path, value):
    target = data
    for key in path[:-1]:
        target = target[key]
    if value is _DELETE:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return data


_DELETE = object()


@pytest.mark.parametrize("make, path, value, message", [
    (receipt, ["金額"], _DELETE, "「金額」がありません"),
    (receipt, ["金額"], "3,300", "「金額」は円の整数で書く"),
    (receipt, ["金額"], 3300.0, "「金額」は円の整数で書く"),
    (receipt, ["日付"], "2025/04/10", "「日付」はYYYY-MM-DDの実在する日付で書く"),
    (receipt, ["日付"], "2026-04-01", "期間外の日付「2026-04-01」"),
    (receipt, ["支払方法の推定"], "振込", "「支払方法の推定」は 口座／カード／現金／立替／後払い／不明 のどれかで書く"),
    (receipt, ["科目候補"], "謎の科目", "「科目候補」の科目「謎の科目」が科目マスタにありません"),
    (receipt, ["自信度"], "中", "「自信度」は 高／低 のどれかで書く"),
    (receipt, ["種類"], "メモ", "「種類」は"),
    (passbook, ["口座ID"], "nope", "口座ID「nope」が kessan-sources.yaml にありません"),
    (passbook, ["ページ", 0, "行", 0, "出金"], 500, "ページ1 行1: 入金と出金の両方に金額があります"),
    (passbook, ["ページ", 0, "行", 1, "残高"], _DELETE, "ページ1 行2: 「残高」がありません"),
    (passbook, ["ページ", 0, "行", 1, "出金"], -3300, "ページ1 行2: 「出金」にマイナスは書かない"),
    (passbook, ["ページ", 1, "ページ番号"], 1, "2番目のページ: 「ページ番号」は前のページより大きい整数で書く"),
    (passbook, ["ページ"], [], "「ページ」がありません"),
    (cash_book, ["科目"], "謎の科目", "「科目」の科目「謎の科目」が科目マスタにありません"),
    (cash_book, ["行", 1, "日付"], "4月4日", "行2: 「日付」はYYYY-MM-DDの実在する日付で書く"),
])
def test_invalid_documents_are_reported(year_dir, accounts, make, path, value, message):
    data = make()
    write_document(year_dir, data)
    _set(data, path, value)
    errors = validate_document(data, accounts, ACCOUNT_IDS, PERIOD, year_dir, PAYMENT)
    assert any(message in e for e in errors), errors


def test_source_path_must_be_under_inbox_and_exist(year_dir, accounts):
    data = receipt(資料="領収書.jpg")
    assert "「資料」は inbox/ の下に置く相対パスで書く（値: 領収書.jpg）" in validate_document(data, accounts, ACCOUNT_IDS, PERIOD, year_dir, PAYMENT)
    data = receipt(資料="inbox/none.jpg")
    assert "資料 inbox/none.jpg が年度フォルダにありません" in validate_document(data, accounts, ACCOUNT_IDS, PERIOD, year_dir, PAYMENT)


def test_top_level_must_be_object(year_dir, accounts):
    assert validate_document([], accounts, ACCOUNT_IDS, PERIOD, year_dir, PAYMENT) == ["JSONの一番外側は {…}（オブジェクト）で書く"]


def test_load_document_reports_broken_json(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{"資料": ', encoding="utf-8")
    data, errors = load_document(path)
    assert data is None
    assert errors[0].startswith("JSONとして読めません")


# --- Fix round 1: 強化バリデーション ---

def test_passbook_row_with_both_zero_deposit_and_withdrawal_is_rejected(year_dir, accounts):
    data = passbook()
    write_document(year_dir, data)
    data["ページ"][0]["行"][0]["入金"] = 0
    data["ページ"][0]["行"][0]["出金"] = 0
    errors = validate_document(data, accounts, ACCOUNT_IDS, PERIOD, year_dir, PAYMENT)
    assert any("入金・出金のどちらかに金額が必要" in e for e in errors), errors


def test_cash_book_row_with_both_zero_deposit_and_withdrawal_is_rejected(year_dir, accounts):
    data = cash_book()
    write_document(year_dir, data)
    data["行"][0]["入金"] = 0
    data["行"][0]["出金"] = 0
    errors = validate_document(data, accounts, ACCOUNT_IDS, PERIOD, year_dir, PAYMENT)
    assert any("入金・出金のどちらかに金額が必要" in e for e in errors), errors


def test_receipt_required_fields_must_be_nonempty(year_dir, accounts):
    for field in ["科目候補", "取引先", "内容"]:
        data = receipt(**{field: ""})
        write_document(year_dir, data)
        errors = validate_document(data, accounts, ACCOUNT_IDS, PERIOD, year_dir, PAYMENT)
        assert any(f"「{field}」が空です" in e for e in errors), f"{field} should reject empty: {errors}"


def test_cash_book_account_must_be_nonempty(year_dir, accounts):
    data = cash_book()
    write_document(year_dir, data)
    data["科目"] = ""
    errors = validate_document(data, accounts, ACCOUNT_IDS, PERIOD, year_dir, PAYMENT)
    assert any("「科目」が空です" in e for e in errors), errors


def test_source_path_cannot_escape_inbox_via_traversal(year_dir, accounts):
    # Create a file outside inbox to verify path traversal attempt is caught
    outside = year_dir / "outside.jpg"
    outside.write_bytes(b"")
    data = receipt(資料="inbox/../outside.jpg")
    errors = validate_document(data, accounts, ACCOUNT_IDS, PERIOD, year_dir, PAYMENT)
    assert any("inbox/ の下に置く" in e for e in errors), errors


def test_source_path_cannot_be_absolute(year_dir, accounts):
    data = receipt(資料="/absolute/path.jpg")
    errors = validate_document(data, accounts, ACCOUNT_IDS, PERIOD, year_dir, PAYMENT)
    assert any("inbox/ の下に置く" in e for e in errors), errors


# --- 通帳のページ検算 ---

def test_pages_that_chain_are_ok():
    assert check_passbook_pages(passbook()["ページ"]) == []


def test_first_page_without_opening_balance_starts_from_first_row():
    pages = passbook()["ページ"]
    del pages[0]["繰越残高"]
    del pages[1]["繰越残高"]
    assert check_passbook_pages(pages) == []


def test_row_that_does_not_chain_marks_its_page():
    pages = passbook()["ページ"]
    pages[1]["行"][0]["残高"] = 1090000
    assert check_passbook_pages(pages) == [2]


def test_gap_between_pages_marks_next_page():
    pages = passbook()["ページ"]
    pages[1]["繰越残高"] = 1000000
    pages[1]["行"][0]["残高"] = 994500
    assert check_passbook_pages(pages) == [2]


# --- F4：出納帳の科目・補助は kessan-sources.yaml に登録した口座（現金など）に限る ---

def test_cash_book_account_must_be_registered_in_sources(year_dir, accounts):
    data = cash_book()
    data["補助"] = "金庫"
    errors = errors_of(year_dir, accounts, data)
    assert any("現金（金庫）" in e and "kessan-sources.yaml" in e for e in errors), errors
