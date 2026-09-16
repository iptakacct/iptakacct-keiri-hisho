from cli import main
from common import OPENING_COLUMNS, STAGING_COLUMNS, read_rows, write_rows
from helpers import staging_row, write_bank_csv, write_sources

PERIOD = ["--start", "2025-04-01", "--end", "2026-03-31"]


def opening(year_dir, rows):
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS, rows)


def test_end_to_end(tmp_path):
    year = tmp_path / "インスタンス" / "work" / "kessan" / "2026-03期"
    assert main(["init", "--year-dir", str(year), *PERIOD]) == 0
    opening(year, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"},
        {"科目": "資本金", "補助": "", "残高": "1000000"},
    ])
    (tmp_path / "インスタンス" / "context" / "company").mkdir(parents=True)
    write_sources(tmp_path / "インスタンス" / "context" / "company")
    csv_path = write_bank_csv(year / "inbox")
    assert main(["import-bank", "--year-dir", str(year), "--account-id", "main", "--file", str(csv_path)]) == 0

    # 人が相手科目を入れて承認した状態を再現する
    rows = read_rows(year / "staging.csv")
    rows[0].update({"貸方科目": "売上高", "承認": "済"})
    rows[1].update({"借方科目": "支払手数料", "承認": "済"})
    write_rows(year / "staging.csv", STAGING_COLUMNS, rows)

    assert main(["post", "--year-dir", str(year)]) == 0
    assert main(["check", "--year-dir", str(year)]) == 0
    assert main(["tb", "--year-dir", str(year)]) == 0
    tb = {(r["科目"], r["補助"]): r["期末残高"] for r in read_rows(year / "output" / "trial-balance.csv")}
    assert tb == {("普通預金", "サンプル銀行"): "1096700", ("資本金", ""): "1000000",
                  ("売上高", ""): "100000", ("支払手数料", ""): "3300"}
    assert (year / "output" / "check-result.md").read_text(encoding="utf-8").count("NG: 0件") == 1


def test_check_returns_1_on_ng(tmp_path):
    year = tmp_path / "2026-03期"
    main(["init", "--year-dir", str(year), *PERIOD])
    opening(year, [{"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"}])
    assert main(["check", "--year-dir", str(year)]) == 1
    assert "NG: 1件" in (year / "output" / "check-result.md").read_text(encoding="utf-8")


def test_error_is_reported_with_exit_1(tmp_path, capsys):
    year = tmp_path / "2026-03期"
    main(["init", "--year-dir", str(year), *PERIOD])
    code = main(["import-bank", "--year-dir", str(year), "--sources", str(write_sources(tmp_path)),
                 "--account-id", "nope", "--file", str(write_bank_csv(tmp_path))])
    assert code == 1
    assert "エラー: 口座IDが設定ファイルにありません" in capsys.readouterr().err


def test_uninitialized_year_dir(tmp_path, capsys):
    assert main(["check", "--year-dir", str(tmp_path / "none")]) == 1
    assert "init" in capsys.readouterr().err


def test_post_returns_1_when_check_finds_ng(tmp_path, capsys):
    year = tmp_path / "2026-03期"
    main(["init", "--year-dir", str(year), *PERIOD])
    opening(year, [{"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"}])
    write_rows(year / "staging.csv", STAGING_COLUMNS, [
        staging_row(日付="2025-04-01", 借方科目="現金", 借方金額="1", 貸方科目="資本金", 貸方金額="1", 承認="済"),
    ])
    assert main(["post", "--year-dir", str(year)]) == 1
    assert len(read_rows(year / "journal.csv")) == 1
    assert "登録は完了済み" in capsys.readouterr().out


def test_import_reports_out_of_period_rows(tmp_path, capsys):
    year = tmp_path / "2026-03期"
    main(["init", "--year-dir", str(year), *PERIOD])
    text = "x\n取引日,お引出し,お預入れ,お取引内容,残高\n2026/04/01,,1000,期末後,\n"
    code = main(["import-bank", "--year-dir", str(year), "--sources", str(write_sources(tmp_path)),
                 "--account-id", "main", "--file", str(write_bank_csv(tmp_path, "p.csv", text))])
    assert code == 0
    assert "期間外のためスキップ 1件" in capsys.readouterr().out


def test_init_requires_period(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        main(["init", "--year-dir", str(tmp_path / "y")])


def test_import_warns_about_overlapping_period(tmp_path, capsys):
    year = tmp_path / "2026-03期"
    main(["init", "--year-dir", str(year), *PERIOD])
    base = ["import-bank", "--year-dir", str(year), "--sources", str(write_sources(tmp_path)), "--account-id", "main"]
    assert main([*base, "--file", str(write_bank_csv(tmp_path))]) == 0
    capsys.readouterr()
    text = "x\n取引日,お引出し,お預入れ,お取引内容,残高\n2025/04/03,500,,ATM,\n"
    assert main([*base, "--file", str(write_bank_csv(tmp_path, "re.csv", text))]) == 0
    assert "警告: 同じ口座で期間が重なる取り込み済みファイルがあります: 2025-04.csv 2025-04-01〜2025-04-05" in capsys.readouterr().out
