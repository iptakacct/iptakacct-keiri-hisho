"""決算整理：前払の候補。期間 2025-04-01〜2026-03-31（架空のデータ）。"""
import pytest

import close
import close_prepaid
from common import JOURNAL_COLUMNS, KessanError, read_rows, write_rows
from helpers import line

SOURCES = {"accounts": [{"id": "main", "科目": "普通預金", "補助": "サンプル銀行"}]}


@pytest.fixture
def company(tmp_path):
    d = tmp_path / "company"
    d.mkdir()
    return d


def registry(company, text="prepaid:\n  - {名前: 損害保険, 科目: 保険料, 摘要キーワード: ソンポ, 月数: 12}\n"):
    (company / "kessan-prepaid.yaml").write_text(text, encoding="utf-8")


def books(year_dir, rows):
    base = [line("0", "2025-04-01", debit=("普通預金", "サンプル銀行", 5000000), credit=("資本金", "", 5000000))]
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, base + rows)


def payment(no, date, amount, memo, source, account="保険料", **extra):
    return line(no, date, debit=(account, "", amount), credit=("普通預金", "サンプル銀行", amount), 摘要=memo,
                取り込み元ID=source, **extra)


def opening(year_dir, rows):
    (year_dir / "opening-balances.csv").write_text(
        "科目,補助,残高\n" + "".join(f"{a},{b},{c}\n" for a, b, c in rows), encoding="utf-8-sig")


def run(year_dir, accounts, company):
    return close.run_close(year_dir, accounts, items=[close_prepaid], sources=SOURCES, company_dir=company)


def staged(year_dir):
    return read_rows(year_dir / "adjustments-staging.csv")


def test_prepaid_insurance(year_dir, accounts, company):
    registry(company)
    books(year_dir, [payment("1", "2025-10-01", 120000, "ソンポ ホケンリヨウ", "bank:main:1")])
    run(year_dir, accounts, company)
    (r,) = staged(year_dir)
    assert (r["取り込み元ID"], r["日付"], r["借方科目"], r["借方補助"], r["借方金額"], r["貸方科目"], r["貸方金額"]) == (
        "close:前払:bank:main:1", "2026-03-31", "前払費用", "損害保険", "60000", "保険料", "60000")
    assert (r["借方税区分"], r["貸方税区分"]) == ("", "")


def test_prepaid_taxable_year_is_out_of_scope(year_dir, accounts, company):
    (year_dir / "period.yaml").write_text(
        '期首日: "2025-04-01"\n期末日: "2026-03-31"\n消費税:\n  区分: 課税\n  方式: 原則\n  計算: 割戻し\n  控除: 個別対応\n',
        encoding="utf-8")
    registry(company)
    books(year_dir, [payment("1", "2025-10-01", 120000, "ソンポ", "bank:main:1", 借方税区分="非課税仕入")])
    run(year_dir, accounts, company)
    (r,) = staged(year_dir)
    assert (r["借方税区分"], r["貸方税区分"]) == ("対象外", "対象外")


def test_prepaid_zero_when_within_period(year_dir, accounts, company):
    registry(company)
    books(year_dir, [payment("1", "2025-04-01", 120000, "ソンポ", "bank:main:1")])  # 2025-04〜2026-03 で期末に終わる
    result = run(year_dir, accounts, company)
    assert staged(year_dir) == [] and result.notes == []


def test_prepaid_rounds_down(year_dir, accounts, company):
    registry(company)
    books(year_dir, [payment("1", "2025-12-15", 100000, "ソンポ", "bank:main:1")])  # 2025-12〜2026-11、後 8か月
    run(year_dir, accounts, company)
    (r,) = staged(year_dir)
    assert r["借方金額"] == "66666"


