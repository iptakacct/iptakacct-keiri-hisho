"""決算整理：未払・未収の候補。期間 2025-04-01〜2026-03-31、翌期 2026-04-01〜2027-03-31（架空のデータ）。"""
import pytest

import close
import close_payable
import close_tax
from common import EVIDENCE_COLUMNS, JOURNAL_COLUMNS, KessanError, STAGING_COLUMNS, init_year_dir, read_rows, write_rows
from evidence import STATE_PAYABLE, STATE_UNPAID
from helpers import evidence_row, line, staging_row

SOURCES = {"accounts": [{"id": "main", "科目": "普通預金", "補助": "サンプル銀行"}]}
ID_BANK = "bank:main:2026-04-30:1"
ID_BANK_IN = "bank:main:2026-04-20:2"


@pytest.fixture
def next_dir(tmp_path):
    d = tmp_path / "2027-03期"
    init_year_dir(d, "2026-04-01", "2027-03-31")
    return d


def books(year_dir, partner="テストシヨウジ", taxable=False):
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, [
        line("1", "2025-04-01", debit=("普通預金", "サンプル銀行", 1000000), credit=("資本金", "", 1000000)),
        line("2", "2025-09-01", debit=("普通預金", "サンプル銀行", 330000), credit=("売上高", "", 330000), 取引先=partner,
             貸方税区分="課税売上10%" if taxable else ""),
    ])


def evidence(year_dir, **values):
    base = dict(証憑ID="ev1", 種類="請求書", 日付="2026-03-25", 金額="55000", 取引先="テストシヨウジ", 内容="制作費",
                科目候補="外注費", 補助候補="", 状態=STATE_UNPAID, 支払期日="2026-04-30", 税率="10", 登録番号="T1234567890123")
    base.update(values)
    write_rows(year_dir / "evidence.csv", EVIDENCE_COLUMNS, [evidence_row(**base)])


def next_journal(next_dir, rows, name="journal.csv"):
    columns = JOURNAL_COLUMNS if name == "journal.csv" else STAGING_COLUMNS
    rows = [staging_row(**r) if name == "staging.csv" else {**dict.fromkeys(columns, ""), **r} for r in rows]
    write_rows(next_dir / name, columns, rows)


def payment_out(date="2026-04-30", amount=55000, memo="フリコミ テストシヨウジ", source=ID_BANK):
    return line("1", date, debit=("未払金", "", amount), credit=("普通預金", "サンプル銀行", amount), 摘要=memo,
                取り込み元ID=source)


def run(year_dir, accounts, next_dir=None, payees=None):
    return close.run_close(year_dir, accounts, items=[close_payable], next_year_dir=next_dir, sources=SOURCES, payees=payees)


def staged(year_dir, source_id):
    return [r for r in read_rows(year_dir / "adjustments-staging.csv") if r["取り込み元ID"] == source_id]


def test_unpaid_matched_next_year(year_dir, accounts, next_dir):
    books(year_dir)
    evidence(year_dir)
    next_journal(next_dir, [payment_out()])
    run(year_dir, accounts, next_dir)
    (r,) = staged(year_dir, "close:未払:ev1")
    assert (r["日付"], r["借方科目"], r["借方金額"], r["貸方科目"], r["貸方補助"], r["貸方金額"], r["取引先"]) == (
        "2026-03-31", "外注費", "55000", "未払金", "", "55000", "テストシヨウジ")
    assert "翌期の支払 2026-04-30 " + ID_BANK + " と一致" in r["要確認理由"]
    assert r["判定"] == "要確認" and r["承認"] == ""


def test_unpaid_without_next_year(year_dir, accounts):
    books(year_dir)
    evidence(year_dir)
    run(year_dir, accounts)
    (r,) = staged(year_dir, "close:未払:ev1")
    assert "翌期の支払が見つからない" in r["要確認理由"]


