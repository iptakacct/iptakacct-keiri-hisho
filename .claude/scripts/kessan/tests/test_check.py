from datetime import datetime

from check import has_ng, run_checks, write_report
from common import (
    JOURNAL_COLUMNS, OPENING_COLUMNS, STATEMENT_BALANCE_COLUMNS,
    init_year_dir, read_rows, write_rows,
)
from helpers import line

BANK = ("普通預金", "サンプル銀行")


def setup_clean(year_dir, extra_lines=(), balances=(("2025-04-01", "1100000"), ("2025-04-05", "1096700"))):
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"},
        {"科目": "資本金", "補助": "", "残高": "1000000"},
    ])
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, [
        line("1", "2025-04-01", debit=(*BANK, 100000), credit=("売上高", "", 100000), 取り込み元ID="bank:1"),
        line("2", "2025-04-05", debit=("支払手数料", "", 3300), credit=(*BANK, 3300), 取り込み元ID="bank:2"),
        *extra_lines,
    ])
    write_rows(year_dir / "statement-balances.csv", STATEMENT_BALANCE_COLUMNS, [
        {"科目": BANK[0], "補助": BANK[1], "日付": d, "残高": b, "ファイル名": "2025-04.csv"} for d, b in balances
    ])


def ng_items(findings):
    return [f.item for f in findings if f.level == "NG"]


def messages(findings, item):
    return [f.message for f in findings if f.item == item]


def test_clean_books_have_no_findings(year_dir, accounts):
    setup_clean(year_dir)
    assert run_checks(year_dir, accounts) == []


def test_unbalanced_voucher(year_dir, accounts):
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", debit=("支払手数料", "", 500))])
    findings = run_checks(year_dir, accounts)
    assert "貸借一致" in ng_items(findings)
    assert messages(findings, "貸借一致") == ["伝票3: 借方500≠貸方0"]


def test_unknown_account(year_dir, accounts):
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", debit=("謎の科目", "", 500), credit=("現金", "", 500))])
    assert "科目マスタ" in ng_items(run_checks(year_dir, accounts))


def test_statement_balance_mismatch(year_dir, accounts):
    setup_clean(year_dir, balances=(("2025-04-01", "1100000"), ("2025-04-05", "1096000")))
    findings = run_checks(year_dir, accounts)
    assert ng_items(findings) == ["口座残高"]
    (message,) = messages(findings, "口座残高")
    assert "2025-04-05" in message and "帳簿1096700" in message and "明細1096000" in message


def test_last_balance_of_the_day_is_used(year_dir, accounts):
    setup_clean(year_dir, balances=(("2025-04-05", "1100000"), ("2025-04-05", "1096700")))
    assert run_checks(year_dir, accounts) == []


def test_duplicate_source_id(year_dir, accounts):
    dup = dict(debit=("雑費", "", 100), credit=(*BANK, 100), 取り込み元ID="bank:z")
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", **dup), line("4", "2025-04-10", **dup)])
    findings = run_checks(year_dir, accounts)
    assert "重複" in ng_items(findings)


def test_duplicate_candidate_is_warning(year_dir, accounts):
    same = dict(debit=("支払手数料", "", 770), credit=(*BANK, 770), 取引先="テスト商事")
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", **same), line("4", "2025-04-10", **same)])
    findings = run_checks(year_dir, accounts)
    assert ng_items(findings) == []
    assert [f.item for f in findings] == ["重複候補"]


def test_negative_asset_balance(year_dir, accounts):
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", debit=("雑費", "", 5000), credit=("現金", "", 5000))])
    findings = run_checks(year_dir, accounts)
    assert ng_items(findings) == ["マイナス残高"]
    assert messages(findings, "マイナス残高") == ["現金（補助なし）: 期末残高-5000"]


def test_deficit_in_retained_earnings_is_not_flagged(year_dir, accounts):
    setup_clean(year_dir)
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"},
        {"科目": "資本金", "補助": "", "残高": "1200000"},
        {"科目": "繰越利益剰余金", "補助": "", "残高": "-200000"},
    ])
    assert run_checks(year_dir, accounts) == []


def test_unbalanced_opening(year_dir, accounts):
    setup_clean(year_dir)
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS,
               [{"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"}])
    findings = run_checks(year_dir, accounts)
    assert messages(findings, "期首残高") == ["期首残高の貸借が1000000円ずれている"]


