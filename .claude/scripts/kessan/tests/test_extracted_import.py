from datetime import datetime

from bank_import import import_bank
from common import read_rows
from extracted_import import import_extracted, inbox_status
from helpers import cash_book, passbook, write_bank_csv, write_document, write_sources

NOW = datetime(2026, 9, 17, 10, 0, 0)


def run(year_dir, tmp_path, accounts, *documents):
    paths = [write_document(year_dir, d) for d in documents]
    return import_extracted(year_dir, write_sources(tmp_path), accounts, paths, now=NOW)


def test_passbook_rows_are_staged_like_bank_csv(year_dir, tmp_path, accounts):
    result = run(year_dir, tmp_path, accounts, passbook())
    assert (result.imported, result.added, result.rejected) == (["通帳-2025-04.pdf.json"], 3, {})
    rows = read_rows(year_dir / "staging.csv")
    assert [(r["日付"], r["借方科目"], r["貸方科目"], r["貸方補助"], r["借方金額"]) for r in rows] == [
        ("2025-04-01", "普通預金", "", "", "100000"),
        ("2025-04-05", "", "普通預金", "サンプル銀行", "3300"),
        ("2025-04-10", "", "普通預金", "サンプル銀行", "5500"),
    ]
    assert rows[0]["摘要"] == "フリコミ カ)テストシヨウジ"
    assert all(r["証憑ファイル"] == "inbox/通帳-2025-04.pdf" and r["取り込み元ID"].startswith("bank:") for r in rows)
    assert all((r["読み取り信頼度"], r["要確認理由"]) == ("高", "相手科目未設定") for r in rows)
    (log,) = read_rows(year_dir / "import-log.csv")
    assert (log["ファイル名"], log["口座ID"], log["対象期間"], log["件数"]) == (
        "inbox/通帳-2025-04.pdf", "main", "2025-04-01〜2025-04-10", "3")
    assert [b["残高"] for b in read_rows(year_dir / "statement-balances.csv")] == ["1100000", "1096700", "1091200"]


def test_passbook_after_csv_does_not_double_import(year_dir, tmp_path, accounts):
    import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path), now=NOW)
    result = run(year_dir, tmp_path, accounts, passbook())
    assert (result.added, result.duplicates) == (1, 2)
    assert len(read_rows(year_dir / "staging.csv")) == 3


def test_csv_after_passbook_does_not_double_import(year_dir, tmp_path, accounts):
    run(year_dir, tmp_path, accounts, passbook())
    result = import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path), now=NOW)
    assert (result.added, result.duplicates) == (0, 2)


def test_page_that_does_not_chain_is_low_confidence(year_dir, tmp_path, accounts):
    data = passbook()
    data["ページ"][1]["行"][0]["残高"] = 1090000
    result = run(year_dir, tmp_path, accounts, data)
    assert result.low_confidence_pages == ["inbox/通帳-2025-04.pdf ページ2"]
    rows = read_rows(year_dir / "staging.csv")
    assert [(r["読み取り信頼度"], r["要確認理由"]) for r in rows] == [
        ("高", "相手科目未設定"), ("高", "相手科目未設定"), ("低", "ページの残高が連続しない"),
    ]


def test_reimport_same_document_adds_nothing(year_dir, tmp_path, accounts):
    run(year_dir, tmp_path, accounts, passbook())
    result = run(year_dir, tmp_path, accounts, passbook())
    assert (result.imported, result.already_imported, result.added) == ([], ["通帳-2025-04.pdf.json"], 0)
    assert len(read_rows(year_dir / "staging.csv")) == 3
    assert len(read_rows(year_dir / "import-log.csv")) == 1


def test_document_with_only_imported_rows_is_still_logged(year_dir, tmp_path, accounts):
    import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path), now=NOW)
    data = passbook()
    del data["ページ"][1]
    result = run(year_dir, tmp_path, accounts, data)
    assert (result.imported, result.added, result.duplicates) == (["通帳-2025-04.pdf.json"], 0, 2)
    log = read_rows(year_dir / "import-log.csv")
    assert [(r["ファイル名"], r["件数"], r["対象期間"]) for r in log][-1] == (
        "inbox/通帳-2025-04.pdf", "0", "2025-04-01〜2025-04-05")


def test_invalid_document_is_rejected_but_others_are_imported(year_dir, tmp_path, accounts):
    bad = passbook()
    bad["口座ID"] = "nope"
    result = run(year_dir, tmp_path, accounts, bad, cash_book())
    assert list(result.rejected) == ["通帳-2025-04.pdf.json"]
    assert "口座ID「nope」" in result.rejected["通帳-2025-04.pdf.json"][0]
    assert result.imported == ["出納帳.xlsx.json"]
    assert {r["証憑ファイル"] for r in read_rows(year_dir / "staging.csv")} == {"inbox/出納帳.xlsx"}


def test_broken_json_is_rejected(year_dir, tmp_path, accounts):
    path = year_dir / "extracted" / "壊れた.json"
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    result = import_extracted(year_dir, write_sources(tmp_path), accounts, [path], now=NOW)
    assert result.rejected["壊れた.json"][0].startswith("JSONとして読めません")
    assert read_rows(year_dir / "staging.csv") == []


def test_cash_book_rows_use_subject_from_document(year_dir, tmp_path, accounts):
    result = run(year_dir, tmp_path, accounts, cash_book())
    assert result.added == 2
    deposit, stamp = read_rows(year_dir / "staging.csv")
    assert (deposit["借方科目"], deposit["借方金額"], deposit["貸方科目"]) == ("現金", "50000", "")
    assert (stamp["貸方科目"], stamp["貸方金額"], stamp["借方科目"]) == ("現金", "1200", "")
    (log,) = read_rows(year_dir / "import-log.csv")
    assert (log["ファイル名"], log["口座ID"]) == ("inbox/出納帳.xlsx", "出納帳:現金")
    assert [(b["科目"], b["残高"]) for b in read_rows(year_dir / "statement-balances.csv")] == [
        ("現金", "50000"), ("現金", "48800")]


def test_unreadable_document_is_listed_and_not_logged(year_dir, tmp_path, accounts):
    result = run(year_dir, tmp_path, accounts, {"資料": "inbox/ぼやけた領収書.jpg", "種類": "読めない", "理由": "文字がつぶれている"})
    assert (result.unreadable, result.imported) == (["inbox/ぼやけた領収書.jpg"], [])
    assert read_rows(year_dir / "import-log.csv") == []


def test_inbox_status(year_dir, tmp_path, accounts):
    csv_path = write_bank_csv(year_dir / "inbox")
    import_bank(year_dir, write_sources(tmp_path), "main", csv_path, now=NOW)
    run(year_dir, tmp_path, accounts, passbook())
    write_document(year_dir, cash_book())
    write_document(year_dir, {"資料": "inbox/ぼやけた領収書.jpg", "種類": "読めない", "理由": "文字がつぶれている"})
    (year_dir / "inbox" / "未着手.pdf").write_bytes(b"")
    assert inbox_status(year_dir) == [
        ("inbox/2025-04.csv", "取り込み済み"),
        ("inbox/ぼやけた領収書.jpg", "読めない"),
        ("inbox/出納帳.xlsx", "読み取り済み・未取り込み"),
        ("inbox/未着手.pdf", "未処理"),
        ("inbox/通帳-2025-04.pdf", "取り込み済み"),
    ]
