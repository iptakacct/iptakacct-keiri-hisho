from common import JOURNAL_COLUMNS, append_rows, init_year_dir, project, read_rows, to_int, write_rows


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
    init_year_dir(d)
    write_rows(d / "journal.csv", JOURNAL_COLUMNS, [dict.fromkeys(JOURNAL_COLUMNS, "x")])
    init_year_dir(d)
    for name in ["journal.csv", "staging.csv", "import-log.csv",
                 "statement-balances.csv", "opening-balances.csv", "adjustments.csv"]:
        assert (d / name).exists()
    assert (d / "inbox").is_dir()
    assert (d / "output").is_dir()
    assert len(read_rows(d / "journal.csv")) == 1
