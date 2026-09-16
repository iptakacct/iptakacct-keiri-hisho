from datetime import datetime

import pytest

from bank_import import import_bank
from common import KessanError, read_rows
from helpers import write_bank_csv, write_sources

NOW = datetime(2026, 9, 16, 10, 0, 0)


def test_import_creates_staging_rows(year_dir, tmp_path):
    result = import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path), now=NOW)
    assert (result.added, result.duplicates, result.zero_amount) == (2, 0, 1)
    deposit, fee = read_rows(year_dir / "staging.csv")
    assert deposit["日付"] == "2025-04-01"
    assert (deposit["借方科目"], deposit["借方補助"], deposit["借方金額"]) == ("普通預金", "サンプル銀行", "100000")
    assert (deposit["貸方科目"], deposit["貸方金額"]) == ("", "100000")
    assert (fee["貸方科目"], fee["貸方補助"], fee["貸方金額"]) == ("普通預金", "サンプル銀行", "3300")
    assert (fee["借方科目"], fee["借方金額"]) == ("", "3300")
    assert deposit["摘要"] == "フリコミ カ）テストシヨウジ"
    assert deposit["証憑ファイル"] == "2025-04.csv"
    assert deposit["取り込み元ID"].startswith("bank:")
    assert (deposit["読み取り信頼度"], deposit["判定"], deposit["要確認理由"], deposit["承認"]) == ("高", "要確認", "相手科目未設定", "")


def test_import_records_log_and_statement_balances(year_dir, tmp_path):
    import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path), now=NOW)
    (log,) = read_rows(year_dir / "import-log.csv")
    assert log["取り込み日時"] == "2026-09-16T10:00:00"
    assert (log["ファイル名"], log["口座ID"], log["対象期間"]) == ("2025-04.csv", "main", "2025-04-01〜2025-04-05")
    assert (log["件数"], log["入金合計"], log["出金合計"], log["登録伝票番号範囲"]) == ("2", "100000", "3300", "")
    balances = read_rows(year_dir / "statement-balances.csv")
    assert [(b["科目"], b["補助"], b["日付"], b["残高"]) for b in balances] == [
        ("普通預金", "サンプル銀行", "2025-04-01", "1100000"),
        ("普通預金", "サンプル銀行", "2025-04-05", "1096700"),
    ]


def test_reimport_same_file_adds_nothing(year_dir, tmp_path):
    sources, csv_path = write_sources(tmp_path), write_bank_csv(tmp_path)
    import_bank(year_dir, sources, "main", csv_path, now=NOW)
    result = import_bank(year_dir, sources, "main", csv_path, now=NOW)
    assert (result.added, result.duplicates) == (0, 2)
    assert len(read_rows(year_dir / "staging.csv")) == 2
    assert len(read_rows(year_dir / "import-log.csv")) == 1
    assert len(read_rows(year_dir / "statement-balances.csv")) == 2


def test_rows_already_in_journal_are_not_imported(year_dir, tmp_path):
    sources, csv_path = write_sources(tmp_path), write_bank_csv(tmp_path)
    import_bank(year_dir, sources, "main", csv_path, now=NOW)
    # staging の行が登録済みになった状態を再現する
    (year_dir / "journal.csv").write_bytes((year_dir / "staging.csv").read_bytes())
    (year_dir / "staging.csv").unlink()
    assert import_bank(year_dir, sources, "main", csv_path, now=NOW).duplicates == 2


def test_identical_rows_in_same_file_are_kept(year_dir, tmp_path):
    text = "入出金明細,\n取引日,お引出し,お預入れ,お取引内容,残高\n2025/04/10,500,,ATM,\n2025/04/10,500,,ATM,\n"
    result = import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path, "dup.csv", text), now=NOW)
    assert result.added == 2
    ids = [r["取り込み元ID"] for r in read_rows(year_dir / "staging.csv")]
    assert len(set(ids)) == 2
    assert read_rows(year_dir / "statement-balances.csv") == []


def test_unknown_account_id(year_dir, tmp_path):
    with pytest.raises(KessanError, match="口座ID"):
        import_bank(year_dir, write_sources(tmp_path), "nope", write_bank_csv(tmp_path))


def test_missing_column(year_dir, tmp_path):
    bad = write_bank_csv(tmp_path, "bad.csv", "x\n日付,金額\n2025/04/01,100\n")
    with pytest.raises(KessanError, match="列が見つかりません"):
        import_bank(year_dir, write_sources(tmp_path), "main", bad)


def test_bad_date(year_dir, tmp_path):
    bad = write_bank_csv(tmp_path, "bad.csv", "x\n取引日,お引出し,お預入れ,お取引内容,残高\n4月1日,100,,A,\n")
    with pytest.raises(KessanError, match="日付"):
        import_bank(year_dir, write_sources(tmp_path), "main", bad)


def test_missing_sources_file(year_dir, tmp_path):
    with pytest.raises(KessanError, match="設定ファイル"):
        import_bank(year_dir, tmp_path / "none.yaml", "main", write_bank_csv(tmp_path))


def test_both_debit_and_credit_nonzero_raises(year_dir, tmp_path):
    """Row with both 入金 and 出金 nonzero should raise error during parsing."""
    bad = write_bank_csv(tmp_path, "bad.csv", "x\n取引日,お引出し,お預入れ,お取引内容,残高\n2025/04/01,500,1000,両方ある,\n")
    with pytest.raises(KessanError, match="入金と出金の両方"):
        import_bank(year_dir, write_sources(tmp_path), "main", bad)
    # Verify staging.csv is empty (nothing was written)
    assert read_rows(year_dir / "staging.csv") == []


def test_encoding_mismatch_raises(year_dir, tmp_path):
    """File encoded as UTF-8 but format says cp932 should raise clear error."""
    # Write a file in UTF-8 containing Japanese that's invalid in cp932
    bad_path = tmp_path / "encoding-bad.csv"
    bad_path.write_text("x\n取引日,お引出し,お預入れ,お取引内容,残高\n2025/04/01,,1000,髙﨑①,\n", encoding="utf-8")
    with pytest.raises(KessanError, match="文字コード"):
        import_bank(year_dir, write_sources(tmp_path), "main", bad_path)


def test_malformed_amount_raises(year_dir, tmp_path):
    """Amount cell with non-numeric text should raise clear error."""
    bad = write_bank_csv(tmp_path, "bad.csv", "x\n取引日,お引出し,お預入れ,お取引内容,残高\n2025/04/01,,不明,テスト,\n")
    with pytest.raises(KessanError, match="金額"):
        import_bank(year_dir, write_sources(tmp_path), "main", bad)


def test_rows_outside_period_are_not_staged(year_dir, tmp_path):
    text = ("x\n取引日,お引出し,お預入れ,お取引内容,残高\n"
            "2025/03/31,,1000,期首前,\n2025/04/01,,2000,期中,\n2026/04/01,500,,期末後,\n")
    result = import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path, "p.csv", text), now=NOW)
    assert (result.added, result.out_of_period) == (1, 2)
    assert [r["摘要"] for r in read_rows(year_dir / "staging.csv")] == ["期中"]
