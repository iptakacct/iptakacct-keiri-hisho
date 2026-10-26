import pytest

from bank_import import load_sources
from common import EVIDENCE_COLUMNS, STAGING_COLUMNS, KessanError, read_rows, write_rows
from evidence_resolve import resolve_evidence
from helpers import evidence_row, staging_row, write_sources


def setup_multiple(year_dir, state="複数候補", **ev):
    """同じ日付・金額の出金が2行あり、証憑が両方に当てはまる状態。"""
    write_rows(year_dir / "staging.csv", STAGING_COLUMNS, [
        staging_row(日付="2025-04-10", 貸方科目="普通預金", 貸方補助="サンプル銀行", 貸方金額="5500", 借方金額="5500",
                    摘要="カード テストブングテン", 取り込み元ID="bank:a", 要確認理由="相手科目未設定"),
        staging_row(日付="2025-04-11", 貸方科目="普通預金", 貸方補助="サンプル銀行", 貸方金額="5500", 借方金額="5500",
                    摘要="ATM", 取り込み元ID="bank:b", 要確認理由="相手科目未設定"),
    ])
    row = dict(証憑ID="e1", 証憑ファイル="inbox/領収書-0001.jpg", 種類="領収書", 日付="2025-04-10", 金額="5500",
               取引先="テスト文具店", 内容="文房具", 科目候補="消耗品費", 状態=state, 取り込み元ID="bank:a;bank:b",
               支払方法の推定="不明", 自信度="高")
    row.update(ev)
    write_rows(year_dir / "evidence.csv", EVIDENCE_COLUMNS, [evidence_row(**row)])


def sources(tmp_path, extra=""):
    return load_sources(write_sources(tmp_path, extra))


def test_resolve_match(year_dir, tmp_path):
    setup_multiple(year_dir)
    result = resolve_evidence(year_dir, "e1", match="bank:a", sources=sources(tmp_path), accounts=None)
    assert result.state == "明細に対応"
    (ev,) = read_rows(year_dir / "evidence.csv")
    assert (ev["状態"], ev["取り込み元ID"]) == ("明細に対応", "bank:a")
    assert result.attached and result.message == "明細に対応（科目を付与）"
    a, b = read_rows(year_dir / "staging.csv")
    assert (a["借方科目"], a["取引先"]) == ("消耗品費", "テスト文具店")
    assert "証憑と一致" in a["要確認理由"] and "相手科目未設定" not in a["要確認理由"]
    assert b["借方科目"] == ""


def test_resolve_match_rejects_non_candidate(year_dir, tmp_path):
    setup_multiple(year_dir)
    with pytest.raises(KessanError, match="候補"):
        resolve_evidence(year_dir, "e1", match="bank:zzz", sources=sources(tmp_path), accounts=None)
    assert read_rows(year_dir / "evidence.csv")[0]["状態"] == "複数候補"


def test_resolve_requires_exactly_one_choice(year_dir, tmp_path):
    setup_multiple(year_dir)
    with pytest.raises(KessanError):
        resolve_evidence(year_dir, "e1", sources=sources(tmp_path), accounts=None)
    with pytest.raises(KessanError):
        resolve_evidence(year_dir, "e1", match="bank:a", as_payable=True, sources=sources(tmp_path), accounts=None)


def test_resolve_as_payable(year_dir, tmp_path):
    setup_multiple(year_dir)
    assert resolve_evidence(year_dir, "e1", as_payable=True, sources=sources(tmp_path), accounts=None).state == "未払候補"
    (ev,) = read_rows(year_dir / "evidence.csv")
    assert (ev["状態"], ev["取り込み元ID"]) == ("未払候補", "")
    with pytest.raises(KessanError, match="未払候補"):
        resolve_evidence(year_dir, "e1", as_payable=True, sources=sources(tmp_path), accounts=None)