def test_unpaid_after_period_ignored(year_dir, accounts):
    books(year_dir)
    evidence(year_dir, 日付="2026-04-02")
    run(year_dir, accounts)
    assert staged(year_dir, "close:未払:ev1") == []


def test_unpaid_kanji_partner_via_payees(year_dir, accounts, next_dir):
    books(year_dir)
    evidence(year_dir, 取引先="テスト商事")
    next_journal(next_dir, [payment_out()])
    run(year_dir, accounts, next_dir)
    assert "翌期の支払が見つからない" in staged(year_dir, "close:未払:ev1")[0]["要確認理由"]
    run(year_dir, accounts, next_dir, payees={"テスト商事": ["テストシヨウジ"]})
    assert "と一致" in staged(year_dir, "close:未払:ev1")[0]["要確認理由"]


def test_unpaid_window_amount_and_multiple(year_dir, accounts, next_dir):
    books(year_dir)
    evidence(year_dir)
    next_journal(next_dir, [payment_out(date="2026-05-11"), payment_out(amount=54000)])  # 期日の11日後・額違い
    run(year_dir, accounts, next_dir)
    assert "翌期の支払が見つからない" in staged(year_dir, "close:未払:ev1")[0]["要確認理由"]
    next_journal(next_dir, [payment_out(date="2026-04-20", source="a"), payment_out(date="2026-05-10", source="b")])
    run(year_dir, accounts, next_dir)
    assert "翌期の支払の候補が複数" in staged(year_dir, "close:未払:ev1")[0]["要確認理由"]


def test_unpaid_without_due_date_uses_92_days_and_staging(year_dir, accounts, next_dir):
    books(year_dir)
    evidence(year_dir, 支払期日="")
    next_journal(next_dir, [], "journal.csv")
    next_journal(next_dir, [{**payment_out(date="2026-06-30"), "判定": "要確認"}], "staging.csv")  # 4/1から91日目
    run(year_dir, accounts, next_dir)
    assert "と一致" in staged(year_dir, "close:未払:ev1")[0]["要確認理由"]


def test_next_year_rows_need_payment_account_on_credit_side(year_dir, accounts, next_dir):
    books(year_dir)
    evidence(year_dir)
    # 入金側（借方が預金）や、預金が関係しない行は支払として数えない
    next_journal(next_dir, [
        line("1", "2026-04-30", debit=("普通預金", "サンプル銀行", 55000), credit=("売上高", "", 55000), 摘要="フリコミ テストシヨウジ"),
        line("2", "2026-04-30", debit=("未払金", "", 55000), credit=("未払費用", "", 55000), 摘要="フリコミ テストシヨウジ"),
    ])
    run(year_dir, accounts, next_dir)
    assert "翌期の支払が見つからない" in staged(year_dir, "close:未払:ev1")[0]["要確認理由"]


def test_next_year_dir_without_period_yaml(year_dir, accounts, tmp_path):
    books(year_dir)
    evidence(year_dir)
    empty = tmp_path / "nothing"
    empty.mkdir()
    with pytest.raises(KessanError, match="period.yaml"):
        run(year_dir, accounts, empty)


def test_unpaid_taxable_columns(year_dir, accounts):
    (year_dir / "period.yaml").write_text(
        '期首日: "2025-04-01"\n期末日: "2026-03-31"\n消費税:\n  区分: 課税\n  方式: 原則\n  計算: 割戻し\n  控除: 個別対応\n',
        encoding="utf-8")
    books(year_dir, taxable=True)
    evidence(year_dir, 税率="8")
    run(year_dir, accounts)
    (r,) = staged(year_dir, "close:未払:ev1")
    assert (r["借方税区分"], r["借方インボイス"], r["借方個別対応"], r["貸方税区分"]) == ("課税仕入8%軽", "適格", "共通", "")
    evidence(year_dir, 登録番号="")
    run(year_dir, accounts)
    (r,) = staged(year_dir, "close:未払:ev1")
    assert r["借方インボイス"] in ("経過80", "なし", "経過70", "経過50", "経過30", "少額特例")


