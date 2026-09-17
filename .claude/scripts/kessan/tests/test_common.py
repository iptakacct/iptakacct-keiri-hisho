import pytest

from common import (
    JOURNAL_COLUMNS, KessanError, append_rows, init_year_dir, load_period, parse_amount, project, read_rows,
    replace_rows, to_int, write_rows,
)


def test_to_int_handles_commas_yen_and_blank():
    assert to_int("1,234") == 1234
    assert to_int("¥5,000") == 5000
    assert to_int("") == 0
    assert to_int(None) == 0


def test_append_after_write_keeps_single_bom(tmp_path):
    path = tmp_path / "a.csv"
    write_rows(path, ["x", "y"], [{"x": "1", "y": "あ"}])
    append_rows(path, ["x", "y"], [{"x": "2", "y": "い"}])
    assert path.read_bytes().count(b"\xef\xbb\xbf") == 1
    assert read_rows(path) == [{"x": "1", "y": "あ"}, {"x": "2", "y": "い"}]


def test_append_to_missing_file_writes_header(tmp_path):
    path = tmp_path / "new.csv"
    append_rows(path, ["x"], [{"x": "1"}])
    assert read_rows(path) == [{"x": "1"}]


def test_read_missing_file_returns_empty(tmp_path):
    assert read_rows(tmp_path / "none.csv") == []


def test_project_fills_missing_and_drops_extra():
    assert project({"a": "1", "z": "9"}, ["a", "b"]) == {"a": "1", "b": ""}


def test_init_year_dir_creates_files_and_is_idempotent(tmp_path):
    d = tmp_path / "2026-03期"
    init_year_dir(d, "2025-04-01", "2026-03-31")
    write_rows(d / "journal.csv", JOURNAL_COLUMNS, [dict.fromkeys(JOURNAL_COLUMNS, "x")])
    init_year_dir(d, "2025-04-01", "2026-03-31")
    for name in ["journal.csv", "staging.csv", "import-log.csv",
                 "statement-balances.csv", "opening-balances.csv", "adjustments.csv"]:
        assert (d / name).exists()
    assert (d / "inbox").is_dir()
    assert (d / "output").is_dir()
    assert len(read_rows(d / "journal.csv")) == 1


def test_replace_rows_writes_content_and_leaves_no_tmp(tmp_path):
    path = tmp_path / "a.csv"
    columns = ["x", "y"]
    rows = [{"x": "1", "y": "あ"}]
    replace_rows(path, columns, rows)
    assert path.read_bytes().count(b"\xef\xbb\xbf") == 1
    assert read_rows(path) == rows
    assert not path.with_name(path.name + ".tmp").exists()


def test_init_writes_period_and_load_period_reads_it(tmp_path):
    d = tmp_path / "2026-03期"
    init_year_dir(d, "2025-04-01", "2026-03-31")
    assert load_period(d) == ("2025-04-01", "2026-03-31")
    assert "期首日" in (d / "period.yaml").read_text(encoding="utf-8")


def test_init_with_different_period_raises(tmp_path):
    d = tmp_path / "2026-03期"
    init_year_dir(d, "2025-04-01", "2026-03-31")
    with pytest.raises(KessanError, match="period.yaml"):
        init_year_dir(d, "2025-05-01", "2026-04-30")
    assert load_period(d) == ("2025-04-01", "2026-03-31")


@pytest.mark.parametrize("start, end", [
    ("2025-04-01", "2025-03-31"),
    ("2025-02-30", "2026-03-31"),
    ("2025/04/01", "2026-03-31"),
])
def test_init_rejects_invalid_period(tmp_path, start, end):
    with pytest.raises(KessanError):
        init_year_dir(tmp_path / "y", start, end)
    assert not (tmp_path / "y" / "period.yaml").exists()


def test_load_period_missing_file(tmp_path):
    with pytest.raises(KessanError, match="period.yaml がありません"):
        load_period(tmp_path)


def test_parse_amount_strips_symbols():
    assert parse_amount("1,234", "x") == 1234
    assert parse_amount("￥5，000円", "x") == 5000
    assert parse_amount(" 300 ", "x") == 300
    assert parse_amount("-200000", "x") == -200000
    assert parse_amount("", "x") == 0
    assert parse_amount(None, "x") == 0


@pytest.mark.parametrize("value", ["abc", "1.5", "12-3", "1万"])
def test_parse_amount_rejects_non_integer(value):
    with pytest.raises(KessanError, match=f"journal.csv 伝票1: 金額「{value}」を読めません"):
        parse_amount(value, "journal.csv 伝票1")


def test_read_rows_non_utf8_raises(tmp_path):
    path = tmp_path / "staging.csv"
    path.write_bytes("日付,摘要\n2025-04-01,テスト\n".encode("cp932"))
    with pytest.raises(KessanError, match="staging.csv: UTF-8で読めません"):
        read_rows(path)


def test_append_rows_rejects_different_header(tmp_path):
    path = tmp_path / "a.csv"
    write_rows(path, ["x", "z"], [{"x": "1", "z": "2"}])
    with pytest.raises(KessanError, match="a.csv の列が想定と違います"):
        append_rows(path, ["x", "y"], [{"x": "2", "y": "い"}])
    assert read_rows(path) == [{"x": "1", "z": "2"}]


def test_append_rows_adds_missing_final_newline(tmp_path):
    path = tmp_path / "a.csv"
    path.write_bytes("﻿x,y\r\n1,あ".encode("utf-8"))
    append_rows(path, ["x", "y"], [{"x": "2", "y": "い"}])
    assert read_rows(path) == [{"x": "1", "y": "あ"}, {"x": "2", "y": "い"}]


def test_replace_rows_removes_tmp_on_failure(tmp_path, monkeypatch):
    import common
    path = tmp_path / "a.csv"

    def fail(src, dst):
        raise PermissionError("locked")

    monkeypatch.setattr(common.os, "replace", fail)
    with pytest.raises(PermissionError):
        replace_rows(path, ["x"], [{"x": "1"}])
    assert not path.with_name(path.name + ".tmp").exists()


@pytest.mark.parametrize("value, expected", [("１２３４", 1234), ("１，０００円", 1000), ("－５００", -500)])
def test_parse_amount_reads_full_width_digits(value, expected):
    assert parse_amount(value, "x") == expected


def test_normalize_key_removes_spaces_and_unifies_width():
    from common import normalize_key
    assert normalize_key(" ﾃｽﾄ　文具店 ") == "テスト文具店"
    assert normalize_key("ＡＢＣ 商店") == "ABC商店"
    assert normalize_key(None) == ""
