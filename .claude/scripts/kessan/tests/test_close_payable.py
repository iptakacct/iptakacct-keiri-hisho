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
    assert r["借方インボイス"] == "経過80"  # 2026-03-25 は経過措置80%の期間


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
    assert r["要確認理由"].startswith("当期分の売上か確認（翌期の入金から推定）。")
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


def test_unpaid_92_day_edge(year_dir, accounts, next_dir):
    books(year_dir)
    evidence(year_dir, 支払期日="")
    next_journal(next_dir, [payment_out(date="2026-07-01")])  # 期末日の92日後（含む）
    run(year_dir, accounts, next_dir)
    assert "と一致" in staged(year_dir, "close:未払:ev1")[0]["要確認理由"]
    next_journal(next_dir, [payment_out(date="2026-07-02")])  # 93日後（含まない）
    run(year_dir, accounts, next_dir)
    assert "翌期の支払が見つからない" in staged(year_dir, "close:未払:ev1")[0]["要確認理由"]


def test_unpaid_flags_payment_within_period(year_dir, accounts):
    books(year_dir)
    rows = read_rows(year_dir / "journal.csv")
    rows.append(line("3", "2026-03-28", debit=("外注費", "", 55000), credit=("普通預金", "サンプル銀行", 55000),
                     摘要="フリコミ テストシヨウジ", 取り込み元ID="bank:main:paid"))
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, rows)
    evidence(year_dir)
    run(year_dir, accounts)
    assert "当期中に同額の支払あり（2026-03-28 bank:main:paid）。二重計上の恐れ" in staged(year_dir, "close:未払:ev1")[0]["要確認理由"]
    evidence(year_dir, 日付="2026-03-29")  # 支払より後の証憑は対象外
    run(year_dir, accounts)
    assert "二重計上" not in staged(year_dir, "close:未払:ev1")[0]["要確認理由"]


def test_next_year_rows_deduplicated_by_source_id(year_dir, accounts, next_dir):
    books(year_dir)
    evidence(year_dir)
    next_journal(next_dir, [payment_out()])
    next_journal(next_dir, [{**payment_out(), "判定": "要確認"}], "staging.csv")  # 同じ行が staging にも残っている
    run(year_dir, accounts, next_dir)
    assert "と一致" in staged(year_dir, "close:未払:ev1")[0]["要確認理由"]


def income(date="2026-04-20", counter="売上高", memo="フリコミ テストシヨウジ", amount=110000):
    return line("1", date, debit=("普通預金", "サンプル銀行", amount), credit=(counter, "", amount), 摘要=memo,
                取り込み元ID=ID_BANK_IN)


def test_receivable_skips_collection_of_booked_receivable(year_dir, accounts, next_dir):
    books(year_dir)
    for counter in ("売掛金", "未収入金", "前受金"):
        next_journal(next_dir, [income(counter=counter)])
        run(year_dir, accounts, next_dir)
        assert staged(year_dir, f"close:未収:{ID_BANK_IN}") == [], counter


def test_receivable_not_proposed_when_explained_by_year_end_balance(year_dir, accounts, next_dir):
    books(year_dir)
    rows = read_rows(year_dir / "journal.csv")
    rows.append(line("3", "2026-03-10", debit=("売掛金", "", 110000), credit=("売上高", "", 110000), 取引先="テストシヨウジ"))
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, rows)
    next_journal(next_dir, [income()])
    r = run(year_dir, accounts, next_dir)
    assert staged(year_dir, f"close:未収:{ID_BANK_IN}") == []
    assert any("翌期の入金 2026-04-20 110,000円（テストシヨウジ）は期末の売掛金で説明できる可能性があるので候補にしない" in n.message
               for n in r.notes)
    next_journal(next_dir, [income(amount=110001)])  # 残高を超える入金は候補になる
    run(year_dir, accounts, next_dir)
    (r,) = staged(year_dir, f"close:未収:{ID_BANK_IN}")
    assert "期末の売掛金が110,000円あるので、一部は計上済みの可能性" in r["要確認理由"]


def test_receivable_balance_by_sub_account_and_opening(year_dir, accounts, next_dir):
    books(year_dir)
    write_rows(year_dir / "opening-balances.csv", ["科目", "補助", "残高"], [
        {"科目": "売掛金", "補助": "テストシヨウジ", "残高": "200000"}, {"科目": "資本金", "補助": "", "残高": "200000"}])
    next_journal(next_dir, [income()])
    r = run(year_dir, accounts, next_dir)
    assert staged(year_dir, f"close:未収:{ID_BANK_IN}") == []
    assert any(n.item == "未収" for n in r.notes)


