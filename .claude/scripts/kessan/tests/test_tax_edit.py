import pytest

from accounts_update import approve
from check import run_checks
from common import JOURNAL_COLUMNS, STAGING_COLUMNS, KessanError, read_rows, write_rows
from helpers import line, staging_row
from post import post_approved
from tax_edit import set_journal_tax, split_tax
from tax_fill import MIXED_RATE_REASON

CASH = {("現金", "")}


def taxable(year_dir):
    (year_dir / "period.yaml").write_text(
        '期首日: "2025-04-01"\n期末日: "2026-03-31"\n'
        "消費税:\n  区分: 課税\n  方式: 原則\n  計算: 割戻し\n  控除: 全額控除\n",
        encoding="utf-8")


def write_journal(year_dir, rows):
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, rows)


def two_line_voucher():
    return [
        line("12", "2025-04-05", debit=("消耗品費", "", 1000), credit=("未払金", "", 2000), 取り込み元ID="manual:x",
             借方税区分="課税仕入10%", 借方インボイス="適格"),
        line("12", "2025-04-05", debit=("支払手数料", "", 1000), 取り込み元ID="manual:x",
             借方税区分="課税仕入10%", 借方インボイス="適格"),
    ]


def test_set_tax_updates_journal_rows(year_dir):
    write_journal(year_dir, two_line_voucher())
    n = set_journal_tax(year_dir, [{"伝票番号": "12", "行": 2, "借方税区分": "課税仕入8%軽", "借方インボイス": "経過80"}])
    assert n == 1
    first, second = read_rows(year_dir / "journal.csv")
    assert (first["借方税区分"], first["借方インボイス"]) == ("課税仕入10%", "適格")
    assert (second["借方税区分"], second["借方インボイス"]) == ("課税仕入8%軽", "経過80")
    skip = ("借方税区分", "借方インボイス")
    assert {k: v for k, v in second.items() if k not in skip} == {
        k: v for k, v in two_line_voucher()[1].items() if k not in skip}


def test_set_tax_updates_adjustments(year_dir):
    write_rows(year_dir / "adjustments.csv", JOURNAL_COLUMNS,
               [line("A1", "2026-03-31", debit=("減価償却費", "", 100), credit=("未払金", "", 100))])
    set_journal_tax(year_dir, [{"伝票番号": "A1", "行": 1, "借方税区分": "対象外"}])
    assert read_rows(year_dir / "adjustments.csv")[0]["借方税区分"] == "対象外"


def test_set_tax_rejects_non_tax_column(year_dir):
    write_journal(year_dir, two_line_voucher())
    before = (year_dir / "journal.csv").read_bytes()
    with pytest.raises(KessanError, match="書き込めない列 借方金額"):
        set_journal_tax(year_dir, [{"伝票番号": "12", "行": 1, "借方金額": "5"}])
    assert (year_dir / "journal.csv").read_bytes() == before


@pytest.mark.parametrize("update, message", [
    ({"伝票番号": "99", "行": 1, "借方税区分": ""}, "伝票99: journal.csv・adjustments.csv にありません"),
    ({"伝票番号": "12", "行": 3, "借方税区分": ""}, "伝票12: 行3 は範囲外"),
    ({"伝票番号": "12", "行": 0, "借方税区分": ""}, "行は1以上の整数"),
    ({"伝票番号": "12", "行": 1, "借方税区分": "謎"}, "借方税区分「謎」は使えない値"),
    ({"伝票番号": "12", "行": 1}, "直す列がありません"),
])
def test_set_tax_collects_errors_and_writes_nothing(year_dir, update, message):
    write_journal(year_dir, two_line_voucher())
    before = (year_dir / "journal.csv").read_bytes()
    good = {"伝票番号": "12", "行": 2, "借方税区分": "対象外"}
    with pytest.raises(KessanError, match=message):
        set_journal_tax(year_dir, [good, update])
    assert (year_dir / "journal.csv").read_bytes() == before


def test_set_tax_rejects_duplicate_entries(year_dir):
    write_journal(year_dir, two_line_voucher())
    u = {"伝票番号": "12", "行": 1, "借方税区分": "対象外"}
    with pytest.raises(KessanError, match="伝票12 行1 が重複"):
        set_journal_tax(year_dir, [u, dict(u)])


# --- split-tax ---

def shopping(**extra):
    values = dict(日付="2025-04-10", 借方科目="消耗品費", 借方金額="1620", 貸方科目="現金", 貸方金額="1620",
                  摘要="スーパー", 取引先="テスト商店", 取り込み元ID="rcpt:1", 読み取り信頼度="高",
                  判定="要確認", 要確認理由=MIXED_RATE_REASON + "／別の理由", 借方インボイス="適格")
    values.update(extra)
    return staging_row(**values)


def write_staging(year_dir, rows):
    write_rows(year_dir / "staging.csv", STAGING_COLUMNS, rows)


def test_split_tax_makes_compound_voucher(year_dir, accounts):
    taxable(year_dir)
    write_staging(year_dir, [staging_row(取り込み元ID="a"), shopping(), staging_row(取り込み元ID="z")])
    no = split_tax(year_dir, "rcpt:1", [(10, 1080), (8, 540)], CASH, accounts)
    assert no == "S1"
    rows = read_rows(year_dir / "staging.csv")
    assert [r["取り込み元ID"] for r in rows] == ["a", "rcpt:1", "rcpt:1", "rcpt:1", "z"]
    ten, eight, cash = rows[1:4]
    assert {r["伝票番号"] for r in rows[1:4]} == {"S1"}
    assert (ten["借方科目"], ten["借方金額"], ten["借方税区分"], ten["貸方金額"], ten["貸方科目"]) == (
        "消耗品費", "1080", "課税仕入10%", "", "")
    assert (eight["借方金額"], eight["借方税区分"], eight["借方インボイス"]) == ("540", "課税仕入8%軽", "適格")
    assert (cash["貸方科目"], cash["貸方金額"], cash["借方科目"], cash["借方金額"], cash["借方税区分"]) == (
        "現金", "1620", "", "", "")
    for r in rows[1:4]:
        assert (r["日付"], r["摘要"], r["取引先"], r["要確認理由"], r["承認"]) == (
            "2025-04-10", "スーパー", "テスト商店", "別の理由", "")
    assert rows[0]["伝票番号"] == rows[4]["伝票番号"] == ""


