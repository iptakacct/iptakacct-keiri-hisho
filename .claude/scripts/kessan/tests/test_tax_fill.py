import pytest

import tax_fill
from common import EVIDENCE_COLUMNS, STAGING_COLUMNS, init_year_dir, read_rows, write_rows
from helpers import staging_row
from tax import TaxSettings, load_transitions
from tax_fill import fill_tax

TAXABLE = TaxSettings("課税", "原則", "割戻し", "全額控除", False)
EXEMPT = TaxSettings("免税", "", "", "", False)


@pytest.fixture
def ydir(tmp_path):
    d = tmp_path / "y"
    init_year_dir(d, "2025-04-01", "2026-03-31",
                  tax={"区分": "課税", "方式": "原則", "計算": "割戻し", "控除": "全額控除"})
    return d


def row(**v):
    base = {"日付": "2025-05-10", "借方科目": "消耗品費", "借方金額": "5500", "貸方科目": "普通預金", "貸方金額": "5500",
            "取り込み元ID": "bank:a1"}
    base.update(v)
    return staging_row(**base)


def evidence(source_id="bank:a1", state="明細に対応", **v):
    base = dict.fromkeys(EVIDENCE_COLUMNS, "")
    base.update({"証憑ID": "e1", "状態": state, "取り込み元ID": source_id, "日付": "2025-05-10", "金額": "5500"})
    base.update(v)
    return base


def run(ydir, accounts, rows, evs=(), settings=TAXABLE):
    write_rows(ydir / "staging.csv", STAGING_COLUMNS, rows)
    write_rows(ydir / "evidence.csv", EVIDENCE_COLUMNS, list(evs))
    result = fill_tax(ydir, accounts, settings, load_transitions())
    return result, read_rows(ydir / "staging.csv")


def test_fills_default_tax_from_account(ydir, accounts):
    result, rows = run(ydir, accounts, [row()])
    assert rows[0]["借方税区分"] == "課税仕入10%"
    assert rows[0]["貸方税区分"] == ""
    assert result.filled == 1


def test_receipt_rate_8_sets_reduced(ydir, accounts):
    _, rows = run(ydir, accounts, [row()], [evidence(税率="8")])
    assert rows[0]["借方税区分"] == "課税仕入8%軽"


def test_rate_8_converts_earlier_default_fill(ydir, accounts):
    run(ydir, accounts, [row()])
    write_rows(ydir / "evidence.csv", EVIDENCE_COLUMNS, [evidence(税率="8")])
    fill_tax(ydir, accounts, TAXABLE, load_transitions())
    rows = read_rows(ydir / "staging.csv")
    assert rows[0]["借方税区分"] == "課税仕入8%軽"
    assert "既定" not in rows[0]["要確認理由"]


def test_mixed_rate_flagged(ydir, accounts):
    result, rows = run(ydir, accounts, [row()], [evidence(税率="混在")])
    assert rows[0]["借方税区分"] == "課税仕入10%"
    assert tax_fill.MIXED_RATE_REASON == "税率が複数（split-tax で分ける）"
    assert tax_fill.MIXED_RATE_REASON in rows[0]["要確認理由"]
    assert result.flagged == 1


def test_invoice_from_evidence(ydir, accounts):
    _, rows = run(ydir, accounts, [row()], [evidence(登録番号="T1234567890123")])
    assert rows[0]["借方インボイス"] == "適格"
    _, rows = run(ydir, accounts, [row(日付="2026-10-15", 借方金額="50000", 貸方金額="50000")],
                  [evidence(日付="2026-10-15", 金額="50000")])
    assert rows[0]["借方インボイス"] == "経過70"


def test_no_evidence_flagged(ydir, accounts):
    result, rows = run(ydir, accounts, [row()])
    assert rows[0]["借方インボイス"] == ""
    assert tax_fill.NO_EVIDENCE_REASON == "証憑が無い課税仕入（インボイスを確認）"
    assert tax_fill.NO_EVIDENCE_REASON in rows[0]["要確認理由"]
    assert result.flagged == 1