def test_adjustments_rows_are_not_payments(year_dir, accounts, company):
    registry(company)
    books(year_dir, [])
    write_rows(year_dir / "adjustments.csv", JOURNAL_COLUMNS, [
        line("A1", "2025-04-01", debit=("保険料", "", 30000), credit=("前払費用", "損害保険", 30000), 摘要="ソンポ",
             取り込み元ID="close:前払戻し:損害保険")])
    opening(year_dir, [("前払費用", "損害保険", 30000), ("資本金", "", 30000)])
    result = run(year_dir, accounts, company)
    # 登録済みの戻し（借方 保険料）を新しい支払として数え直さない：新規の候補は出ず、戻しは登録済みとして据え置き
    assert staged(year_dir) == [] and (result.candidates_added, result.candidates_kept) == (0, 1)


def test_opening_prepaid_reversed(year_dir, accounts, company):
    registry(company)
    books(year_dir, [])
    opening(year_dir, [("前払費用", "損害保険", 30000), ("資本金", "", 30000)])
    run(year_dir, accounts, company)
    (r,) = staged(year_dir)
    assert (r["取り込み元ID"], r["日付"], r["借方科目"], r["借方金額"], r["貸方科目"], r["貸方補助"], r["貸方金額"]) == (
        "close:前払戻し:損害保険", "2025-04-01", "保険料", "30000", "前払費用", "損害保険", "30000")


def test_opening_prepaid_without_registry_noted(year_dir, accounts, company):
    books(year_dir, [])
    opening(year_dir, [("前払費用", "損害保険", 30000), ("資本金", "", 30000)])
    result = run(year_dir, accounts, company)
    assert staged(year_dir) == []
    assert any("期首の前払費用（損害保険）の戻し先が kessan-prepaid.yaml に無い" in n.message for n in result.notes)


def test_large_unregistered_payment_noted(year_dir, accounts, company):
    books(year_dir, [payment("1", "2025-10-01", 60000, "ビルカンリ カイヒ", "bank:main:1", account="地代家賃"),
                     payment("2", "2025-11-01", 49999, "ツウシン", "bank:main:2", account="通信費")])
    result = run(year_dir, accounts, company)
    messages = [n.message for n in result.notes]
    assert messages == ["年払いなら kessan-prepaid.yaml に登録（2025-10-01 ビルカンリ カイヒ 60,000円）"]


def test_invalid_registry_lists_all_problems(year_dir, accounts, company):
    registry(company, "prepaid:\n"
                      "  - {名前: A, 科目: 売上高, 摘要キーワード: ソンポ}\n"
                      "  - {名前: A, 科目: 保険料, 摘要キーワード: ソ, 月数: 0}\n"
                      "  - {名前: C, 科目: 保険料}\n")
    books(year_dir, [])
    with pytest.raises(KessanError) as e:
        run(year_dir, accounts, company)
    text = str(e.value)
    for word in ("費用の科目ではありません", "重複", "短すぎ", "月数", "どちらかを書く"):
        assert word in text


def test_items_order():
    names = [m.__name__ for m in close.ITEMS]
    assert names.index("close_payable") < names.index("close_prepaid") < names.index("close_tax")


def test_ambiguous_and_negative_rows_noted(year_dir, accounts, company):
    registry(company, "prepaid:\n  - {名前: 損害保険, 科目: 保険料, 摘要キーワード: ソンポ}\n"
                      "  - {名前: 傷害保険, 科目: 保険料, 摘要キーワード: ジヨウガイ}\n")
    books(year_dir, [payment("1", "2025-10-01", 120000, "ソンポ ジヨウガイ", "bank:main:1"),
                     line("2", "2025-11-01", debit=("保険料", "", -1000), credit=("普通預金", "サンプル銀行", -1000), 摘要="ソンポ",
                          取り込み元ID="bank:main:2")])
    opening(year_dir, [("前払費用", "損害保険", -500), ("資本金", "", -500)])
    # マイナスの行は検算が先に止める形なので、検算を通さず collect を直接呼ぶ
    candidates, notes = close_prepaid.collect(close.build_context(year_dir, accounts, company_dir=company))
    assert candidates == []
    text = "\n".join(n.message for n in notes)
    assert "複数の項目に当たるため候補にしません" in text and "金額が正でない" in text and "マイナス" in text
