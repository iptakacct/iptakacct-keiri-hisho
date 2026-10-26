"""決算整理：消費税・法人税等の候補。帳簿は 3a の test_warimodoshi_10_and_8 と同じ（概算納付税額 7,200円）。"""
import close
from common import JOURNAL_COLUMNS, read_rows, write_rows
from helpers import line

ID_TAX = "close:消費税:2026-03-31"
ID_CORP = "close:法人税等:2026-03-31"


def taxable(year_dir, method="原則"):
    body = f"消費税:\n  区分: 課税\n  方式: {method}\n  計算: 割戻し\n"
    if method == "原則":
        body += "  控除: 全額控除\n"
    (year_dir / "period.yaml").write_text('期首日: "2025-04-01"\n期末日: "2026-03-31"\n' + body, encoding="utf-8")


def books(year_dir, prepaid=0, tax_columns=True):
    t = tax_columns
    rows = [
        line("1", "2025-06-01", debit=("現金", "", 110000), credit=("売上高", "", 110000),
             貸方税区分="課税売上10%" if t else ""),
        line("2", "2025-06-02", debit=("消耗品費", "", 10800), credit=("現金", "", 10800),
             借方税区分="課税仕入8%軽" if t else "", 借方インボイス="適格" if t else ""),
        line("3", "2025-06-02", debit=("消耗品費", "", 22000), credit=("現金", "", 22000),
             借方税区分="課税仕入10%" if t else "", 借方インボイス="適格" if t else ""),
    ]
    if prepaid:
        rows.append(line("4", "2025-11-30", debit=("仮払税金", "", prepaid), credit=("現金", "", prepaid), 摘要="中間納付"))
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, rows)


def candidate_lines(year_dir, source_id):
    """staging の候補行を (科目, 補助, 借方金額, 貸方金額, 借方税区分, 貸方税区分) の形で（借方・貸方の両方を持つ行は2つに分けない）。"""
    out = []
    for r in read_rows(year_dir / "adjustments-staging.csv"):
        if r["取り込み元ID"] != source_id:
            continue
        assert r["伝票番号"].startswith("C") and r["日付"] == "2026-03-31"
        if r["借方科目"]:
            out.append(("借", r["借方科目"], r["借方補助"], int(r["借方金額"]), r["借方税区分"]))
        if r["貸方科目"]:
            out.append(("貸", r["貸方科目"], r["貸方補助"], int(r["貸方金額"]), r["貸方税区分"]))
    return out


def reason(year_dir, source_id):
    return next(r["要確認理由"] for r in read_rows(year_dir / "adjustments-staging.csv") if r["取り込み元ID"] == source_id)


def run(year_dir, accounts):
    return close.run_close(year_dir, accounts)


def test_consumption_tax_candidate(year_dir, accounts):
    taxable(year_dir)
    books(year_dir, prepaid=3000)
    run(year_dir, accounts)
    assert candidate_lines(year_dir, ID_TAX) == [
        ("借", "租税公課", "", 7200, "対象外"),
        ("貸", "仮払税金", "", 3000, ""),
        ("貸", "未払消費税等", "", 4200, ""),
    ]
    assert "概算（tax-summary）" in reason(year_dir, ID_TAX)


def test_consumption_tax_without_prepaid(year_dir, accounts):
    taxable(year_dir)
    books(year_dir)
    run(year_dir, accounts)
    assert candidate_lines(year_dir, ID_TAX) == [
        ("借", "租税公課", "", 7200, "対象外"),
        ("貸", "未払消費税等", "", 7200, ""),
    ]


def test_consumption_tax_refund(year_dir, accounts):
    taxable(year_dir)
    books(year_dir, prepaid=9000)
    run(year_dir, accounts)
    assert candidate_lines(year_dir, ID_TAX) == [
        ("借", "租税公課", "", 7200, "対象外"),
        ("貸", "仮払税金", "", 9000, ""),
        ("借", "未収入金", "消費税", 1800, ""),
    ]


def test_consumption_tax_override(year_dir, accounts):
    taxable(year_dir)
    books(year_dir, prepaid=3000)
    close.set_amount(year_dir, "消費税", 10000)
    run(year_dir, accounts)
    assert candidate_lines(year_dir, ID_TAX) == [
        ("借", "租税公課", "", 10000, "対象外"),
        ("貸", "仮払税金", "", 3000, ""),
        ("貸", "未払消費税等", "", 7000, ""),
    ]
    assert "上書き額" in reason(year_dir, ID_TAX)


def test_consumption_tax_zero_makes_no_candidate(year_dir, accounts):
    taxable(year_dir)
    books(year_dir)
    close.set_amount(year_dir, "消費税", 0)
    run(year_dir, accounts)
    assert candidate_lines(year_dir, ID_TAX) == []


def test_consumption_tax_not_for_kanni_and_exempt(year_dir, accounts):
    books(year_dir, tax_columns=False)  # conftest の年度は免税
    r = run(year_dir, accounts)
    assert candidate_lines(year_dir, ID_TAX) == []
    assert not any(n.item == "消費税" and "close-set-amount" in n.message for n in r.notes)

    taxable(year_dir, method="簡易")
    books(year_dir)
    r = run(year_dir, accounts)
    assert candidate_lines(year_dir, ID_TAX) == []
    assert any(n.item == "消費税" and "達人で計算した額を close-set-amount で入れる" in n.message for n in r.notes)

    close.set_amount(year_dir, "消費税", 5000)
    run(year_dir, accounts)
    assert candidate_lines(year_dir, ID_TAX) == [
        ("借", "租税公課", "", 5000, "対象外"),
        ("貸", "未払消費税等", "", 5000, ""),
    ]


def test_corporate_tax_only_with_override(year_dir, accounts):
    taxable(year_dir)
    books(year_dir)
    r = run(year_dir, accounts)
    assert candidate_lines(year_dir, ID_CORP) == []
    assert any(n.item == "法人税等" and "達人で計算した額（期末の未払額）を close-set-amount で入れる" in n.message
               for n in r.notes)

    close.set_amount(year_dir, "法人税等", 70000)
    run(year_dir, accounts)
    assert candidate_lines(year_dir, ID_CORP) == [
        ("借", "法人税等", "", 70000, "対象外"),
        ("貸", "未払法人税等", "", 70000, ""),
    ]
    assert "上書き額" in reason(year_dir, ID_CORP)


def test_corporate_tax_exempt_year_has_no_tax_columns(year_dir, accounts):
    books(year_dir, tax_columns=False)
    close.set_amount(year_dir, "法人税等", 70000)
    run(year_dir, accounts)
    assert candidate_lines(year_dir, ID_CORP) == [
        ("借", "法人税等", "", 70000, ""),
        ("貸", "未払法人税等", "", 70000, ""),
    ]


def test_tax_candidates_post_cleanly(year_dir, accounts):
    from check import has_ng, run_checks
    taxable(year_dir)
    books(year_dir, prepaid=3000)
    close.set_amount(year_dir, "法人税等", 70000)
    run(year_dir, accounts)
    close.approve_candidates(year_dir, [ID_TAX, ID_CORP])
    assert close.post_candidates(year_dir, accounts) == ["A1", "A2"]
    assert not has_ng(run_checks(year_dir, accounts))
    # 登録後に流し直しても、消費税の概算は変わらない（租税公課は対象外なので集計に入らない）
    r = run(year_dir, accounts)
    assert r.candidates_added == 0 and r.candidates_kept == 2