def test_missing_opening_is_warning(year_dir, accounts):
    assert [(f.level, f.item) for f in run_checks(year_dir, accounts)] == [("WARN", "期首残高")]


def setup_prev(tmp_path):
    prev = tmp_path / "2025-03期"
    init_year_dir(prev, "2024-04-01", "2025-03-31")
    write_rows(prev / "opening-balances.csv", OPENING_COLUMNS, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "500000"},
        {"科目": "資本金", "補助": "", "残高": "500000"},
    ])
    write_rows(prev / "journal.csv", JOURNAL_COLUMNS, [
        line("1", "2024-06-01", debit=(*BANK, 200000), credit=("売上高", "", 200000)),
        line("2", "2024-07-01", debit=("雑費", "", 50000), credit=(*BANK, 50000)),
    ])
    return prev


def write_current_opening(year_dir, retained):
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "650000"},
        {"科目": "資本金", "補助": "", "残高": "500000"},
        {"科目": "繰越利益剰余金", "補助": "", "残高": str(retained)},
    ])


def test_opening_matches_previous_year(year_dir, accounts, tmp_path):
    prev = setup_prev(tmp_path)
    write_current_opening(year_dir, 150000)
    assert run_checks(year_dir, accounts, prev_year_dir=prev) == []


def test_opening_differs_from_previous_year(year_dir, accounts, tmp_path):
    prev = setup_prev(tmp_path)
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "640000"},
        {"科目": "資本金", "補助": "", "残高": "500000"},
        {"科目": "繰越利益剰余金", "補助": "", "残高": "140000"},
    ])
    findings = run_checks(year_dir, accounts, prev_year_dir=prev)
    assert "普通預金（サンプル銀行）: 期首640000≠前期末650000" in messages(findings, "期首残高")
    assert "繰越利益剰余金（補助なし）: 期首140000≠前期末150000" in messages(findings, "期首残高")


def test_write_report(year_dir, accounts):
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", debit=("雑費", "", 5000), credit=("現金", "", 5000))])
    findings = run_checks(year_dir, accounts)
    assert has_ng(findings)
    path = write_report(year_dir, findings, now=datetime(2026, 9, 16, 10, 0))
    text = path.read_text(encoding="utf-8")
    assert path == year_dir / "output" / "check-result.md"
    assert "# 検算結果（2026-09-16 10:00）" in text
    assert "NG: 1件 / 警告: 0件" in text
    assert "- [マイナス残高] 現金（補助なし）: 期末残高-5000" in text


def test_has_ng_false_for_warnings_only(year_dir, accounts):
    assert has_ng(run_checks(year_dir, accounts)) is False


def test_unreadable_amount_in_journal_names_file_and_voucher(year_dir, accounts):
    import pytest
    from common import KessanError
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", debit=("雑費", "", "5千"), credit=("現金", "", 5000))])
    with pytest.raises(KessanError, match="journal.csv 伝票3: 金額「5千」を読めません"):
        run_checks(year_dir, accounts)


# --- 伝票番号（journal は数字、adjustments は A で始まる） ---

def write_adjustments(year_dir, rows):
    write_rows(year_dir / "adjustments.csv", JOURNAL_COLUMNS, rows)


def test_adjustments_with_a_numbers_are_clean(year_dir, accounts):
    setup_clean(year_dir)
    write_adjustments(year_dir, [line("A1", "2026-03-31", debit=("法人税等", "", 70000), credit=("未払法人税等", "", 70000))])
    assert run_checks(year_dir, accounts) == []


def test_same_number_in_both_files_is_not_merged(year_dir, accounts):
    setup_clean(year_dir, extra_lines=[line("3", "2025-04-10", debit=("雑費", "", 500))])
    write_adjustments(year_dir, [line("3", "2026-03-31", credit=("未払金", "", 500))])
    findings = run_checks(year_dir, accounts)
    assert messages(findings, "貸借一致") == ["伝票3: 借方500≠貸方0", "adjustments.csv 伝票3: 借方0≠貸方500"]
    assert messages(findings, "伝票番号") == [
        "adjustments.csv: 伝票番号「3」がAで始まっていない（決算整理仕訳は A1, A2…）",
        "伝票番号「3」が journal.csv と adjustments.csv の両方にある",
    ]


