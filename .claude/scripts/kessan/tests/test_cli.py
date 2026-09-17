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


def test_post_returns_3_when_check_finds_ng(tmp_path, capsys):
    year = tmp_path / "2026-03期"
    main(["init", "--year-dir", str(year), *PERIOD])
    opening(year, [{"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"}])
    write_rows(year / "staging.csv", STAGING_COLUMNS, [
        staging_row(日付="2025-04-01", 借方科目="現金", 借方金額="1", 貸方科目="資本金", 貸方金額="1", 承認="済"),
    ])
    assert main(["post", "--year-dir", str(year)]) == 3
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


def test_usage_error_exits_with_2():
    import pytest
    with pytest.raises(SystemExit) as e:
        main(["post"])
    assert e.value.code == 2


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


# --- 段階2：資料の読み取り・確認・承認 ---

PATTERNS_YAML = """\
patterns:
  - 名前: 振込手数料
    入出金: 出金
    摘要キーワード: テスウリヨウ
    科目: 支払手数料
    金額の範囲: [0, 5000]
"""


def make_instance(tmp_path):
    from helpers import write_sources
    root = tmp_path / "インスタンス"
    year = root / "work" / "kessan" / "2026-03期"
    assert main(["init", "--year-dir", str(year), *PERIOD]) == 0
    company = root / "context" / "company"
    company.mkdir(parents=True)
    write_sources(company)
    (company / "kessan-patterns.yaml").write_text(PATTERNS_YAML, encoding="utf-8")
    return year


def by_description(year):
    return {r["摘要"]: r for r in read_rows(year / "staging.csv")}


def test_stage2_end_to_end(tmp_path, capsys):
    import json
    from openpyxl import load_workbook
    from helpers import passbook, receipt, write_document
    from review_xlsx import REVIEW_COLUMNS

    year = make_instance(tmp_path)
    opening(year, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"},
        {"科目": "資本金", "補助": "", "残高": "1000000"},
    ])
    write_document(year, passbook())
    write_document(year, receipt())
    assert main(["inbox-status", "--year-dir", str(year)]) == 0
    assert "読み取り済み・未取り込み\tinbox/領収書-0001.jpg" in capsys.readouterr().out

    assert main(["import-extracted", "--year-dir", str(year)]) == 0
    out = capsys.readouterr().out
    assert "証憑: 明細に対応 1件" in out
    assert "自動承認 1件" in out
    rows = by_description(year)
    assert (rows["テスウリヨウ"]["借方科目"], rows["テスウリヨウ"]["承認"]) == ("支払手数料", "済")
    assert rows["カード テストブングテン"]["借方科目"] == "消耗品費"

    candidates = year / "output" / "set-accounts-20260917.json"
    candidates.write_text(json.dumps({
        rows["フリコミ カ)テストシヨウジ"]["取り込み元ID"]: {"貸方科目": "売上高", "要確認理由": "新規の取引先"},
    }, ensure_ascii=False), encoding="utf-8")
    assert main(["set-accounts", "--year-dir", str(year), "--file", str(candidates)]) == 0

    assert main(["review", "--year-dir", str(year)]) == 0
    (review,) = (year / "output").glob("review-*.xlsx")
    workbook = load_workbook(review)
    for line in workbook["確認"].iter_rows(min_row=2):
        line[REVIEW_COLUMNS.index("承認")].value = "済"
    workbook.save(review)
    assert main(["apply-review", "--year-dir", str(year), "--file", str(review)]) == 0
    assert "承認を反映: 2件" in capsys.readouterr().out

    assert main(["post", "--year-dir", str(year)]) == 0
    assert main(["tb", "--year-dir", str(year)]) == 0
    tb = {(r["科目"], r["補助"]): r["期末残高"] for r in read_rows(year / "output" / "trial-balance.csv")}
    assert tb == {("普通預金", "サンプル銀行"): "1091200", ("資本金", ""): "1000000", ("売上高", ""): "100000",
                  ("支払手数料", ""): "3300", ("消耗品費", ""): "5500"}
    registered = {r["摘要"]: r["登録区分"] for r in read_rows(year / "journal.csv")}
    assert registered == {"フリコミ カ)テストシヨウジ": "確認済", "テスウリヨウ": "自動", "カード テストブングテン": "確認済"}
    assert main(["inbox-status", "--year-dir", str(year)]) == 0
    assert "未処理・未取り込み 0件" in capsys.readouterr().out


