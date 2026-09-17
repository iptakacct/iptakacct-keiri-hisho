from datetime import datetime

import pytest

from bank_import import import_bank
from check import run_checks
from common import OPENING_COLUMNS, STAGING_COLUMNS, KessanError, init_year_dir, read_rows, write_rows
from evidence import append_evidence
from helpers import evidence_row, write_bank_csv, write_sources
from post import post_approved

HEADER = "入出金明細,\n取引日,お引出し,お預入れ,お取引内容,残高\n"
OLDEST_FIRST = HEADER + (
    '2025/04/01,,"100,000",フリコミ,"1,100,000"\n'
    '2025/04/05,3300,,テスウリヨウ,"1,096,700"\n'
    '2025/04/05,1000,,ATM,"1,095,700"\n'
)
NEWEST_FIRST = HEADER + (
    '2025/04/05,1000,,ATM,"1,095,700"\n'
    '2025/04/05,3300,,テスウリヨウ,"1,096,700"\n'
    '2025/04/01,,"100,000",フリコミ,"1,100,000"\n'
)

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
    assert deposit["摘要"] == "フリコミ カ)テストシヨウジ"  # NFKC で全角括弧は半角になる
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


# --- 並び順（新しい順のCSV） ---

def test_newest_first_file_is_stored_oldest_first(year_dir, tmp_path, accounts):
    import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path, "new.csv", NEWEST_FIRST), now=NOW)
    staged = read_rows(year_dir / "staging.csv")
    assert [r["摘要"] for r in staged] == ["フリコミ", "テスウリヨウ", "ATM"]
    balances = read_rows(year_dir / "statement-balances.csv")
    assert [(b["日付"], b["残高"]) for b in balances] == [
        ("2025-04-01", "1100000"), ("2025-04-05", "1096700"), ("2025-04-05", "1095700"),
    ]
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"},
        {"科目": "資本金", "補助": "", "残高": "1000000"},
    ])
    staged[0].update({"貸方科目": "売上高", "承認": "済"})
    staged[1].update({"借方科目": "支払手数料", "承認": "済"})
    staged[2].update({"借方科目": "雑費", "承認": "済"})
    write_rows(year_dir / "staging.csv", STAGING_COLUMNS, staged)
    post_approved(year_dir, accounts, now=NOW)
    assert [f for f in run_checks(year_dir, accounts) if f.level == "NG"] == []


def test_broken_balance_chain_raises_and_writes_nothing(year_dir, tmp_path):
    text = HEADER + (
        '2025/04/01,,"100,000",フリコミ,"1,100,000"\n'
        '2025/04/05,3300,,テスウリヨウ,"1,090,000"\n'
        '2025/04/06,1000,,ATM,"1,089,000"\n'
    )
    with pytest.raises(KessanError, match=r"broken\.csv 4行目: 残高の連続が合いません"):
        import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path, "broken.csv", text), now=NOW)
    for name in ("staging.csv", "statement-balances.csv", "import-log.csv"):
        assert read_rows(year_dir / name) == []


def test_source_ids_do_not_depend_on_export_order(tmp_path):
    ids = []
    for name, text in (("old.csv", OLDEST_FIRST), ("new.csv", NEWEST_FIRST)):
        d = tmp_path / ("year-" + name) / "2026-03期"
        init_year_dir(d, "2025-04-01", "2026-03-31")
        import_bank(d, write_sources(tmp_path), "main", write_bank_csv(tmp_path, name, text), now=NOW)
        ids.append({r["取り込み元ID"] for r in read_rows(d / "staging.csv")})
    assert len(ids[0]) == 3 and ids[0] == ids[1]


def test_rows_without_some_balances_keep_file_order(year_dir, tmp_path):
    text = HEADER + '2025/04/05,1000,,ATM,\n2025/04/01,,"100,000",フリコミ,"1,100,000"\n'
    import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path, "part.csv", text), now=NOW)
    assert [r["摘要"] for r in read_rows(year_dir / "staging.csv")] == ["ATM", "フリコミ"]


# --- 二重取り込みの検知（摘要の表記ゆれ・期間の重なり） ---

