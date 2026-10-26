"""決算整理：減価償却の候補。期間 2025-04-01〜2026-03-31（架空のデータ）。"""
from decimal import Decimal

import pytest

import close
import close_depreciation
from common import JOURNAL_COLUMNS, KessanError, read_rows, write_rows
from helpers import line

SOURCES = {"accounts": [{"id": "main", "科目": "普通預金", "補助": "サンプル銀行"}]}
HEADER = "資産名,科目,取得日,事業供用日,取得価額,償却方法,耐用年数,前期末償却累計額,除却日,備考\n"


@pytest.fixture
def company(tmp_path):
    d = tmp_path / "company"
    d.mkdir()
    return d


def ledger(company, *rows, header=HEADER):
    (company / "fixed-assets.csv").write_text(header + "".join(r + "\n" for r in rows), encoding="utf-8-sig")


def books(year_dir, rows=()):
    base = [line("0", "2025-04-01", debit=("普通預金", "サンプル銀行", 5000000), credit=("資本金", "", 5000000))]
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, base + list(rows))


def asset_opening(year_dir):
    # 直接控除（一括償却・少額特例・無形固定資産）で資産の残高がマイナスにならないよう、期首に資産の残高を置く
    (year_dir / "opening-balances.csv").write_text(
        "科目,補助,残高\n工具器具備品,,10000000\nソフトウエア,,10000000\n資本金,,20000000\n", encoding="utf-8-sig")


def run(year_dir, accounts, company):
    if not read_rows(year_dir / "journal.csv"):
        asset_opening(year_dir)
    return close.run_close(year_dir, accounts, items=[close_depreciation], sources=SOURCES, company_dir=company)


def staged(year_dir):
    return read_rows(year_dir / "adjustments-staging.csv")


def one(year_dir, accounts, company):
    run(year_dir, accounts, company)
    (r,) = staged(year_dir)
    return r


def test_rates_table_is_complete():
    rates = close_depreciation.load_rates()
    assert set(rates["定額法"]) == set(range(2, 51)) and set(rates["定率法"]) == set(range(2, 51))
    assert rates["定額法"][4] == Decimal("0.250")
    assert rates["定率法"][4] == {"償却率": Decimal("0.500"), "改定償却率": Decimal("1.000"), "保証率": Decimal("0.12499")}
    assert rates["定率法"][10]["保証率"] == Decimal("0.06552")


def test_first_year_months(year_dir, accounts, company):
    ledger(company, "サーバー,工具器具備品,2025-06-20,2025-07-01,400000,定額法,4,0,,")
    r = one(year_dir, accounts, company)  # 7月〜3月＝9か月：400,000 × 0.250 × 9/12
    assert (r["取り込み元ID"], r["日付"], r["借方科目"], r["借方金額"], r["貸方科目"], r["貸方金額"]) == (
        "close:減価償却:サーバー", "2026-03-31", "減価償却費", "75000", "減価償却累計額", "75000")
    assert (r["借方税区分"], r["貸方税区分"]) == ("", "")


def test_supply_date_defaults_to_acquisition_date(year_dir, accounts, company):
    ledger(company, "サーバー,工具器具備品,2025-07-01,,400000,定額法,4,0,,")
    assert one(year_dir, accounts, company)["借方金額"] == "75000"


def test_declining_balance_first_year(year_dir, accounts, company):
    ledger(company, "サーバー,工具器具備品,2025-07-01,2025-07-01,400000,定率法,4,0,,")
    assert one(year_dir, accounts, company)["借方金額"] == "150000"  # 400,000 × 0.500 × 9/12


def test_declining_balance_switch_to_revised(year_dir, accounts, company):
    ledger(company, "サーバー,工具器具備品,2022-04-01,2022-04-01,400000,定率法,4,350000,,")
    # B=50,000：調整前 25,000 < 保証額 49,996 → 改定 50,000 × 1.000 だが上限（B−1）
    assert one(year_dir, accounts, company)["借方金額"] == "49999"