def test_unpaid_exempt_year_has_no_tax_columns(year_dir, accounts):
    books(year_dir)
    evidence(year_dir)
    run(year_dir, accounts)
    (r,) = staged(year_dir, "close:未払:ev1")
    assert (r["借方税区分"], r["借方インボイス"], r["借方個別対応"]) == ("", "", "")


def test_unpaid_without_account_candidate_is_note(year_dir, accounts):
    books(year_dir)
    evidence(year_dir, 科目候補="")
    r = run(year_dir, accounts)
    assert staged(year_dir, "close:未払:ev1") == []
    assert any(n.item == "未払" and "ev1" in n.message for n in r.notes)


def test_receivable_from_next_year_income(year_dir, accounts, next_dir):
    books(year_dir)
    next_journal(next_dir, [
        line("1", "2026-04-20", debit=("普通預金", "サンプル銀行", 110000), credit=("売上高", "", 110000),
             摘要="フリコミ テストシヨウジ", 取り込み元ID=ID_BANK_IN),
        line("2", "2026-06-10", debit=("普通預金", "サンプル銀行", 5000), credit=("売上高", "", 5000),
             摘要="フリコミ テストシヨウジ", 取り込み元ID="bank:main:late"),  # 60日を超える
        line("3", "2026-04-21", debit=("普通預金", "サンプル銀行", 7000), credit=("売上高", "", 7000),
             摘要="フリコミ ベツノカイシヤ", 取り込み元ID="bank:main:other"),  # 当期の売上先ではない
    ])
    run(year_dir, accounts, next_dir)
    (r,) = staged(year_dir, f"close:未収:{ID_BANK_IN}")
    assert (r["日付"], r["借方科目"], r["借方金額"], r["貸方科目"], r["貸方金額"], r["取引先"]) == (
        "2026-03-31", "売掛金", "110000", "売上高", "110000", "テストシヨウジ")
    assert r["要確認理由"] == "当期分の売上か確認（翌期の入金から推定）"
    assert {x["取り込み元ID"] for x in read_rows(year_dir / "adjustments-staging.csv")} == {f"close:未収:{ID_BANK_IN}"}


def test_receivable_taxable_year_uses_taxable_sales(year_dir, accounts, next_dir):
    (year_dir / "period.yaml").write_text(
        '期首日: "2025-04-01"\n期末日: "2026-03-31"\n消費税:\n  区分: 課税\n  方式: 原則\n  計算: 割戻し\n  控除: 全額控除\n',
        encoding="utf-8")
    books(year_dir, taxable=True)
    next_journal(next_dir, [line("1", "2026-04-20", debit=("普通預金", "サンプル銀行", 110000), credit=("売上高", "", 110000),
                                 摘要="フリコミ テストシヨウジ", 取り込み元ID=ID_BANK_IN)])
    run(year_dir, accounts, next_dir)
    (r,) = staged(year_dir, f"close:未収:{ID_BANK_IN}")
    assert r["貸方税区分"] == "課税売上10%"


def test_evidence_state_updated_after_post(year_dir, accounts):
    books(year_dir)
    evidence(year_dir)
    run(year_dir, accounts)
    close.approve_candidates(year_dir, ["close:未払:ev1"])
    assert close.post_candidates(year_dir, accounts, items=[close_payable]) == ["A1"]
    (ev,) = read_rows(year_dir / "evidence.csv")
    assert (ev["状態"], ev["取り込み元ID"]) == (STATE_PAYABLE, "close:未払:ev1")
    close_payable.after_post(year_dir, ["close:未払:ev1", "close:未収:x"])  # 何度呼んでも同じ
    assert read_rows(year_dir / "evidence.csv") == [ev]
    run(year_dir, accounts)  # 計上済みの証憑からは候補が出ない
    assert staged(year_dir, "close:未払:ev1") == []


def test_default_items_order_puts_payable_before_tax():
    assert close.ITEMS.index(close_payable) < close.ITEMS.index(close_tax)
