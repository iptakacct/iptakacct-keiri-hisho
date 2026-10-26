"""決算整理の通しテスト：候補 → 金額の上書き → 承認 → 登録 → 検算。架空の課税（原則・割戻し・全額控除）の会社。

期間 2025-04-01〜2026-03-31、翌期 2026-04-01〜2027-03-31。手計算した正解:

  未払（close:未払:ev1）    2026-03-25 付の請求書 55,000円（外注費・課税仕入10%・適格）が翌期 4/30 に支払われる
                            → 借方 外注費 55,000／貸方 未払金 55,000
  未収（close:未収:…）      翌期 4/20 の入金 110,000円（売上先に合う）→ 借方 売掛金 110,000／貸方 売上高 110,000（課税売上10%）
  前払（close:前払:…）      2025-07-01 に年払いの保険料 24,000円（12か月）。役務は 7月〜翌年6月、期末（3月）より後は 4〜6月の3か月
                            → 24,000 × 3 ÷ 12 ＝ 6,000円（借方 前払費用（火災保険）／貸方 保険料）
  前払戻し（close:前払戻し:火災保険） 期首の前払費用（火災保険）12,000円 → 借方 保険料／貸方 前払費用（火災保険）12,000円
  減価償却（サーバー）      取得価額 440,000円・定額法・耐用年数4年（償却率 0.250）・2025-10-01 供用（10月〜3月＝6か月）
                            → floor(440,000 × 0.250 × 6 ÷ 12) ＝ 55,000円（借方 減価償却費／貸方 減価償却累計額）
  減価償却（PC一式）        取得価額 150,000円・一括償却（取得月に関わらず当期の月数12か月で3年均等）
                            → floor(150,000 × 12 ÷ 36) ＝ 50,000円（借方 減価償却費／貸方 工具器具備品）
  消費税                    課税売上10%: 帳簿 1,100,000 ＋ 未収の候補 110,000 ＝ 1,210,000円 → 税 floor(1,210,000 × 10 ÷ 110) ＝ 110,000
                            課税仕入10%・適格: サーバー 440,000 ＋ PC 150,000 ＋ 未払の候補 55,000 ＝ 645,000円
                              → 税 floor(645,000 × 10 ÷ 110) ＝ floor(58,636.36) ＝ 58,636（全額控除なので控除も 58,636）
                            （保険料は非課税仕入、役員報酬・減価償却費・前払・戻しは対象外なので入らない）
                            年税額 110,000 − 58,636 ＝ 51,364円。中間納付（仮払税金）20,000円を引いて 未払消費税等 31,364円
                            → 借方 租税公課 51,364／貸方 仮払税金 20,000・未払消費税等 31,364
  法人税等                  close-set-amount 法人税等 70,000（期末の未払額。中間納付なし）
                            → 借方 法人税等 70,000／貸方 未払法人税等 70,000
  所見（候補にならない）    役員報酬は 4〜9月 100,000円・10〜3月 120,000円 → 月額が一定でない。仮払金 30,000円（田中）が残る。
                            役員借入金 500,000円 → 1,500,000円（100万円の借入）。役員貸付金 200,000円が残る（認定利息の確認）
"""
import pytest

import close
from check import has_ng, run_checks
from cli import main
from common import EVIDENCE_COLUMNS, JOURNAL_COLUMNS, read_rows, write_rows
from evidence import STATE_PAYABLE, STATE_UNPAID
from helpers import evidence_row, line, write_sources

BANK = ("普通預金", "サンプル銀行")
ID_PREPAID = "bank:main:2025-07-01:1"
ID_PAID = "bank:main:2026-04-30:1"
ID_INCOME = "bank:main:2026-04-20:2"
TAX_ARGS = ["--consumption-tax", "課税", "--tax-method", "原則", "--tax-calc", "割戻し", "--tax-allocation", "全額控除"]

REGISTRY = """\
prepaid:
  - 名前: 火災保険
    科目: 保険料
    摘要キーワード: 火災保険
    月数: 12
"""
LEDGER = (
    "資産名,科目,取得日,事業供用日,取得価額,償却方法,耐用年数,前期末償却累計額,除却日\n"
    "サーバー,工具器具備品,2025-10-01,2025-10-01,440000,定額法,4,0,\n"
    "PC一式,工具器具備品,2025-09-01,2025-09-01,150000,一括償却,,0,\n"
)