def test_half_width_kana_reexport_is_same_id(year_dir, tmp_path):
    sources = write_sources(tmp_path)
    import_bank(year_dir, sources, "main", write_bank_csv(tmp_path), now=NOW)
    half = (
        "入出金明細,\n取引日,お引出し,お預入れ,お取引内容,残高\n"
        '2025/04/01,,"100,000",ﾌﾘｺﾐ ｶ)ﾃｽﾄｼﾖｳｼﾞ,"1,100,000"\n'
        '2025/04/05,3300,,ﾃｽｳﾘﾖｳ,"1,096,700"\n'
    )
    result = import_bank(year_dir, sources, "main", write_bank_csv(tmp_path, "half.csv", half), now=NOW)
    assert (result.added, result.duplicates) == (0, 2)


def test_same_date_and_amount_with_different_description_is_flagged(year_dir, tmp_path):
    sources = write_sources(tmp_path)
    import_bank(year_dir, sources, "main", write_bank_csv(tmp_path), now=NOW)
    other = HEADER + '2025/04/05,3300,,フリコミテスウリヨウ,"1,096,700"\n'
    result = import_bank(year_dir, sources, "main", write_bank_csv(tmp_path, "other.csv", other), now=NOW)
    assert result.added == 1
    rows = read_rows(year_dir / "staging.csv")
    assert rows[-1]["要確認理由"] == "取り込み済みの明細と日付・金額が一致（二重取り込みの疑い）"
    assert rows[0]["要確認理由"] == "相手科目未設定"


def test_overlapping_import_period_is_reported(year_dir, tmp_path):
    sources = write_sources(tmp_path)
    first = import_bank(year_dir, sources, "main", write_bank_csv(tmp_path), now=NOW)
    assert first.overlapping_imports == []
    later = HEADER + "2025/04/03,500,,ATM,\n2025/04/10,700,,ATM,\n"
    result = import_bank(year_dir, sources, "main", write_bank_csv(tmp_path, "later.csv", later), now=NOW)
    assert result.overlapping_imports == ["2025-04.csv 2025-04-01〜2025-04-05"]


# --- 証憑との突き合わせ（明細側。I3: 証憑を先に取り込んだ場合の計上漏れ・二重計上の検知） ---

def test_new_bank_row_flags_suspected_double_booking_with_new_entry_evidence(year_dir, tmp_path):
    append_evidence(year_dir, [evidence_row(
        証憑ID="ev1", 証憑ファイル="inbox/領収書-0001.jpg", 種類="領収書",
        日付="2025-04-03", 金額="3300", 取引先="テスト業者", 内容="文房具",
        科目候補="消耗品費", 状態="新規仕訳", 取り込み元ID="receipt:ev1", 取り込み日時="2025-04-03T00:00:00",
    )])
    import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path), now=NOW)
    fee = read_rows(year_dir / "staging.csv")[1]
    assert "証憑から計上済みの仕訳と金額・日付が近い（二重計上の疑い）" in fee["要確認理由"]


def test_new_bank_row_flags_match_with_unpaid_evidence(year_dir, tmp_path):
    append_evidence(year_dir, [evidence_row(
        証憑ID="ev2", 証憑ファイル="inbox/請求書-0001.pdf", 種類="請求書",
        日付="2025-04-05", 金額="3300", 取引先="テスト業者", 内容="振込手数料",
        科目候補="支払手数料", 状態="未払候補", 取り込み元ID="", 取り込み日時="2025-04-05T00:00:00",
    )])
    import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path), now=NOW)
    fee = read_rows(year_dir / "staging.csv")[1]
    assert "未払候補の証憑と金額が一致（支払の可能性）" in fee["要確認理由"]


def test_deposit_row_is_not_checked_against_evidence(year_dir, tmp_path):
    """入金側の行は支払ではないので、証憑との突き合わせ対象にしない。"""
    append_evidence(year_dir, [evidence_row(
        証憑ID="ev3", 証憑ファイル="inbox/領収書-0002.jpg", 種類="領収書",
        日付="2025-04-01", 金額="100000", 取引先="テスト業者", 内容="備品",
        科目候補="消耗品費", 状態="新規仕訳", 取り込み元ID="receipt:ev3", 取り込み日時="2025-04-01T00:00:00",
    )])
    import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path), now=NOW)
    deposit = read_rows(year_dir / "staging.csv")[0]
    assert "二重計上の疑い" not in deposit["要確認理由"]
    assert "支払の可能性" not in deposit["要確認理由"]


