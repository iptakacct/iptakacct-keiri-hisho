from datetime import datetime

import pytest

from common import IMPORT_LOG_COLUMNS, JOURNAL_COLUMNS, STAGING_COLUMNS, KessanError, read_rows, write_rows
from helpers import line, staging_row
from post import post_approved

NOW = datetime(2026, 9, 16, 10, 0, 0)


def write_staging(year_dir, rows):
    write_rows(year_dir / "staging.csv", STAGING_COLUMNS, rows)


def capital(**extra):
    return staging_row(日付="2025-04-01", 借方科目="現金", 借方金額="1", 貸方科目="資本金", 貸方金額="1", 承認="済", **extra)


def test_post_moves_only_approved_rows(year_dir, accounts):
    write_staging(year_dir, [
        staging_row(日付="2025-04-01", 借方科目="普通預金", 借方補助="サンプル銀行", 借方金額="100000",
                    貸方科目="売上高", 貸方金額="100000", 摘要="入金", 取り込み元ID="bank:a",
                    判定="確立済み", 承認="済", 証憑ファイル="2025-04.csv"),
        staging_row(日付="2025-04-05", 借方金額="3300", 貸方科目="普通預金", 貸方補助="サンプル銀行",
                    貸方金額="3300", 摘要="手数料", 取り込み元ID="bank:b", 判定="要確認"),
    ])
    result = post_approved(year_dir, accounts, now=NOW)
    assert result.vouchers == ["1"]
    assert result.remaining == 1
    (posted,) = read_rows(year_dir / "journal.csv")
    assert (posted["伝票番号"], posted["登録区分"], posted["登録日時"]) == ("1", "自動", "2026-09-16T10:00:00")
    assert (posted["借方科目"], posted["貸方科目"], posted["取り込み元ID"]) == ("普通預金", "売上高", "bank:a")
    assert "承認" not in posted
    (rest,) = read_rows(year_dir / "staging.csv")
    assert rest["取り込み元ID"] == "bank:b"


def test_post_composite_voucher(year_dir, accounts):
    write_staging(year_dir, [
        staging_row(伝票番号="T1", 日付="2025-04-25", 借方科目="役員報酬", 借方金額="300000", 摘要="4月役員報酬", 承認="済"),
        staging_row(伝票番号="T1", 日付="2025-04-25", 貸方科目="普通預金", 貸方補助="サンプル銀行", 貸方金額="280000",
                    取り込み元ID="bank:c", 承認="済"),
        staging_row(伝票番号="T1", 日付="2025-04-25", 貸方科目="預り金", 貸方金額="20000", 承認="済"),
    ])
    assert post_approved(year_dir, accounts, now=NOW).vouchers == ["1"]
    rows = read_rows(year_dir / "journal.csv")
    assert [r["伝票番号"] for r in rows] == ["1", "1", "1"]
    assert all(r["登録区分"] == "確認済" for r in rows)


def test_numbering_continues_from_journal(year_dir, accounts):
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS,
               [line("7", "2025-04-01", debit=("現金", "", 1), credit=("資本金", "", 1))])
    write_staging(year_dir, [capital(), capital()])
    assert post_approved(year_dir, accounts, now=NOW).vouchers == ["8", "9"]
    assert len(read_rows(year_dir / "journal.csv")) == 3


def test_nothing_approved_is_noop(year_dir, accounts):
    write_staging(year_dir, [staging_row(日付="2025-04-01", 借方科目="現金", 借方金額="1")])
    result = post_approved(year_dir, accounts, now=NOW)
    assert (result.vouchers, result.remaining) == ([], 1)
    assert read_rows(year_dir / "journal.csv") == []