def journal_rows():
    rows = [
        line("1", "2025-06-01", debit=BANK + (1100000,), credit=("売上高", "", 1100000), 貸方税区分="課税売上10%",
             取引先="テストシヨウジ", 摘要="フリコミ テストシヨウジ"),
        line("2", "2025-07-01", debit=("保険料", "", 24000), credit=BANK + (24000,), 借方税区分="非課税仕入",
             摘要="火災保険料 年払い", 取り込み元ID=ID_PREPAID),
        line("3", "2025-09-01", debit=("工具器具備品", "", 150000), credit=BANK + (150000,), 借方税区分="課税仕入10%",
             借方インボイス="適格", 摘要="PC一式"),
        line("4", "2025-10-01", debit=("工具器具備品", "", 440000), credit=BANK + (440000,), 借方税区分="課税仕入10%",
             借方インボイス="適格", 摘要="サーバー"),
        line("5", "2025-11-30", debit=("仮払税金", "", 20000), credit=BANK + (20000,), 摘要="消費税の中間納付"),
        line("6", "2025-12-01", debit=BANK + (1000000,), credit=("役員借入金", "", 1000000), 摘要="役員からの借入"),
        line("7", "2025-08-01", debit=("役員貸付金", "", 200000), credit=BANK + (200000,), 摘要="役員への貸付"),
        line("8", "2026-03-15", debit=("仮払金", "田中", 30000), credit=BANK + (30000,), 摘要="出張の仮払"),
    ]
    for i in range(12):  # 役員報酬：4〜9月 100,000円、10〜3月 120,000円
        year, month = (2025, 4 + i) if i < 9 else (2026, i - 8)
        amount = 100000 if i < 6 else 120000
        rows.append(line(str(9 + i), f"{year}-{month:02d}-25", debit=("役員報酬", "", amount), credit=BANK + (amount,),
                         借方税区分="対象外", 摘要="役員報酬"))
    return rows


@pytest.fixture
def company_books(tmp_path):
    root = tmp_path / "インスタンス"
    year = root / "work" / "kessan" / "2026-03期"
    nxt = root / "work" / "kessan" / "2027-03期"
    company = root / "context" / "company"
    company.mkdir(parents=True)
    assert main(["init", "--year-dir", str(year), "--start", "2025-04-01", "--end", "2026-03-31", *TAX_ARGS]) == 0
    assert main(["init", "--year-dir", str(nxt), "--start", "2026-04-01", "--end", "2027-03-31"]) == 0
    write_sources(company)
    (company / "kessan-prepaid.yaml").write_text(REGISTRY, encoding="utf-8")
    (company / "fixed-assets.csv").write_text(LEDGER, encoding="utf-8-sig")

    opening = [("普通預金", "サンプル銀行", 1000000), ("前払費用", "火災保険", 12000), ("役員借入金", "", 500000),
               ("資本金", "", 512000)]
    write_rows(year / "opening-balances.csv", ["科目", "補助", "残高"],
               [{"科目": n, "補助": s, "残高": str(a)} for n, s, a in opening])
    write_rows(year / "journal.csv", JOURNAL_COLUMNS, journal_rows())
    write_rows(year / "evidence.csv", EVIDENCE_COLUMNS, [evidence_row(
        証憑ID="ev1", 種類="請求書", 日付="2026-03-25", 金額="55000", 取引先="テストシヨウジ", 内容="制作費",
        科目候補="外注費", 状態=STATE_UNPAID, 支払期日="2026-04-30", 税率="10", 登録番号="T1234567890123")])
    write_rows(nxt / "journal.csv", JOURNAL_COLUMNS, [
        line("1", "2026-04-30", debit=("未払金", "", 55000), credit=BANK + (55000,), 摘要="フリコミ テストシヨウジ",
             取り込み元ID=ID_PAID),
        line("2", "2026-04-20", debit=BANK + (110000,), credit=("売上高", "", 110000), 摘要="フリコミ テストシヨウジ",
             取り込み元ID=ID_INCOME),
    ])
    return year, nxt