def test_evidence_match_window_includes_day_seven(year_dir, tmp_path):
    """I3 fix round 2: 証憑との突き合わせ窓は前後7日（両端含む）。7日ちょうどは対象。"""
    append_evidence(year_dir, [evidence_row(
        証憑ID="ev7", 証憑ファイル="inbox/領収書-0007.jpg", 種類="領収書",
        日付="2025-04-12", 金額="3300", 取引先="テスト業者", 内容="文房具",  # 明細の2025-04-05から+7日
        科目候補="消耗品費", 状態="新規仕訳", 取り込み元ID="receipt:ev7", 取り込み日時="2025-04-12T00:00:00",
    )])
    import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path), now=NOW)
    fee = read_rows(year_dir / "staging.csv")[1]
    assert "二重計上の疑い" in fee["要確認理由"]


def test_evidence_match_window_excludes_day_eight(year_dir, tmp_path):
    """I3 fix round 2: 8日離れていれば突き合わせ対象外。"""
    append_evidence(year_dir, [evidence_row(
        証憑ID="ev8", 証憑ファイル="inbox/領収書-0008.jpg", 種類="領収書",
        日付="2025-04-13", 金額="3300", 取引先="テスト業者", 内容="文房具",  # 明細の2025-04-05から+8日
        科目候補="消耗品費", 状態="新規仕訳", 取り込み元ID="receipt:ev8", 取り込み日時="2025-04-13T00:00:00",
    )])
    import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path), now=NOW)
    fee = read_rows(year_dir / "staging.csv")[1]
    assert "二重計上の疑い" not in fee["要確認理由"]


# --- 設定ファイル（kessan-sources.yaml）の不備 ---

def write_yaml(tmp_path, text):
    path = tmp_path / "sources.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_broken_yaml_raises(year_dir, tmp_path):
    with pytest.raises(KessanError, match="設定ファイル"):
        import_bank(year_dir, write_yaml(tmp_path, "formats: [\n"), "main", write_bank_csv(tmp_path))


@pytest.mark.parametrize("fmt, message", [
    ("    columns:\n      日付: 取引日\n      出金: お引出し\n", "date_format"),
    ('    date_format: "%Y/%m/%d"\n', "columns"),
    ('    date_format: "%Y/%m/%d"\n    columns:\n      日付: 取引日\n      摘要: お取引内容\n', "入金・出金"),
])
def test_incomplete_format_raises(year_dir, tmp_path, fmt, message):
    text = "formats:\n  f:\n" + fmt + "accounts:\n  - id: main\n    format: f\n    科目: 普通預金\n"
    with pytest.raises(KessanError, match=message):
        import_bank(year_dir, write_yaml(tmp_path, text), "main", write_bank_csv(tmp_path))


def test_template_accounts_example_parses_when_uncommented():
    import yaml
    from pathlib import Path
    template = Path(__file__).resolve().parents[4] / "テンプレート" / "context" / "company" / "kessan-sources.yaml"
    lines = template.read_text(encoding="utf-8").split("\n")
    assert yaml.safe_load("\n".join(lines))["accounts"] == []
    start = lines.index("# accounts:")
    block = []
    for text in lines[start:]:
        if not text.startswith("#"):
            break
        block.append(text[2:])
    uncommented = [t for t in lines[:start] if t != "accounts: []"] + block
    data = yaml.safe_load("\n".join(uncommented))
    (account,) = data["accounts"]
    assert account["format"] in data["formats"]
    assert account["科目"] == "普通預金"


# --- マイナスの金額 ---

def test_negative_deposit_is_withdrawal_and_negative_withdrawal_is_deposit(year_dir, tmp_path):
    text = HEADER + "2025/04/10,,-500,返金取消,\n2025/04/11,-700,,出金取消,\n"
    import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path, "neg.csv", text), now=NOW)
    first, second = read_rows(year_dir / "staging.csv")
    assert (first["貸方科目"], first["貸方金額"], first["借方科目"], first["借方金額"]) == ("普通預金", "500", "", "500")
    assert (second["借方科目"], second["借方金額"], second["貸方科目"], second["貸方金額"]) == ("普通預金", "700", "", "700")


def test_negative_deposit_with_withdrawal_is_both_sides(year_dir, tmp_path):
    text = HEADER + "2025/04/10,300,-500,両方,\n"
    with pytest.raises(KessanError, match="入金と出金の両方"):
        import_bank(year_dir, write_sources(tmp_path), "main", write_bank_csv(tmp_path, "neg.csv", text), now=NOW)