def test_declining_balance_keeps_revised_base_after_switch(year_dir, accounts, company):
    # 耐用年数10・1,000,000：7年目に調整前償却額が保証額 65,520 を下回り、改定取得価額は 262,144（7年目の期首帳簿価額）。
    # 8年目（今期）は期首帳簿価額 196,608 ではなく改定取得価額 × 0.250 ＝ 65,536 のまま
    ledger(company, "設備,工具器具備品,2018-04-01,2018-04-01,1000000,定率法,10,803392,,")
    assert one(year_dir, accounts, company)["借方金額"] == "65536"


def test_straight_line_final_year_keeps_one_yen(year_dir, accounts, company):
    ledger(company, "サーバー,工具器具備品,2021-04-01,2021-04-01,400000,定額法,4,370000,,")
    assert one(year_dir, accounts, company)["借方金額"] == "29999"  # 本来 100,000 だが 400,000−370,000−1


def test_fully_depreciated_asset_is_ignored(year_dir, accounts, company):
    ledger(company, "サーバー,工具器具備品,2021-04-01,2021-04-01,400000,定額法,4,399999,,")
    result = run(year_dir, accounts, company)
    assert staged(year_dir) == [] and result.notes == []


def test_lump_sum_three_years(year_dir, accounts, company):
    ledger(company, "PC一式,工具器具備品,2025-09-01,2025-09-01,150000,一括償却,,0,,")
    r = one(year_dir, accounts, company)  # 20万円未満の資産だけが対象（一括償却）
    assert (r["借方科目"], r["借方金額"], r["貸方科目"], r["貸方金額"]) == ("減価償却費", "50000", "工具器具備品", "50000")


def test_lump_sum_each_year_is_one_third(year_dir, accounts, company):
    for prior in ("0", "50000", "100000"):
        ledger(company, f"PC一式,工具器具備品,2023-09-01,2023-09-01,150000,一括償却,,{prior},,")
        run(year_dir, accounts, company)
        assert [r["借方金額"] for r in staged(year_dir)] == ["50000"]


def test_lump_sum_is_capped_at_the_remainder(year_dir, accounts, company):
    ledger(company, "PC一式,工具器具備品,2023-09-01,2023-09-01,150000,一括償却,,130000,,")
    assert one(year_dir, accounts, company)["借方金額"] == "20000"


def test_lump_sum_short_year_is_prorated(year_dir, accounts, company):
    (year_dir / "period.yaml").write_text('期首日: "2025-10-01"\n期末日: "2026-03-31"\n', encoding="utf-8")
    ledger(company, "PC一式,工具器具備品,2025-10-01,2025-10-01,150000,一括償却,,0,,")
    assert one(year_dir, accounts, company)["借方金額"] == "25000"  # 150,000 × 6/36


def test_lump_sum_ignores_disposal(year_dir, accounts, company):
    ledger(company, "PC一式,工具器具備品,2024-09-01,2024-09-01,150000,一括償却,,50000,2025-08-15,",
           "PC二式,工具器具備品,2023-09-01,2023-09-01,150000,一括償却,,50000,2025-01-15,")
    result = run(year_dir, accounts, company)
    assert [r["借方金額"] for r in staged(year_dir)] == ["50000", "50000"]
    assert not any("除却" in n.message for n in result.notes)


def test_declining_first_year_guarantee_test_uses_full_year(year_dir, accounts, company):
    # 調整前償却額は月割り前の1年分で保証額と比べ、選んだ額を月割りする
    for life, supply, expected in ((4, "2026-02-01", "83333"), (10, "2026-01-01", "50000"), (10, "2026-03-01", "16666")):
        ledger(company, f"設備,工具器具備品,{supply},{supply},1000000,定率法,{life},0,,")
        result = run(year_dir, accounts, company)
        assert [r["借方金額"] for r in staged(year_dir)] == [expected]
        assert not any("再現できない" in n.message for n in result.notes)


def test_declining_retired_after_two_months(year_dir, accounts, company):
    ledger(company, "設備,工具器具備品,2023-04-01,2023-04-01,1000000,定率法,10,360000,2025-05-15,")
    result = run(year_dir, accounts, company)
    assert [r["借方金額"] for r in staged(year_dir)] == ["21333"]  # B=640,000 → 128,000 × 2/12
    assert not any("再現できない" in n.message for n in result.notes)