@pytest.mark.parametrize("bad, message", [
    (staging_row(日付="2025-04-05", 借方金額="3300", 貸方科目="普通預金", 貸方金額="3300", 承認="済"),
     "借方科目が未設定"),
    (staging_row(日付="2025-04-05", 借方科目="謎の科目", 借方金額="3300", 貸方科目="普通預金", 貸方金額="3300", 承認="済"),
     "科目マスタに無い借方科目「謎の科目」"),
    (staging_row(日付="2025-04-05", 借方科目="支払手数料", 借方金額="3300", 貸方科目="普通預金", 貸方金額="3000", 承認="済"),
     "借方3300≠貸方3000"),
    (staging_row(日付="2026-04-01", 借方科目="現金", 借方金額="1", 貸方科目="資本金", 貸方金額="1", 承認="済"),
     "期間外の日付"),
    (staging_row(日付="2025/4/5", 借方科目="現金", 借方金額="1", 貸方科目="資本金", 貸方金額="1", 承認="済"),
     "日付「2025/4/5」はYYYY-MM-DD形式で入力"),
    (staging_row(日付="2025-02-30", 借方科目="現金", 借方金額="1", 貸方科目="資本金", 貸方金額="1", 承認="済"),
     "日付「2025-02-30」はYYYY-MM-DD形式で入力"),
    (staging_row(日付="2025-04-05", 借方科目="現金", 借方金額="1万", 貸方科目="資本金", 貸方金額="1", 承認="済"),
     "金額「1万」を読めません"),
    (staging_row(日付="2025-04-05", 借方科目="現金", 借方金額="-100", 貸方科目="資本金", 貸方金額="-100", 承認="済"),
     "マイナスの金額"),
    (staging_row(日付="2025-04-05", 借方科目="現金", 貸方科目="資本金", 承認="済"),
     "金額が0の伝票"),
])
def test_invalid_rows_block_everything(year_dir, accounts, bad, message):
    write_staging(year_dir, [capital(), bad])
    with pytest.raises(KessanError, match=message):
        post_approved(year_dir, accounts, now=NOW)
    assert read_rows(year_dir / "journal.csv") == []
    assert len(read_rows(year_dir / "staging.csv")) == 2


def test_source_id_already_in_journal_is_rejected(year_dir, accounts):
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS,
               [line("1", "2025-04-01", debit=("現金", "", 1), credit=("資本金", "", 1), 取り込み元ID="bank:a")])
    write_staging(year_dir, [capital(取り込み元ID="bank:a")])
    with pytest.raises(KessanError, match="登録済み"):
        post_approved(year_dir, accounts, now=NOW)


def test_same_source_id_in_two_vouchers_is_rejected(year_dir, accounts):
    write_staging(year_dir, [capital(取り込み元ID="bank:a"), capital(取り込み元ID="bank:a")])
    with pytest.raises(KessanError, match="複数の伝票"):
        post_approved(year_dir, accounts, now=NOW)


def test_post_updates_import_log_range(year_dir, accounts):
    write_rows(year_dir / "import-log.csv", IMPORT_LOG_COLUMNS,
               [dict(dict.fromkeys(IMPORT_LOG_COLUMNS, ""), ファイル名="2025-04.csv")])
    write_staging(year_dir, [capital(証憑ファイル="2025-04.csv"), capital(証憑ファイル="2025-04.csv")])
    post_approved(year_dir, accounts, now=NOW)
    write_staging(year_dir, [capital(証憑ファイル="2025-04.csv")])
    post_approved(year_dir, accounts, now=NOW)
    (log,) = read_rows(year_dir / "import-log.csv")
    assert log["登録伝票番号範囲"] == "1-3"


def test_crash_during_staging_write_leaves_journal_written(year_dir, accounts, monkeypatch):
    """登録後の staging.csv 書き換えに失敗しても、帳簿は登録済みで、再実行しても二重登録にならない。"""
    write_staging(year_dir, [capital()])
    import post
    original_replace_rows = post.replace_rows
    staging_writes = []

    def replace_rows_with_failure(path, columns, rows):
        if path.name == "staging.csv":
            staging_writes.append(path)
            if len(staging_writes) == 2:  # 1回目は取り込み元IDの付与、2回目が登録済み行の削除
                raise OSError("Simulated disk failure during staging.csv write")
        return original_replace_rows(path, columns, rows)

    monkeypatch.setattr(post, "replace_rows", replace_rows_with_failure)

    with pytest.raises(KessanError, match="登録は完了済み（伝票 1）ですが、staging.csv の更新に失敗しました"):
        post_approved(year_dir, accounts, now=NOW)

    journal = read_rows(year_dir / "journal.csv")
    assert len(journal) == 1
    assert journal[0]["伝票番号"] == "1"
    staging = read_rows(year_dir / "staging.csv")
    assert len(staging) == 1
    assert staging[0]["承認"] == "済"
    assert staging[0]["取り込み元ID"] == journal[0]["取り込み元ID"]

    # 利用者が気づかずに再実行しても、同じ行は登録済みとして拒否される
    monkeypatch.setattr(post, "replace_rows", original_replace_rows)
    with pytest.raises(KessanError, match="登録済み"):
        post_approved(year_dir, accounts, now=NOW)
    assert len(read_rows(year_dir / "journal.csv")) == 1