def test_receivable_payees(year_dir, accounts, next_dir):
    books(year_dir, partner="テスト商事")
    next_journal(next_dir, [income()])
    run(year_dir, accounts, next_dir)
    assert staged(year_dir, f"close:未収:{ID_BANK_IN}") == []
    run(year_dir, accounts, next_dir, payees={"テスト商事": ["テストシヨウジ"]})
    (r,) = staged(year_dir, f"close:未収:{ID_BANK_IN}")
    assert r["取引先"] == "テスト商事"


def test_rejected_candidate_excluded_from_tax_estimate(year_dir, accounts):
    (year_dir / "period.yaml").write_text("\n".join([
        '期首日: "2025-04-01"', '期末日: "2026-03-31"', "消費税:", "  区分: 課税", "  方式: 原則", "  計算: 割戻し",
        "  控除: 全額控除", ""]), encoding="utf-8")
    books(year_dir, taxable=True)
    evidence(year_dir)

    def tax_amount():
        close.run_close(year_dir, accounts, items=close.ITEMS, sources=SOURCES)
        return next(int(r["借方金額"]) for r in staged(year_dir, "close:消費税:2026-03-31") if r["借方科目"] == "租税公課")

    with_unpaid = tax_amount()
    close.reject_candidates(year_dir, ["close:未払:ev1"], "計上しない")
    assert tax_amount() == with_unpaid + 5000  # 未払の候補（税込55,000＝税5,000）を除いた概算になる


TAXABLE_PERIOD = ('期首日: "2025-04-01"\n期末日: "2026-03-31"\n消費税:\n  区分: 課税\n  方式: 原則\n  計算: 割戻し\n'
                  '  控除: 全額控除\n')


def test_unpost_reverts_evidence_state(year_dir, accounts):
    books(year_dir)
    evidence(year_dir)
    run(year_dir, accounts)
    close.approve_candidates(year_dir, ["close:未払:ev1"])
    close.post_candidates(year_dir, accounts, items=[close_payable])
    assert close.unpost_candidates(year_dir, ["close:未払:ev1"], items=[close_payable]) == ["A1"]
    (ev,) = read_rows(year_dir / "evidence.csv")
    assert (ev["状態"], ev["取り込み元ID"]) == (STATE_UNPAID, "")
    close_payable.after_unpost(year_dir, ["close:未払:ev1"])  # 何度呼んでも同じ
    assert read_rows(year_dir / "evidence.csv") == [ev]
    run(year_dir, accounts)  # 未払の候補として出直す
    assert staged(year_dir, "close:未払:ev1")[0]["承認"] == ""


def test_unpaid_mixed_rate_evidence_is_note(year_dir, accounts):
    (year_dir / "period.yaml").write_text(TAXABLE_PERIOD, encoding="utf-8")
    books(year_dir, taxable=True)
    evidence(year_dir, 税率="混在", 証憑ファイル="inbox/請求書-1.pdf")
    r = run(year_dir, accounts)
    assert staged(year_dir, "close:未払:ev1") == []
    assert any("税率が混在する証憑（inbox/請求書-1.pdf）は resolve-evidence --as-new で仕訳にしてから split-tax で分ける"
               in n.message for n in r.notes)


def test_unpaid_before_period_start_is_note(year_dir, accounts):
    books(year_dir)
    evidence(year_dir, 日付="2025-03-20")
    r = run(year_dir, accounts)
    assert staged(year_dir, "close:未払:ev1") == []
    assert any(n.item == "未払" and "ev1" in n.message and "期首日より前" in n.message for n in r.notes)


def test_unpaid_pl_account_without_default_tax_is_not_staged(year_dir, accounts):
    (year_dir / "period.yaml").write_text(TAXABLE_PERIOD, encoding="utf-8")
    books(year_dir, taxable=True)
    evidence(year_dir, 科目候補="固定資産売却損")  # 既定税区分が無い費用の科目
    r = run(year_dir, accounts)
    assert staged(year_dir, "close:未払:ev1") == []
    assert any("close:未払:ev1" in n.message and "税区分が空" in n.message for n in r.notes)