def test_small_amount_special(year_dir, accounts, company):
    ledger(company, "ノートPC,工具器具備品,2025-09-01,2025-09-01,250000,少額特例,,0,,")
    r = one(year_dir, accounts, company)
    assert (r["借方科目"], r["借方金額"], r["貸方科目"], r["貸方金額"]) == ("減価償却費", "250000", "工具器具備品", "250000")


def test_small_amount_cap_noted(year_dir, accounts, company):
    rows = [f"PC{i},工具器具備品,2025-09-01,2025-09-01,299999,少額特例,,0," + "," for i in range(11)]
    ledger(company, *rows)
    result = run(year_dir, accounts, company)
    assert len(staged(year_dir)) == 11
    assert any("300万円" in n.message for n in result.notes)


def test_small_amount_under_cap_not_noted(year_dir, accounts, company):
    rows = [f"PC{i},工具器具備品,2025-09-01,2025-09-01,299999,少額特例,,0," + "," for i in range(10)]
    ledger(company, *rows)
    assert not any("300万円" in n.message for n in run(year_dir, accounts, company).notes)


def test_taxable_year_depreciation_side_is_outside_scope(year_dir, accounts, company):
    (year_dir / "period.yaml").write_text(
        '期首日: "2025-04-01"\n期末日: "2026-03-31"\n消費税:\n  区分: 課税\n  方式: 原則\n  計算: 割戻し\n  控除: 個別対応\n',
        encoding="utf-8")
    ledger(company, "サーバー,工具器具備品,2025-07-01,2025-07-01,400000,定額法,4,0,,")
    r = one(year_dir, accounts, company)
    assert (r["借方税区分"], r["貸方税区分"]) == ("対象外", "")


def test_asset_in_service_after_period_is_ignored(year_dir, accounts, company):
    ledger(company, "サーバー,工具器具備品,2026-03-20,2026-04-01,400000,定額法,4,0,,")
    result = run(year_dir, accounts, company)
    assert staged(year_dir) == [] and result.notes == []


def test_asset_retired_before_period_is_ignored(year_dir, accounts, company):
    ledger(company, "サーバー,工具器具備品,2021-04-01,2021-04-01,400000,定額法,4,200000,2025-03-31,")
    assert staged(year_dir) == [] and run(year_dir, accounts, company).notes == []


def test_asset_retired_in_period_depreciates_to_that_month(year_dir, accounts, company):
    ledger(company, "サーバー,工具器具備品,2021-04-01,2021-04-01,400000,定額法,4,200000,2025-08-15,")
    result = run(year_dir, accounts, company)
    (r,) = staged(year_dir)
    assert r["借方金額"] == "41666"  # 4〜8月＝5か月：400,000 × 0.250 × 5/12（円未満切り捨て）
    assert any("除却" in n.message for n in result.notes)


def test_short_fiscal_year_is_left_to_the_owner(year_dir, accounts, company):
    (year_dir / "period.yaml").write_text('期首日: "2025-10-01"\n期末日: "2026-03-31"\n', encoding="utf-8")
    ledger(company, "サーバー,工具器具備品,2024-04-01,2024-04-01,400000,定額法,4,100000,,",
           "設備,工具器具備品,2024-04-01,2024-04-01,400000,定率法,4,100000,,")
    result = run(year_dir, accounts, company)
    assert staged(year_dir) == []
    messages = [n.message for n in result.notes]
    assert ("事業年度が12か月未満のため、サーバーの償却は耐用年数省令4条の率で別に計算する（候補は作っていない）" in messages)
    assert any("設備" in m and "12か月未満" in m for m in messages)


def test_small_amount_after_supply_year_is_noted(year_dir, accounts, company):
    ledger(company, "ノートPC,工具器具備品,2024-09-01,2024-09-01,250000,少額特例,,100000,,")
    result = run(year_dir, accounts, company)
    assert staged(year_dir) == []
    assert any("少額減価償却資産の特例は事業供用年度だけ（ノートPC）" in n.message for n in result.notes)


def test_small_amount_requires_small_company_note(year_dir, accounts, company):
    ledger(company, "ノートPC,工具器具備品,2025-09-01,2025-09-01,250000,少額特例,,0,,")
    assert any("中小企業者等だけ" in n.message for n in run(year_dir, accounts, company).notes)