def test_no_evidence_reason_removed_when_evidence_arrives(ydir, accounts):
    run(ydir, accounts, [row()])
    write_rows(ydir / "evidence.csv", EVIDENCE_COLUMNS, [evidence(登録番号="T1234567890123")])
    fill_tax(ydir, accounts, TAXABLE, load_transitions())
    rows = read_rows(ydir / "staging.csv")
    assert rows[0]["借方インボイス"] == "適格"
    assert tax_fill.NO_EVIDENCE_REASON not in rows[0]["要確認理由"]


def test_does_not_overwrite_and_is_idempotent(ydir, accounts):
    r = row(借方税区分="対象外", 借方インボイス="なし")
    _, rows = run(ydir, accounts, [r], [evidence(登録番号="T1234567890123", 税率="8")])
    assert (rows[0]["借方税区分"], rows[0]["借方インボイス"]) == ("対象外", "なし")
    assert tax_fill.default_mismatch_reason("課税仕入10%") in rows[0]["要確認理由"]
    first = (ydir / "staging.csv").read_bytes()
    fill_tax(ydir, accounts, TAXABLE, load_transitions())
    assert (ydir / "staging.csv").read_bytes() == first


def test_idempotent_after_fill(ydir, accounts):
    run(ydir, accounts, [row(), row(取り込み元ID="bank:a2", 借方科目="会議費")], [evidence(税率="8")])
    first = (ydir / "staging.csv").read_bytes()
    result = fill_tax(ydir, accounts, TAXABLE, load_transitions())
    assert (ydir / "staging.csv").read_bytes() == first
    assert result.filled == 0


def test_exempt_year_does_nothing(ydir, accounts):
    result, rows = run(ydir, accounts, [row()], settings=EXEMPT)
    assert (result.filled, result.flagged) == (0, 0)
    assert rows[0]["借方税区分"] == "" and rows[0]["要確認理由"] == ""


def test_approved_rows_untouched(ydir, accounts):
    result, rows = run(ydir, accounts, [row(承認="済")])
    assert rows[0]["借方税区分"] == "" and result.filled == 0


def test_allocation_default_common(ydir, accounts):
    settings = TaxSettings("課税", "原則", "割戻し", "個別対応", False)
    _, rows = run(ydir, accounts, [row()], settings=settings)
    assert rows[0]["借方個別対応"] == "共通"
    _, rows = run(ydir, accounts, [row()])
    assert rows[0]["借方個別対応"] == ""


def test_possible_reduced_rate_flagged(ydir, accounts):
    _, rows = run(ydir, accounts, [row(借方科目="会議費")], [evidence()])
    assert tax_fill.REDUCED_RATE_POSSIBLE_REASON == "軽減税率の可能性（証憑で税率を確認）"
    assert tax_fill.REDUCED_RATE_POSSIBLE_REASON in rows[0]["要確認理由"]
    _, rows = run(ydir, accounts, [row(借方科目="会議費")], [evidence(税率="10")])
    assert tax_fill.REDUCED_RATE_POSSIBLE_REASON not in rows[0]["要確認理由"]


def test_fixed_asset_and_credit_side(ydir, accounts):
    # 固定資産（有形固定資産）は既定税区分を持つ側だけ。収益の貸方も対象
    _, rows = run(ydir, accounts, [row(借方科目="普通預金", 貸方科目="売上高")])
    assert rows[0]["貸方税区分"] == "課税売上10%"
    assert rows[0]["借方税区分"] == ""


def test_multiple_candidate_evidence_ignored(ydir, accounts):
    _, rows = run(ydir, accounts, [row()],
                  [evidence(source_id="bank:a1;bank:a2", state="複数候補", 登録番号="T1234567890123")])
    assert rows[0]["借方インボイス"] == ""


def test_no_evidence_reason_removed_when_tax_no_longer_purchase(ydir, accounts):
    run(ydir, accounts, [row()])
    rows = read_rows(ydir / "staging.csv")
    rows[0]["借方税区分"] = "対象外"
    write_rows(ydir / "staging.csv", STAGING_COLUMNS, rows)
    fill_tax(ydir, accounts, TAXABLE, load_transitions())
    assert tax_fill.NO_EVIDENCE_REASON not in read_rows(ydir / "staging.csv")[0]["要確認理由"]