def test_unpaid_reason_tells_to_fix_next_year_voucher(year_dir, accounts, next_dir):
    books(year_dir)
    evidence(year_dir)
    next_journal(next_dir, [line("1", "2026-04-30", debit=("外注費", "", 55000), credit=("普通預金", "サンプル銀行", 55000),
                                 摘要="フリコミ テストシヨウジ", 取り込み元ID=ID_BANK)])
    run(year_dir, accounts, next_dir)
    assert f"翌期の伝票 {ID_BANK} は登録後に 未払金 の消込に直す" in staged(year_dir, "close:未払:ev1")[0]["要確認理由"]
    next_journal(next_dir, [payment_out()])  # 既に未払金の消込になっていれば言わない
    run(year_dir, accounts, next_dir)
    assert "消込に直す" not in staged(year_dir, "close:未払:ev1")[0]["要確認理由"]


def test_receivable_reason_tells_to_fix_next_year_voucher(year_dir, accounts, next_dir):
    books(year_dir)
    next_journal(next_dir, [income()])
    run(year_dir, accounts, next_dir)
    assert f"翌期の伝票 {ID_BANK_IN} は登録後に 売掛金 の消込に直す" in staged(year_dir, f"close:未収:{ID_BANK_IN}")[0]["要確認理由"]


def test_receivable_next_year_fixed_before_posting_is_note(year_dir, accounts, next_dir):
    books(year_dir)
    next_journal(next_dir, [income()])
    run(year_dir, accounts, next_dir)
    assert staged(year_dir, f"close:未収:{ID_BANK_IN}")
    next_journal(next_dir, [income(counter="売掛金")])  # 当期で登録する前に、翌期を売掛金の回収に直してしまった
    r = run(year_dir, accounts, next_dir)
    assert staged(year_dir, f"close:未収:{ID_BANK_IN}") == []
    assert any(n.item == "未収" and f"close:未収:{ID_BANK_IN}" in n.message and "売掛金" in n.message for n in r.notes)


def test_receivable_no_misleading_note_after_posting(year_dir, accounts, next_dir):
    books(year_dir)
    next_journal(next_dir, [income()])
    run(year_dir, accounts, next_dir)
    close.approve_candidates(year_dir, [f"close:未収:{ID_BANK_IN}"])
    close.post_candidates(year_dir, accounts, items=[close_payable])
    r = run(year_dir, accounts, next_dir)  # 翌期はまだ売上高のまま
    assert not any("期末の売掛金で説明できる" in n.message for n in r.notes)
    assert r.candidates_kept == 1


def taxable_sales(year_dir, taxes):
    rows = [line("1", "2025-04-01", debit=("普通預金", "サンプル銀行", 1000000), credit=("資本金", "", 1000000))]
    for i, tax in enumerate(taxes, start=2):
        rows.append(line(str(i), "2025-09-01", debit=("普通預金", "サンプル銀行", 1000), credit=("売上高", "", 1000),
                         取引先="テストシヨウジ", 貸方税区分=tax))
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, rows)


def test_receivable_tax_follows_partner_sales(year_dir, accounts, next_dir):
    (year_dir / "period.yaml").write_text(TAXABLE_PERIOD, encoding="utf-8")
    taxable_sales(year_dir, ["課税売上8%軽", "課税売上8%軽"])
    next_journal(next_dir, [income()])
    r = run(year_dir, accounts, next_dir)
    assert staged(year_dir, f"close:未収:{ID_BANK_IN}")[0]["貸方税区分"] == "課税売上8%軽"
    assert not any("課税売上10%" in n.message for n in r.notes)
    taxable_sales(year_dir, ["課税売上8%軽", "非課税売上"])
    r = run(year_dir, accounts, next_dir)
    assert staged(year_dir, f"close:未収:{ID_BANK_IN}")[0]["貸方税区分"] == "課税売上10%"
    assert any(n.item == "未収" and "課税売上10%" in n.message for n in r.notes)


def test_report_has_tax_columns(year_dir, accounts):
    (year_dir / "period.yaml").write_text(TAXABLE_PERIOD, encoding="utf-8")
    books(year_dir, taxable=True)
    evidence(year_dir)
    r = run(year_dir, accounts)
    assert "| 外注費 | 課税仕入10% | 未払金 |  |" in r.report_path.read_text(encoding="utf-8")