def test_import_extracted_returns_4_when_a_file_is_rejected(tmp_path, capsys):
    from helpers import cash_book, write_document
    year = make_instance(tmp_path)
    write_document(year, cash_book())
    (year / "extracted" / "壊れた.json").write_text("{", encoding="utf-8")
    assert main(["import-extracted", "--year-dir", str(year)]) == 4
    out = capsys.readouterr().out
    assert "形式エラーのため取り込んでいません: 壊れた.json" in out
    assert len(read_rows(year / "staging.csv")) == 2


def test_import_extracted_with_explicit_file(tmp_path, capsys):
    from helpers import cash_book, passbook, write_document
    year = make_instance(tmp_path)
    write_document(year, passbook())
    path = write_document(year, cash_book())
    assert main(["import-extracted", "--year-dir", str(year), "--file", str(path)]) == 0
    assert {r["証憑ファイル"] for r in read_rows(year / "staging.csv")} == {"inbox/出納帳.xlsx"}


def test_import_bank_auto_approves_with_patterns(tmp_path, capsys):
    year = make_instance(tmp_path)
    csv_path = write_bank_csv(year / "inbox")
    assert main(["import-bank", "--year-dir", str(year), "--account-id", "main", "--file", str(csv_path)]) == 0
    assert "確立済みパターン: 自動承認 1件" in capsys.readouterr().out
    assert by_description(year)["テスウリヨウ"]["判定"] == "確立済み"


def test_set_accounts_rejects_established_judgement(tmp_path, capsys):
    import json
    year = make_instance(tmp_path)
    write_rows(year / "staging.csv", STAGING_COLUMNS, [
        staging_row(日付="2025-04-05", 借方金額="3300", 貸方科目="普通預金", 貸方補助="サンプル銀行", 貸方金額="3300",
                    摘要="ATM", 取り込み元ID="bank:a"),
    ])
    path = tmp_path / "set.json"
    path.write_text(json.dumps({"bank:a": {"借方科目": "雑費", "判定": "確立済み"}}, ensure_ascii=False), encoding="utf-8")
    assert main(["set-accounts", "--year-dir", str(year), "--file", str(path)]) == 1
    assert "判定=確立済み はAIからは指定できません" in capsys.readouterr().err
    assert read_rows(year / "staging.csv")[0]["借方科目"] == ""


def test_approve_command(tmp_path, capsys):
    year = make_instance(tmp_path)
    write_rows(year / "staging.csv", STAGING_COLUMNS, [
        staging_row(日付="2025-04-05", 借方科目="雑費", 借方金額="500", 貸方科目="普通預金", 貸方補助="サンプル銀行",
                    貸方金額="500", 摘要="ATM", 取り込み元ID="bank:a"),
    ])
    assert main(["approve", "--year-dir", str(year), "--ids", "bank:none"]) == 1
    assert "取り込み元ID bank:none: staging.csv にありません" in capsys.readouterr().err
    assert main(["approve", "--year-dir", str(year), "--ids", "bank:a"]) == 0
    assert "承認: 1件" in capsys.readouterr().out
    assert read_rows(year / "staging.csv")[0]["承認"] == "済"


def test_apply_review_prints_memos(tmp_path, capsys):
    from openpyxl import load_workbook
    from review_xlsx import REVIEW_COLUMNS
    year = make_instance(tmp_path)
    write_rows(year / "staging.csv", STAGING_COLUMNS, [
        staging_row(日付="2025-04-05", 借方科目="雑費", 借方金額="500", 貸方科目="普通預金", 貸方補助="サンプル銀行",
                    貸方金額="500", 摘要="ATM", 取り込み元ID="bank:a"),
    ])
    assert main(["review", "--year-dir", str(year)]) == 0
    (review,) = (year / "output").glob("review-*.xlsx")
    workbook = load_workbook(review)
    workbook["確認"].cell(row=2, column=REVIEW_COLUMNS.index("修正メモ") + 1).value = "会議費では？"
    workbook.save(review)
    capsys.readouterr()
    assert main(["apply-review", "--year-dir", str(year), "--file", str(review)]) == 0
    assert "修正メモ: bank:a\t会議費では？" in capsys.readouterr().out