def staged(year):
    """staging の候補を 取り込み元ID → [(借貸, 科目, 補助, 金額, 税区分)] にする。"""
    out = {}
    for r in read_rows(year / "adjustments-staging.csv"):
        rows = out.setdefault(r["取り込み元ID"], [])
        if r["借方科目"]:
            rows.append(("借", r["借方科目"], r["借方補助"], int(r["借方金額"]), r["借方税区分"]))
        if r["貸方科目"]:
            rows.append(("貸", r["貸方科目"], r["貸方補助"], int(r["貸方金額"]), r["貸方税区分"]))
    return out


def test_closing_flow(company_books, accounts, capsys):
    year, nxt = company_books
    assert not has_ng(run_checks(year, accounts))  # 前提：帳簿そのものは検算を通る

    assert main(["close-set-amount", "--year-dir", str(year), "--item", "法人税等", "--amount", "70000"]) == 0
    assert main(["close-candidates", "--year-dir", str(year), "--next-year-dir", str(nxt)]) == 0
    out = capsys.readouterr().out
    assert "所見［役員報酬］: 役員報酬の月額が一定でない（2025-04: 100,000円" in out
    assert "所見［残高］: 仮払金が残っている（田中: 30,000円）" in out
    assert "所見［残高］: 役員借入金が 500,000円 → 1,500,000円" in out
    assert "所見［残高］: 役員貸付金があるので認定利息の要否を確認" in out

    cands = staged(year)
    assert cands == {
        "close:未払:ev1": [("借", "外注費", "", 55000, "課税仕入10%"), ("貸", "未払金", "", 55000, "")],
        f"close:未収:{ID_INCOME}": [("借", "売掛金", "", 110000, ""), ("貸", "売上高", "", 110000, "課税売上10%")],
        f"close:前払:{ID_PREPAID}": [("借", "前払費用", "火災保険", 6000, "対象外"), ("貸", "保険料", "", 6000, "対象外")],
        "close:前払戻し:火災保険": [("借", "保険料", "", 12000, "対象外"), ("貸", "前払費用", "火災保険", 12000, "対象外")],
        "close:減価償却:サーバー": [("借", "減価償却費", "", 55000, "対象外"), ("貸", "減価償却累計額", "", 55000, "")],
        "close:減価償却:PC一式": [("借", "減価償却費", "", 50000, "対象外"), ("貸", "工具器具備品", "", 50000, "")],
        "close:消費税:2026-03-31": [("借", "租税公課", "", 51364, "対象外"), ("貸", "仮払税金", "", 20000, ""),
                                 ("貸", "未払消費税等", "", 31364, "")],
        "close:法人税等:2026-03-31": [("借", "法人税等", "", 70000, "対象外"), ("貸", "未払法人税等", "", 70000, "")],
    }
    assert all(r["判定"] == "要確認" and r["承認"] == "" for r in read_rows(year / "adjustments-staging.csv"))
    assert read_rows(year / "adjustments.csv") == []  # 承認するまで帳簿には入らない

    assert main(["close-approve", "--year-dir", str(year), "--all"]) == 0
    # 消費税は概算なので --all では残る。この通しテストでは概算のまま明示して承認する
    assert main(["close-approve", "--year-dir", str(year), "--ids", "close:消費税:2026-03-31"]) == 0
    capsys.readouterr()
    assert main(["close-post", "--year-dir", str(year)]) == 0
    assert "決算整理仕訳の登録: 8件（A1, A2, A3, A4, A5, A6, A7, A8）" in capsys.readouterr().out

    posted = read_rows(year / "adjustments.csv")
    assert {r["伝票番号"] for r in posted} == {f"A{i}" for i in range(1, 9)}
    assert all(r["登録区分"] == "決算整理" for r in posted)
    assert read_rows(year / "adjustments-staging.csv") == []
    (ev,) = read_rows(year / "evidence.csv")
    assert (ev["状態"], ev["取り込み元ID"]) == (STATE_PAYABLE, "close:未払:ev1")
    assert not has_ng(run_checks(year, accounts))

    # 登録後に流し直しても候補は増えず、消費税の額も変わらない（登録済みの内容と再計算が一致する）。
    # 未払は証憑が未払計上になったので出なくなる。未収は翌期がまだ売上高のままなので登録済みとして据え置き
    # （登録済みの未収を「期末の売掛金で説明できる」と数え直さない）
    assert main(["close-candidates", "--year-dir", str(year), "--next-year-dir", str(nxt)]) == 0
    again = capsys.readouterr().out
    assert "要確認 0件 / 承認済み・登録済みのため据え置き 7件" in again
    assert "期末の売掛金で説明できる" not in again
    assert read_rows(year / "adjustments-staging.csv") == []
    assert len(read_rows(year / "adjustments.csv")) == len(posted)
    assert "違います" not in again