def test_resolve_as_new_creates_staging_row(year_dir, tmp_path):
    setup_multiple(year_dir, 支払方法の推定="立替")
    assert resolve_evidence(year_dir, "e1", as_new=True, sources=sources(tmp_path), accounts=None).state == "新規仕訳"
    (ev,) = read_rows(year_dir / "evidence.csv")
    assert (ev["状態"], ev["取り込み元ID"]) == ("新規仕訳", "receipt:e1")
    rows = read_rows(year_dir / "staging.csv")
    assert len(rows) == 3
    new = rows[-1]
    assert (new["取り込み元ID"], new["借方科目"], new["貸方科目"], new["借方金額"], new["判定"]) == (
        "receipt:e1", "消耗品費", "役員借入金", "5500", "要確認")


def test_resolve_as_new_from_unpaid(year_dir, tmp_path):
    setup_multiple(year_dir, state="未払候補", 取り込み元ID="", 支払方法の推定="立替")
    assert resolve_evidence(year_dir, "e1", as_new=True, sources=sources(tmp_path), accounts=None).state == "新規仕訳"


def test_resolve_as_new_requires_payment_method(year_dir, tmp_path):
    setup_multiple(year_dir, 支払方法の推定="後払い")
    with pytest.raises(KessanError, match="as-payable"):
        resolve_evidence(year_dir, "e1", as_new=True, sources=sources(tmp_path), accounts=None)
    assert len(read_rows(year_dir / "staging.csv")) == 2
    assert read_rows(year_dir / "evidence.csv")[0]["状態"] == "複数候補"


def test_resolve_unknown_evidence(year_dir, tmp_path):
    setup_multiple(year_dir)
    with pytest.raises(KessanError, match="無い"):
        resolve_evidence(year_dir, "nope", as_payable=True, sources=sources(tmp_path), accounts=None)


def test_cli_resolve_evidence(year_dir, tmp_path, capsys):
    from cli import main
    setup_multiple(year_dir)
    src = write_sources(tmp_path)
    assert main(["resolve-evidence", "--year-dir", str(year_dir), "--evidence-id", "e1", "--as-payable",
                 "--sources", str(src)]) == 0
    assert "未払候補" in capsys.readouterr().out


def two_evidence(year_dir, **second):
    """e1（複数候補 bank:a;bank:b）に加えて e2 を足す。"""
    rows = read_rows(year_dir / "evidence.csv")
    row = {**rows[0], "証憑ID": "e2"}
    row.update(second)
    write_rows(year_dir / "evidence.csv", EVIDENCE_COLUMNS, [*rows, evidence_row(**row)])


def test_resolve_match_rejects_double_bind(year_dir, tmp_path):
    setup_multiple(year_dir)
    two_evidence(year_dir, 状態="明細に対応", 取り込み元ID="bank:a")
    with pytest.raises(KessanError, match="別の証憑（e2）が対応済み"):
        resolve_evidence(year_dir, "e1", match="bank:a", sources=sources(tmp_path), accounts=None)
    assert read_rows(year_dir / "evidence.csv")[0]["状態"] == "複数候補"
    assert read_rows(year_dir / "staging.csv")[0]["借方科目"] == ""


def test_resolve_match_rejects_unknown_row(year_dir, tmp_path):
    setup_multiple(year_dir, 取り込み元ID="bank:a;bank:gone")
    with pytest.raises(KessanError, match="無い"):
        resolve_evidence(year_dir, "e1", match="bank:gone", sources=sources(tmp_path), accounts=None)
    assert read_rows(year_dir / "evidence.csv")[0]["状態"] == "複数候補"