def test_split_tax_picks_unused_voucher_number(year_dir, accounts):
    taxable(year_dir)
    write_staging(year_dir, [shopping(取り込み元ID="r1", 伝票番号="S1"), shopping(取り込み元ID="r2")])
    assert split_tax(year_dir, "r2", [(10, 1000), (8, 620)], CASH, accounts) == "S2"


def test_split_tax_uses_current_category_when_set(year_dir, accounts):
    taxable(year_dir)
    write_staging(year_dir, [shopping(借方科目="会議費", 借方税区分="課税仕入8%軽")])
    split_tax(year_dir, "rcpt:1", [(10, 1000), (8, 620)], CASH, accounts)
    rows = read_rows(year_dir / "staging.csv")
    assert [r["借方税区分"] for r in rows[:2]] == ["課税仕入10%", "課税仕入8%軽"]


def test_split_tax_credit_side_other(year_dir, accounts):
    """明細側が借方（入金）のとき、相手側は貸方。"""
    taxable(year_dir)
    write_staging(year_dir, [staging_row(日付="2025-04-10", 借方科目="現金", 借方金額="2200", 貸方科目="売上高",
                                         貸方金額="2200", 取り込み元ID="s1", 貸方税区分="課税売上10%")])
    split_tax(year_dir, "s1", [(10, 1100), (8, 1100)], CASH, accounts)
    rows = read_rows(year_dir / "staging.csv")
    assert [(r["貸方科目"], r["貸方金額"], r["貸方税区分"]) for r in rows[:2]] == [
        ("売上高", "1100", "課税売上10%"), ("売上高", "1100", "課税売上8%軽")]
    assert (rows[2]["借方科目"], rows[2]["借方金額"]) == ("現金", "2200")


def test_split_tax_menzei_year_keeps_tax_columns_empty(year_dir, accounts):
    write_staging(year_dir, [shopping(借方インボイス="")])
    split_tax(year_dir, "rcpt:1", [(10, 1080), (8, 540)], CASH, accounts)
    assert {r["借方税区分"] for r in read_rows(year_dir / "staging.csv")} == {""}


@pytest.mark.parametrize("parts, message", [
    ([(10, 1000), (8, 500)], "合計1500が相手側の金額1620と一致しません"),
    ([(10, 1620)], "2つ以上"),
    ([(5, 1000), (8, 620)], "税率は10か8"),
    ([(10, 0), (8, 1620)], "金額は1以上の整数"),
    ([(10, -5), (8, 1625)], "金額は1以上の整数"),
])
def test_split_tax_amount_mismatch(year_dir, accounts, parts, message):
    taxable(year_dir)
    write_staging(year_dir, [shopping()])
    before = (year_dir / "staging.csv").read_bytes()
    with pytest.raises(KessanError, match=message):
        split_tax(year_dir, "rcpt:1", parts, CASH, accounts)
    assert (year_dir / "staging.csv").read_bytes() == before


@pytest.mark.parametrize("rows, source_id, message", [
    ([shopping()], "none", "staging.csv にありません"),
    ([shopping(), shopping()], "rcpt:1", "2行あります"),
    ([shopping(承認="済")], "rcpt:1", "承認済み"),
    ([shopping(伝票番号="S1")], "rcpt:1", "既に伝票番号"),
    ([shopping(借方科目="消耗品費", 貸方科目="未払金")], "rcpt:1", "明細側"),
    ([shopping(借方科目="現金", 借方補助="")], "rcpt:1", "明細側"),
])
def test_split_tax_rejects_bad_rows(year_dir, accounts, rows, source_id, message):
    taxable(year_dir)
    write_staging(year_dir, rows)
    with pytest.raises(KessanError, match=message):
        split_tax(year_dir, source_id, [(10, 1000), (8, 620)], CASH, accounts)


def test_split_then_post(year_dir, accounts):
    taxable(year_dir)
    write_staging(year_dir, [shopping(日付="2025-04-10")])
    split_tax(year_dir, "rcpt:1", [(10, 1080), (8, 540)], CASH, accounts)
    approve(year_dir, accounts, ["rcpt:1"])
    r = post_approved(year_dir, accounts)
    assert len(r.vouchers) == 1
    rows = read_rows(year_dir / "journal.csv")
    assert len(rows) == 3 and {x["伝票番号"] for x in rows} == {r.vouchers[0]}
    ng = [f for f in run_checks(year_dir, accounts) if f.level == "NG" and f.item in ("貸借一致", "税区分")]
    assert ng == []


def test_set_tax_rejects_voucher_in_both_files(year_dir):
    write_journal(year_dir, two_line_voucher())
    write_rows(year_dir / "adjustments.csv", JOURNAL_COLUMNS, [line("12", "2026-03-31", debit=("雑費", "", 1), credit=("現金", "", 1))])
    with pytest.raises(KessanError, match="両方にあり"):
        set_journal_tax(year_dir, [{"伝票番号": "12", "行": 1, "借方税区分": "対象外"}])