def test_small_amount_cap_is_prorated_in_short_year(year_dir, accounts, company):
    (year_dir / "period.yaml").write_text('期首日: "2025-10-01"\n期末日: "2026-03-31"\n', encoding="utf-8")
    rows = [f"PC{i},工具器具備品,2025-10-01,2025-10-01,299999,少額特例,,0,," for i in range(5)]
    ledger(company, *rows)
    assert not any("年間上限" in n.message for n in run(year_dir, accounts, company).notes)  # 1,499,995 ≤ 1,500,000
    ledger(company, *rows, "PC9,工具器具備品,2025-10-01,2025-10-01,299999,少額特例,,0,,")
    assert any("年間上限" in n.message for n in run(year_dir, accounts, company).notes)


def test_unregistered_asset_noted(year_dir, accounts, company):
    ledger(company, "サーバー,工具器具備品,2025-07-01,2025-07-01,400000,定額法,4,0,,")
    books(year_dir, [
        line("1", "2025-07-01", debit=("工具器具備品", "", 400000), credit=("普通預金", "サンプル銀行", 400000), 摘要="サーバー"),
        line("2", "2025-08-10", debit=("工具器具備品", "", 180000), credit=("普通預金", "サンプル銀行", 180000), 摘要="業務用プリンター"),
        line("3", "2025-09-10", debit=("消耗品費", "", 120000), credit=("普通預金", "サンプル銀行", 120000), 摘要="作業机"),
        line("4", "2025-09-11", debit=("消耗品費", "", 90000), credit=("普通預金", "サンプル銀行", 90000), 摘要="文具"),
    ])
    result = run(year_dir, accounts, company)
    messages = [n.message for n in result.notes]
    assert len(messages) == 2
    assert any("固定資産台帳への登録を検討" in m and "業務用プリンター" in m and "180,000円" in m for m in messages)
    assert any("作業机" in m for m in messages)


def test_unregistered_asset_noted_without_ledger(year_dir, accounts, company):
    books(year_dir, [line("1", "2025-08-10", debit=("工具器具備品", "", 180000), credit=("普通預金", "サンプル銀行", 180000),
                          摘要="業務用プリンター")])
    result = run(year_dir, accounts, company)
    assert staged(year_dir) == [] and len(result.notes) == 1


def test_no_company_dir_is_fine(year_dir, accounts):
    books(year_dir)
    result = close.run_close(year_dir, accounts, items=[close_depreciation], sources=SOURCES, company_dir=None)
    assert result.notes == [] and staged(year_dir) == []


@pytest.mark.parametrize("row, message", [
    ("サーバー,工具器具備品,2025-07-01,2025-07-01,400000,定額法,,0,,", "耐用年数"),
    ("サーバー,工具器具備品,2025-07-01,2025-07-01,400000,定額法,51,0,,", "耐用年数"),
    ("サーバー,工具器具備品,2025-07-01,2025-07-01,400000,定額法,abc,0,,", "耐用年数"),
    ("サーバー,工具器具備品,2025-07-01,2025-07-01,400000,逓減法,4,0,,", "償却方法"),
    ("サーバー,現金,2025-07-01,2025-07-01,400000,定額法,4,0,,", "固定資産"),
    ("サーバー,土地,2025-07-01,2025-07-01,400000,定額法,4,0,,", "固定資産"),
    ("サーバー,存在しない科目,2025-07-01,2025-07-01,400000,定額法,4,0,,", "科目"),
    ("サーバー,工具器具備品,2025-13-01,2025-07-01,400000,定額法,4,0,,", "取得日"),
    ("サーバー,工具器具備品,2025-07-01,2025-06-01,400000,定額法,4,0,,", "事業供用日"),
    ("サーバー,工具器具備品,2025-07-01,2025-07-01,0,定額法,4,0,,", "取得価額"),
    ("サーバー,工具器具備品,2025-07-01,2025-07-01,400000,定額法,4,400001,,", "前期末償却累計額"),
    ("サーバー,工具器具備品,2025-07-01,2025-07-01,400000,定額法,4,-1,,", "前期末償却累計額"),
    ("サーバー,工具器具備品,2025-07-01,2025-07-01,200000,一括償却,,0,,", "一括償却"),
    ("サーバー,工具器具備品,2025-07-01,2025-07-01,300000,少額特例,,0,,", "少額特例"),
    ("サーバー,工具器具備品,2025-07-01,2025-07-01,400000,定額法,4,0,2025-06-30,", "除却日"),
    (",工具器具備品,2025-07-01,2025-07-01,400000,定額法,4,0,,", "資産名"),
])
def test_invalid_ledger_rejected(year_dir, accounts, company, row, message):
    ledger(company, row)
    with pytest.raises(KessanError) as e:
        run(year_dir, accounts, company)
    assert message in str(e.value)
    assert staged(year_dir) == []