def test_resolve_match_onto_approved_row_does_not_attach(year_dir, tmp_path, capsys):
    from cli import main
    setup_multiple(year_dir)
    rows = read_rows(year_dir / "staging.csv")
    rows[0].update({"承認": "済", "借方科目": "雑費"})
    write_rows(year_dir / "staging.csv", STAGING_COLUMNS, rows)
    before = (year_dir / "staging.csv").read_bytes()
    src = write_sources(tmp_path)
    assert main(["resolve-evidence", "--year-dir", str(year_dir), "--evidence-id", "e1", "--match", "bank:a",
                 "--sources", str(src)]) == 0
    assert "付与なし" in capsys.readouterr().out
    assert read_rows(year_dir / "evidence.csv")[0]["状態"] == "明細に対応"
    assert (year_dir / "staging.csv").read_bytes() == before


def test_resolve_match_onto_posted_row(year_dir, tmp_path):
    from common import JOURNAL_COLUMNS
    from helpers import line
    setup_multiple(year_dir)
    write_rows(year_dir / "staging.csv", STAGING_COLUMNS, read_rows(year_dir / "staging.csv")[1:])  # bank:a は登録済みで staging に無い
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, [
        line("1", "2025-04-10", debit=("雑費", "", 5500), credit=("普通預金", "サンプル銀行", 5500), 取り込み元ID="bank:a")])
    result = resolve_evidence(year_dir, "e1", match="bank:a", sources=sources(tmp_path), accounts=None)
    assert (result.state, result.attached) == ("明細に対応", False)


def test_resolve_match_rejects_bad_amount(year_dir, tmp_path):
    setup_multiple(year_dir, 金額="abc")
    with pytest.raises(KessanError, match="数字"):
        resolve_evidence(year_dir, "e1", match="bank:a", sources=sources(tmp_path), accounts=None)


def test_resolve_as_payable_from_new_entry_without_row(year_dir, tmp_path):
    setup_multiple(year_dir, state="新規仕訳", 取り込み元ID="receipt:e1")
    assert resolve_evidence(year_dir, "e1", as_payable=True, sources=sources(tmp_path), accounts=None).state == "未払候補"


def test_resolve_as_payable_from_new_entry_with_row_is_error(year_dir, tmp_path):
    setup_multiple(year_dir, state="新規仕訳", 取り込み元ID="bank:a")
    with pytest.raises(KessanError, match="新規仕訳"):
        resolve_evidence(year_dir, "e1", as_payable=True, sources=sources(tmp_path), accounts=None)


def test_resolve_as_payable_from_matched_is_error(year_dir, tmp_path):
    setup_multiple(year_dir, state="明細に対応", 取り込み元ID="bank:a")
    with pytest.raises(KessanError, match="明細に対応"):
        resolve_evidence(year_dir, "e1", as_payable=True, sources=sources(tmp_path), accounts=None)


def test_resolve_as_new_does_not_duplicate_existing_receipt_row(year_dir, tmp_path):
    setup_multiple(year_dir, 支払方法の推定="立替")
    rows = read_rows(year_dir / "staging.csv")
    write_rows(year_dir / "staging.csv", STAGING_COLUMNS, [*rows, staging_row(取り込み元ID="receipt:e1")])
    resolve_evidence(year_dir, "e1", as_new=True, sources=sources(tmp_path), accounts=None)
    assert len(read_rows(year_dir / "staging.csv")) == 3
    assert read_rows(year_dir / "evidence.csv")[0]["状態"] == "新規仕訳"


def test_resolve_as_new_refuses_discarded_receipt(year_dir, tmp_path):
    from common import DISCARD_LOG_COLUMNS
    setup_multiple(year_dir, 支払方法の推定="立替")
    log = dict.fromkeys(DISCARD_LOG_COLUMNS, "")
    log["取り込み元ID"] = "receipt:e1"
    write_rows(year_dir / "discard-log.csv", DISCARD_LOG_COLUMNS, [log])
    with pytest.raises(KessanError, match="破棄"):
        resolve_evidence(year_dir, "e1", as_new=True, sources=sources(tmp_path), accounts=None)
    assert len(read_rows(year_dir / "staging.csv")) == 2