def test_non_numeric_journal_voucher_number(year_dir, accounts):
    setup_clean(year_dir, extra_lines=[line("T1", "2025-04-10", debit=("雑費", "", 500), credit=("未払金", "", 500))])
    findings = run_checks(year_dir, accounts)
    assert ng_items(findings) == ["伝票番号"]
    assert messages(findings, "伝票番号") == ["journal.csv: 伝票番号「T1」が数字ではない"]


# --- 期首残高に貸方残高の資産（減価償却累計額）がある場合 ---

def write_opening_with_depreciation(year_dir):
    write_rows(year_dir / "opening-balances.csv", OPENING_COLUMNS, [
        {"科目": "普通預金", "補助": "サンプル銀行", "残高": "1000000"},
        {"科目": "工具器具備品", "補助": "", "残高": "500000"},
        {"科目": "減価償却累計額", "補助": "", "残高": "200000"},
        {"科目": "資本金", "補助": "", "残高": "1300000"},
    ])


def test_opening_with_accumulated_depreciation_is_balanced(year_dir, accounts):
    setup_clean(year_dir)
    write_opening_with_depreciation(year_dir)
    assert run_checks(year_dir, accounts) == []


def test_negative_accumulated_depreciation_is_flagged(year_dir, accounts):
    setup_clean(year_dir, extra_lines=[
        line("3", "2025-04-10", debit=("減価償却累計額", "", 300000), credit=("雑収入", "", 300000)),
    ])
    write_opening_with_depreciation(year_dir)
    findings = run_checks(year_dir, accounts)
    assert messages(findings, "マイナス残高") == ["減価償却累計額（補助なし）: 期末残高-100000"]


# --- 消費税の検算 ---

def taxable_year(year_dir, allocation="全額控除"):
    import yaml
    s = {"区分": "課税", "方式": "原則", "計算": "割戻し", "控除": allocation}
    (year_dir / "period.yaml").write_text(
        '期首日: "2025-04-01"\n期末日: "2026-03-31"\n' + yaml.safe_dump({"消費税": s}, allow_unicode=True),
        encoding="utf-8")


def tax_lines(**extra):
    """売上1本・経費1本（ともに普通預金）。extra は 2本目（経費）の税の列を上書きする。"""
    sale = line("1", "2025-04-01", debit=(*BANK, 110000), credit=("売上高", "", 110000), 取り込み元ID="bank:1",
                貸方税区分="課税売上10%")
    cost = dict(借方税区分="課税仕入10%", 借方インボイス="適格")
    cost.update(extra)
    fee = line("2", "2025-04-05", debit=("支払手数料", "", 3300), credit=(*BANK, 3300), 取り込み元ID="bank:2", **cost)
    return [sale, fee]


def setup_tax(year_dir, **extra):
    setup_clean(year_dir)
    rows = read_rows(year_dir / "journal.csv")
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, [dict(r, **t) for r, t in zip(rows, [
        {k: v for k, v in tax_lines(**extra)[0].items() if k.endswith(("税区分", "インボイス", "個別対応"))},
        {k: v for k, v in tax_lines(**extra)[1].items() if k.endswith(("税区分", "インボイス", "個別対応"))},
    ])])


def tax_messages(findings):
    return [f.message for f in findings if f.item == "税区分"]


def test_exempt_year_skips_tax_checks(year_dir, accounts):
    setup_clean(year_dir)
    rows = read_rows(year_dir / "journal.csv")
    rows[1]["借方税区分"] = "でたらめ"
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, rows)
    assert tax_messages(run_checks(year_dir, accounts)) == []


def test_valid_taxable_journal_passes(year_dir, accounts):
    taxable_year(year_dir)
    setup_tax(year_dir)
    assert run_checks(year_dir, accounts) == []


def test_missing_tax_is_ng(year_dir, accounts):
    taxable_year(year_dir)
    setup_tax(year_dir, 借方税区分="", 借方インボイス="")
    assert tax_messages(run_checks(year_dir, accounts)) == ["伝票2: 借方 支払手数料 の税区分が空"]


def test_unknown_tax_value_is_ng(year_dir, accounts):
    taxable_year(year_dir)
    setup_tax(year_dir, 借方税区分="課税仕入9%")
    assert tax_messages(run_checks(year_dir, accounts)) == ["伝票2: 借方税区分「課税仕入9%」は使えない値"]