def test_locked_staging_file_blocks_everything(year_dir, accounts, monkeypatch):
    import builtins
    write_staging(year_dir, [capital()])
    before = (year_dir / "staging.csv").read_bytes()
    original_open = builtins.open

    def locked_open(file, mode="r", *args, **kwargs):
        if str(file).endswith("staging.csv") and any(m in mode for m in "aw"):
            raise PermissionError(13, "Permission denied", str(file))
        return original_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", locked_open)
    with pytest.raises(KessanError, match="staging.csv に書き込めません（Excelで開いていたら閉じてから再実行）"):
        post_approved(year_dir, accounts, now=NOW)
    monkeypatch.setattr(builtins, "open", original_open)
    assert read_rows(year_dir / "journal.csv") == []
    assert (year_dir / "staging.csv").read_bytes() == before


def test_manual_rows_get_one_source_id_per_voucher(year_dir, accounts):
    write_staging(year_dir, [
        capital(),
        staging_row(伝票番号="T1", 日付="2025-04-25", 借方科目="役員報酬", 借方金額="300000", 承認="済"),
        staging_row(伝票番号="T1", 日付="2025-04-25", 貸方科目="普通預金", 貸方補助="サンプル銀行", 貸方金額="280000",
                    取り込み元ID="bank:c", 承認="済"),
        staging_row(伝票番号="T1", 日付="2025-04-25", 貸方科目="預り金", 貸方金額="20000", 承認="済"),
    ])
    post_approved(year_dir, accounts, now=NOW)
    rows = read_rows(year_dir / "journal.csv")
    ids = [r["取り込み元ID"] for r in rows]
    assert ids[0].startswith("manual:")
    assert ids[1].startswith("manual:") and ids[1] == ids[3] and ids[1] != ids[0]
    assert ids[2] == "bank:c"


def test_malformed_import_log_range_raises_kessan_error_but_posts_voucher(year_dir, accounts):
    """Malformed 登録伝票番号範囲 should raise KessanError, but journal is already written."""
    write_rows(year_dir / "import-log.csv", IMPORT_LOG_COLUMNS,
               [dict(dict.fromkeys(IMPORT_LOG_COLUMNS, ""), ファイル名="2025-04.csv", 登録伝票番号範囲="abc")])
    write_staging(year_dir, [capital(証憑ファイル="2025-04.csv")])

    with pytest.raises(KessanError, match="登録伝票番号範囲が不正"):
        post_approved(year_dir, accounts, now=NOW)

    # Journal should contain the posted voucher (write happens before import log update)
    journal = read_rows(year_dir / "journal.csv")
    assert len(journal) == 1
    assert journal[0]["伝票番号"] == "1"


def test_composite_voucher_with_different_dates_is_rejected(year_dir, accounts):
    write_staging(year_dir, [
        staging_row(伝票番号="T1", 日付="2025-04-25", 借方科目="役員報酬", 借方金額="300000", 承認="済"),
        staging_row(伝票番号="T1", 日付="2025-04-26", 貸方科目="普通預金", 貸方金額="300000", 承認="済"),
    ])
    with pytest.raises(KessanError, match="伝票内で日付が違う"):
        post_approved(year_dir, accounts, now=NOW)
    assert read_rows(year_dir / "journal.csv") == []