def test_approve_all_holds_estimated_consumption_tax(company_books, capsys):
    """原則課税で達人の額が未入力（概算）のとき、--all は消費税の候補を承認せずに残し、理由を出す。"""
    year, nxt = company_books
    assert main(["close-candidates", "--year-dir", str(year), "--next-year-dir", str(nxt)]) == 0
    capsys.readouterr()
    assert main(["close-approve", "--year-dir", str(year), "--all"]) == 0
    out = capsys.readouterr().out
    assert "close:消費税:2026-03-31" in out and "概算" in out and "close-set-amount" in out
    approval = {r["取り込み元ID"]: r["承認"] for r in read_rows(year / "adjustments-staging.csv")}
    assert approval.pop("close:消費税:2026-03-31") == ""
    assert approval and set(approval.values()) == {"済"}
    # 明示すれば承認できる
    assert main(["close-approve", "--year-dir", str(year), "--ids", "close:消費税:2026-03-31"]) == 0


def test_approve_all_includes_consumption_tax_with_override(company_books, capsys):
    year, nxt = company_books
    assert main(["close-set-amount", "--year-dir", str(year), "--item", "消費税", "--amount", "51364"]) == 0
    assert main(["close-candidates", "--year-dir", str(year), "--next-year-dir", str(nxt)]) == 0
    capsys.readouterr()
    assert main(["close-approve", "--year-dir", str(year), "--all"]) == 0
    assert "概算" not in capsys.readouterr().out
    assert {r["承認"] for r in read_rows(year / "adjustments-staging.csv")} == {"済"}


def test_set_amount_without_rerun_does_not_let_estimate_be_approved(company_books, capsys):
    """close-candidates（概算 51,364）→ close-set-amount 消費税 40,000 → close-candidates を流さずに承認しても、
    古い概算の候補は承認されない（--all は残して理由を出す、--ids は止める）。流し直せば承認できる。"""
    year, nxt = company_books
    tax_id = "close:消費税:2026-03-31"
    assert main(["close-candidates", "--year-dir", str(year), "--next-year-dir", str(nxt)]) == 0
    assert main(["close-set-amount", "--year-dir", str(year), "--item", "消費税", "--amount", "40000"]) == 0
    rows = [r for r in read_rows(year / "adjustments-staging.csv") if r["取り込み元ID"] == tax_id]
    assert all("close-set-amount で額を入れたので close-candidates をやり直す" in r["要確認理由"] for r in rows)
    capsys.readouterr()
    assert main(["close-approve", "--year-dir", str(year), "--all"]) == 0
    out = capsys.readouterr().out
    assert f"承認せずに残しました: {tax_id}" in out and "close-candidates" in out
    approval = {r["取り込み元ID"]: r["承認"] for r in read_rows(year / "adjustments-staging.csv")}
    assert approval[tax_id] == ""
    assert main(["close-approve", "--year-dir", str(year), "--ids", tax_id]) == 1
    assert "close-candidates" in capsys.readouterr().err
    assert {r["取り込み元ID"]: r["承認"] for r in read_rows(year / "adjustments-staging.csv")}[tax_id] == ""

    assert main(["close-candidates", "--year-dir", str(year), "--next-year-dir", str(nxt)]) == 0
    assert main(["close-approve", "--year-dir", str(year), "--ids", tax_id]) == 0
    rows = [r for r in read_rows(year / "adjustments-staging.csv") if r["取り込み元ID"] == tax_id]
    assert {r["承認"] for r in rows} == {"済"} and any(r["借方金額"] == "40000" for r in rows)


def test_approve_all_does_not_list_already_approved_tax(company_books, capsys):
    year, nxt = company_books
    tax_id = "close:消費税:2026-03-31"
    assert main(["close-candidates", "--year-dir", str(year), "--next-year-dir", str(nxt)]) == 0
    assert main(["close-approve", "--year-dir", str(year), "--ids", tax_id]) == 0  # 概算のまま明示して承認済み
    capsys.readouterr()
    assert main(["close-approve", "--year-dir", str(year), "--all"]) == 0
    assert "承認せずに残しました" not in capsys.readouterr().out