def test_wrong_kind_is_ng(year_dir, accounts):
    taxable_year(year_dir)
    setup_tax(year_dir)
    rows = read_rows(year_dir / "journal.csv")
    rows[0]["貸方税区分"] = "課税仕入10%"
    rows[0]["貸方インボイス"] = "適格"
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, rows)
    ng = [f for f in run_checks(year_dir, accounts) if f.level == "NG"]
    assert [f.message for f in ng] == ["伝票1: 貸方 売上高 に税区分「課税仕入10%」は使えない"]


def test_missing_invoice_is_ng(year_dir, accounts):
    taxable_year(year_dir)
    setup_tax(year_dir, 借方インボイス="")
    assert tax_messages(run_checks(year_dir, accounts)) == ["伝票2: 借方 インボイスが空（課税仕入には必須）"]


def test_unknown_invoice_is_ng(year_dir, accounts):
    taxable_year(year_dir)
    setup_tax(year_dir, 借方インボイス="たぶん適格")
    assert tax_messages(run_checks(year_dir, accounts)) == ["伝票2: 借方 インボイス「たぶん適格」は使えない値"]


def test_allocation_required_when_kobetsu(year_dir, accounts):
    taxable_year(year_dir, allocation="個別対応")
    setup_tax(year_dir)
    assert tax_messages(run_checks(year_dir, accounts)) == ["伝票2: 借方 個別対応が空（個別対応方式の課税仕入には必須）"]
    setup_tax(year_dir, 借方個別対応="共通")
    assert run_checks(year_dir, accounts) == []
    setup_tax(year_dir, 借方個別対応="謎")
    assert tax_messages(run_checks(year_dir, accounts)) == ["伝票2: 借方 個別対応「謎」は使えない値"]


def test_invoice_beside_non_purchase_is_warn(year_dir, accounts):
    taxable_year(year_dir)
    setup_tax(year_dir, 借方税区分="対象外")
    findings = run_checks(year_dir, accounts)
    assert [(f.level, f.message) for f in findings if f.item == "税区分"] == [
        ("WARN", "伝票2: 借方 インボイス「適格」は課税仕入以外には付けない")]


def test_adjustments_rows_are_checked(year_dir, accounts):
    taxable_year(year_dir)
    setup_tax(year_dir)
    adj = line("A1", "2026-03-31", debit=("消耗品費", "", 1000), credit=("未払金", "", 1000))
    write_rows(year_dir / "adjustments.csv", JOURNAL_COLUMNS, [adj])
    assert tax_messages(run_checks(year_dir, accounts)) == ["adjustments.csv 伝票A1: 借方 消耗品費 の税区分が空"]


# ---- 最終レビューの修正：証憑の税率・固定資産・経過措置の日付・簡易／2割特例 ----

from common import EVIDENCE_COLUMNS  # noqa: E402
from check import check_tax  # noqa: E402
from tax import TaxSettings, load_transitions  # noqa: E402

TAXABLE = TaxSettings("課税", "原則", "割戻し", "全額控除", False)


def write_evidence(year_dir, rate, source_id="bank:2", state="明細に対応"):
    row = dict.fromkeys(EVIDENCE_COLUMNS, "")
    row.update({"証憑ID": "e1", "証憑ファイル": "inbox/領収書-テスト.jpg", "状態": state, "取り込み元ID": source_id,
                "税率": rate})
    write_rows(year_dir / "evidence.csv", EVIDENCE_COLUMNS, [row])


def test_evidence_rate_8_with_10_percent_is_ng(year_dir, accounts):
    taxable_year(year_dir)
    setup_tax(year_dir)
    write_evidence(year_dir, "8")
    msgs = tax_messages([f for f in run_checks(year_dir, accounts) if f.level == "NG"])
    assert len(msgs) == 1 and "伝票2" in msgs[0] and "inbox/領収書-テスト.jpg" in msgs[0] and "8%" in msgs[0]


def test_evidence_rate_10_with_reduced_is_ng(year_dir, accounts):
    taxable_year(year_dir)
    setup_tax(year_dir, 借方税区分="課税仕入8%軽")
    write_evidence(year_dir, "10")
    assert len([f for f in run_checks(year_dir, accounts) if f.level == "NG" and f.item == "税区分"]) == 1


def test_evidence_rate_matching_is_clean(year_dir, accounts):
    taxable_year(year_dir)
    setup_tax(year_dir, 借方税区分="課税仕入8%軽")
    write_evidence(year_dir, "8")
    assert run_checks(year_dir, accounts) == []