def test_invalid_ledger_lists_every_problem(year_dir, accounts, company):
    ledger(company,
           "A,工具器具備品,2025-07-01,2025-07-01,400000,定額法,,0,,",
           "B,工具器具備品,2025-07-01,2025-07-01,400000,逓減法,4,0,,",
           "A,工具器具備品,2025-07-01,2025-07-01,400000,定額法,4,0,,")
    with pytest.raises(KessanError) as e:
        run(year_dir, accounts, company)
    text = str(e.value)
    assert "2行目" in text and "3行目" in text and "重複" in text


def test_missing_ledger_column_rejected(year_dir, accounts, company):
    ledger(company, "サーバー,工具器具備品", header="資産名,科目\n")
    with pytest.raises(KessanError) as e:
        run(year_dir, accounts, company)
    assert "列" in str(e.value)


def test_ignored_useful_life_for_lump_sum(year_dir, accounts, company):
    ledger(company, "PC一式,工具器具備品,2025-09-01,2025-09-01,150000,一括償却,99,0,,")
    assert one(year_dir, accounts, company)["借方金額"] == "50000"


def test_rates_validation(tmp_path):
    bad = tmp_path / "rates.yaml"
    bad.write_text('根拠URL: x\n確認日: "2026-10-26"\n定額法:\n  2: "0.500"\n定率法:\n  2: {償却率: "1.000"}\n', encoding="utf-8")
    with pytest.raises(KessanError) as e:
        close_depreciation.load_rates(bad)
    assert "耐用年数" in str(e.value)


def test_duplicate_ledger_column_rejected(year_dir, accounts, company):
    ledger(company, "サーバー,工具器具備品,2025-07-01,2025-07-01,400000,定額法,4,0,,,", header=HEADER.rstrip() + ",備考" + chr(10))
    with pytest.raises(KessanError) as e:
        run(year_dir, accounts, company)
    assert "重複" in str(e.value)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-0.5", "1.5", "abc"])
def test_rates_reject_non_rates(tmp_path, value):
    text = (close_depreciation.RATES_PATH).read_text(encoding="utf-8").replace('4: "0.250"', f'4: "{value}"', 1)
    bad = tmp_path / "rates.yaml"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(KessanError) as e:
        close_depreciation.load_rates(bad)
    assert "耐用年数4年" in str(e.value)


def test_intangible_straight_line_credits_asset_directly(year_dir, accounts, company):
    ledger(company, "会計ソフト,ソフトウエア,2025-04-01,2025-04-01,600000,定額法,5,0,,")
    r = one(year_dir, accounts, company)  # 600,000 × 0.200
    assert (r["借方科目"], r["借方金額"], r["貸方科目"], r["貸方金額"]) == ("減価償却費", "120000", "ソフトウエア", "120000")


def test_small_amount_already_expensed_is_not_staged(year_dir, accounts, company):
    ledger(company, "ノートPC,工具器具備品,2025-09-01,2025-09-01,250000,少額特例,,0,,")
    books(year_dir, [line("1", "2025-09-01", debit=("消耗品費", "", 250000), credit=("普通預金", "サンプル銀行", 250000),
                          摘要="ノートPC")])
    (year_dir / "opening-balances.csv").write_text("科目,補助,残高\n", encoding="utf-8-sig")  # 資産の残高が無い
    result = run(year_dir, accounts, company)
    assert staged(year_dir) == []
    assert any("close:減価償却:ノートPC" in n.message and "工具器具備品" in n.message and "マイナス" in n.message
               for n in result.notes)