def test_evidence_with_several_candidates_is_ignored(year_dir, accounts):
    taxable_year(year_dir)
    setup_tax(year_dir)
    write_evidence(year_dir, "8", source_id="bank:2;bank:1", state="複数候補")
    assert run_checks(year_dir, accounts) == []


def test_mixed_rate_evidence_needs_split_voucher(year_dir, accounts):
    taxable_year(year_dir)
    setup_tax(year_dir)
    write_evidence(year_dir, "混在")
    msgs = [f.message for f in run_checks(year_dir, accounts) if f.level == "NG"]
    assert len(msgs) == 1 and "伝票2" in msgs[0] and "混在" in msgs[0]


def test_mixed_rate_evidence_with_split_voucher_is_clean(year_dir, accounts):
    taxable_year(year_dir)
    setup_clean(year_dir)
    rows = read_rows(year_dir / "journal.csv")
    rows[0]["貸方税区分"] = "課税売上10%"
    rows = rows[:1] + [
        line("2", "2025-04-05", debit=("消耗品費", "", 2200), 取り込み元ID="bank:2", 借方税区分="課税仕入10%",
             借方インボイス="適格"),
        line("2", "2025-04-05", debit=("消耗品費", "", 1100), 取り込み元ID="bank:2", 借方税区分="課税仕入8%軽",
             借方インボイス="適格"),
        line("2", "2025-04-05", credit=(*BANK, 3300), 取り込み元ID="bank:2"),
    ]
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, rows)
    write_evidence(year_dir, "混在")
    assert run_checks(year_dir, accounts) == []


def _entry(debit, credit, amount=110000, date="2025-06-01", **extra):
    return line("9", date, debit=(debit, "", amount), credit=(credit, "", amount), **extra)


def tax_findings(entries, accounts, settings=TAXABLE):
    return [(f.level, f.message) for f in check_tax(entries, accounts, settings, [], load_transitions())]


def test_purchase_on_credit_side_of_bs_account_warns(accounts):
    found = tax_findings([_entry("普通預金", "工具器具備品", 貸方税区分="課税仕入10%", 貸方インボイス="適格")], accounts)
    assert [lvl for lvl, _ in found] == ["WARN"] and "貸方" in found[0][1]


def test_tax_on_bs_account_outside_fixed_assets_warns(accounts):
    found = tax_findings([_entry("消耗品費", "普通預金", 借方税区分="課税仕入10%", 借方インボイス="適格",
                                 貸方税区分="課税売上10%")], accounts)
    assert [lvl for lvl, _ in found] == ["WARN"] and "普通預金" in found[0][1]
    assert tax_findings([_entry("消耗品費", "普通預金", 借方税区分="課税仕入10%", 借方インボイス="適格",
                                貸方税区分="対象外")], accounts) == []


def test_asset_sale_on_credit_side_as_taxable_sale_is_clean(accounts):
    assert tax_findings([_entry("普通預金", "車両運搬具", 330000, 貸方税区分="課税売上10%")], accounts) == []
    assert tax_findings([_entry("車両運搬具", "普通預金", 330000, 借方税区分="課税仕入10%", 借方インボイス="適格")],
                        accounts) == []


def test_transition_label_must_match_date(accounts):
    found = tax_findings([_entry("消耗品費", "普通預金", 借方税区分="課税仕入10%", 借方インボイス="経過70")], accounts)
    assert [lvl for lvl, _ in found] == ["WARN"] and "経過80" in found[0][1]
    found = tax_findings([_entry("消耗品費", "普通預金", 借方税区分="課税仕入10%", 借方インボイス="制度前")], accounts)
    assert [lvl for lvl, _ in found] == ["WARN"] and "制度前" in found[0][1]


def test_before_invoice_system_is_accepted(accounts):
    assert tax_findings([_entry("消耗品費", "普通預金", date="2023-09-30", 借方税区分="課税仕入10%",
                                借方インボイス="制度前")], accounts) == []


def test_invoice_not_required_under_simplified_or_20_percent(accounts):
    entries = [_entry("消耗品費", "普通預金", 借方税区分="課税仕入10%")]
    assert tax_findings(entries, accounts, TaxSettings("課税", "簡易", "割戻し", "", False)) == []
    assert tax_findings(entries, accounts, TaxSettings("課税", "2割特例", "割戻し", "", False)) == []
    assert [lvl for lvl, _ in tax_findings(entries, accounts)] == ["NG"]
