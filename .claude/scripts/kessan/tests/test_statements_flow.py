"""決算書・達人XMLの通しテスト：test_close_flow.py の架空の課税の会社（決算整理の登録まで）に statements と export-tatsujin を流す。

帳簿は test_close_flow.py の company_books をそのまま使う。以下は帳簿の仕訳と決算整理 A1〜A8 から手で計算した正解（コードから作っていない）。

  貸借対照表（期末）
    流動資産  普通預金 916,000（期首 1,000,000 ＋売上入金 1,100,000 −保険 24,000 −PC 150,000 −サーバー 440,000
              −中間納付 20,000 ＋借入 1,000,000 −貸付 200,000 −仮払 30,000 −役員報酬 1,320,000）／売掛金 110,000
              ／前払費用 6,000（期首 12,000 ＋6,000 −12,000）／仮払金 30,000／役員貸付金 200,000 ＝ 1,262,000
              （仮払税金は 20,000 −20,000 で 0）
    固定資産  工具器具備品 150,000 ＋440,000 −50,000（PC一式の一括償却）＝ 540,000／減価償却累計額 −55,000 ＝ 485,000
    資産合計  1,747,000
    負債      流動 未払金 55,000・未払法人税等 70,000・未払消費税等 31,364 ＝ 156,364／固定 役員借入金 1,500,000／合計 1,656,364
    純資産    資本金 512,000＋繰越利益剰余金（当期純利益 −421,364、期首の繰越利益剰余金は 0）＝ 90,636
              負債＋純資産 ＝ 1,656,364 ＋ 90,636 ＝ 1,747,000（資産合計と一致）

  損益計算書
    売上高 1,100,000 ＋未収 110,000 ＝ 1,210,000（売上原価なし）→ 売上総利益 1,210,000
    販管費 役員報酬 1,320,000（100,000×6 ＋120,000×6）／外注費 55,000／保険料 24,000 ＋12,000 −6,000 ＝ 30,000
           ／租税公課 51,364／減価償却費 55,000 ＋50,000 ＝ 105,000 → 合計 1,561,364
    営業利益 1,210,000 −1,561,364 ＝ −351,364 ＝ 経常利益 ＝ 税引前当期純利益／法人税等 70,000／当期純利益 −421,364

  消費税 SY35（原則・割戻し・全額控除）
    課税売上10%（税込 1,210,000）→ 税抜 1,100,000・税額 110,000
    課税仕入10%・適格（税込 440,000 ＋150,000 ＋55,000 ＝ 645,000）→ 税額 floor(645,000×10÷110) ＝ 58,636、税抜 645,000 −58,636 ＝ 586,364

  概況書 UG16（期首 4月 → TUKIBETU_01＝4月 … _12＝3月）
    売上高 1,210,000／役員報酬 1,320,000／減価償却費 105,000／租税公課 51,364／営業損益・税引前 −351,364
    月別の売上：6月（_03）1,100,000、3月（_12）110,000（未収は期末日付）／外注費：3月 55,000
    人件費：4〜9月（_01〜_06）100,000、10〜3月（_07〜_12）120,000

  内訳書 UG13  普通預金の補助（サンプル銀行）916,000、売掛金 110,000
"""
import xml.etree.ElementTree as ET

import pytest

from check import has_ng, run_checks
from cli import main
from test_close_flow import company_books  # noqa: F401  （フィクスチャ：架空の課税の会社の帳簿）


def read_xml(path):
    raw = path.read_bytes()
    assert b"\r\n" in raw
    text = raw.decode("cp932")  # cp932 で読めること
    assert text.startswith('<?xml version="1.0" encoding="Shift_JIS"?>')
    return ET.fromstring(text.split("\n", 1)[1])


def tag(root, path):
    node = root.find(path)
    assert node is not None, path
    return (node.text or "").strip()


def items(root, section):
    nodes = list(root.find(section).iter("Item"))
    result = {i.get("Label"): int(i.text) for i in nodes}
    assert len(result) == len(nodes)  # 同じラベルの項目が重複していない
    return result


@pytest.fixture
def closed_year(company_books, accounts):  # noqa: F811
    year, nxt = company_books
    for argv in (
        ["close-set-amount", "--year-dir", str(year), "--item", "法人税等", "--amount", "70000"],
        ["close-candidates", "--year-dir", str(year), "--next-year-dir", str(nxt)],
        ["close-approve", "--year-dir", str(year), "--all"],
        ["close-approve", "--year-dir", str(year), "--ids", "close:消費税:2026-03-31"],  # 概算は --all で残るので明示
        ["close-post", "--year-dir", str(year)],
    ):
        assert main(argv) == 0
    assert not has_ng(run_checks(year, accounts))
    return year


def test_statements_and_export_flow(closed_year, capsys):
    year = closed_year
    capsys.readouterr()
    assert main(["statements", "--year-dir", str(year)]) == 0
    out = capsys.readouterr().out
    assert "当期純利益 -421,364円" in out
    assert "会社名が未設定" in out  # init で --company-name を付けていない
    for name in ("financial-statements.xlsx", "notes-draft.md", "uchiwake-material.xlsx", "gaikyo-material.xlsx"):
        assert (year / "output" / name).exists()

    assert main(["export-tatsujin", "--year-dir", str(year)]) == 0
    capsys.readouterr()
    folder = year / "output" / "tatsujin"
    names = sorted(p.name for p in folder.glob("*.xml"))
    assert names == ["gaikyo-UG16-20260331.xml", "kessan-HJ21-20260331.xml", "shohizei-SY35-20260331.xml",
                     "uchiwake-UG13-20260331.xml"]
    xml = {n.split("-")[0]: read_xml(folder / n) for n in names}
    for kind, prefix in (("kessan", "HJ21"), ("uchiwake", "UG13"), ("gaikyo", "UG16"), ("shohizei", "SY35")):
        assert tag(xml[kind], "SYSTEM/GYOUMUPREFIX") == prefix
        assert tag(xml[kind], "SYSTEM/PROGRAMNAME") == "kessan"
        assert tag(xml[kind], "BASIC/JIGYOUNENDOFROM") == "20250401"
        assert tag(xml[kind], "BASIC/JIGYOUNENDOTO") == "20260331"

    # 決算書 HJ21
    bs = xml["kessan"].find("DATA/BalanceSheet")
    assert int(bs.find("Assets").get("Amount")) == 1747000
    assert int(bs.find("Assets/CurrentAssets").get("Amount")) == 1262000
    assert int(bs.find("Assets/NoncurrentAssets/PropertyPlantAndEquipment").get("Amount")) == 485000
    assert int(bs.find("Liabilities").get("Amount")) == 1656364
    assert int(bs.find("Liabilities/CurrentLiabilities").get("Amount")) == 156364
    assert int(bs.find("Liabilities/NoncurrentLiabilities").get("Amount")) == 1500000
    assert int(bs.find("NetAssets").get("Amount")) == 90636
    bs_items = items(xml["kessan"], "DATA/BalanceSheet")
    assert bs_items["減価償却累計額"] == -55000
    assert bs_items["普通預金"] == 916000
    assert bs_items["繰越利益剰余金"] == -421364
    pl = xml["kessan"].find("DATA/IncomeStatement")
    amounts = {e.tag: int(e.get("Amount")) for e in pl}
    assert amounts == {
        "NetSales": 1210000, "CostOfSales": 0, "GrossProfit": 1210000, "SellingGeneralAndAdministrativeExpenses": 1561364,
        "OperatingIncome": -351364, "NonOperatingIncome": 0, "NonOperatingExpenses": 0, "OrdinaryIncome": -351364,
        "ExtraordinaryIncome": 0, "ExtraordinaryLoss": 0, "IncomeBeforeIncomeTaxes": -351364, "IncomeTaxes": 70000,
        "NetIncome": -421364,
    }
    sga = items(xml["kessan"], "DATA/IncomeStatement/SellingGeneralAndAdministrativeExpenses")
    assert sga == {"役員報酬": 1320000, "外注費": 55000, "保険料": 30000, "租税公課": 51364, "減価償却費": 105000}
    assert tag(xml["kessan"], "DATA/StatementChangesNetAssets/ChangesNetAssetsItem[2]/BalanceEndCurrentPeriod") == "-421364"

    # 消費税 SY35
    assert tag(xml["shohizei"], "BASIC/KAZEIKUBUN") == "1"
    assert tag(xml["shohizei"], "DATA/D020114/KAZEIURIAGEZEINUKI10_WARIMODOSHI") == "1100000"
    assert tag(xml["shohizei"], "DATA/D020114/KAZEIURIAGEZEIGAKU10_WARIMODOSHI") == "110000"
    assert tag(xml["shohizei"], "DATA/D020124/SHIIREKAZEIZEINUKI10_WARIMODOSHI") == "586364"
    assert tag(xml["shohizei"], "DATA/D020124/SHIIREKAZEIZEIGAKU10_WARIMODOSHI") == "58636"

    # 概況書 UG16
    gk = xml["gaikyo"]
    expected_main = {"URIAGE_SYUUNYUUDAKA": 1210000, "YAKUINHOUSYUU": 1320000, "GENKASYOUKYAKUHI_HANKANHI": 105000,
                     "TIDAIYATIN_SOZEIKOUKA_HANKANHI": 51364, "EIGYOUSONEKI": -351364, "ZEIBIKIMAETOUKISONEKI": -351364,
                     "SISANNOBU": 1747000, "GENKINYOKIN": 916000, "URIKAKEKIN": 110000, "KASITUKEKIN": 200000,
                     "FUSAINOBU": 1656364, "KOJINKARIIREKIN": 1500000, "SIHONNOBU": 90636}
    for key, value in expected_main.items():
        assert tag(gk, f"DATA/SYUYOUKAMOKU/{key}") == str(value)
    monthly = "DATA/TUKIBETUURIAGEDAKA/"
    months = [tag(gk, f"{monthly}TUKIBETU_{n:02d}") for n in range(1, 13)]
    assert months == [str(m) for m in (4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3)]
    assert tag(gk, monthly + "URIAGESYUUNYUUKINGAKU1_03") == "1100000"
    assert tag(gk, monthly + "URIAGESYUUNYUUKINGAKU1_12") == "110000"
    assert tag(gk, monthly + "URIAGESYUUNYUUKINGAKU1_01") == ""
    assert tag(gk, monthly + "GAICHUUHI_12") == "55000"
    assert [tag(gk, f"{monthly}JINKENHI_{n:02d}") for n in range(1, 13)] == ["100000"] * 6 + ["120000"] * 6

    # 内訳書 UG13：普通預金の補助の残高
    ug13 = xml["uchiwake"]
    deposit = next(k for k in ug13.findall("DATA/KANJYOKAMOKU") if tag(k, "KANJYOKAMOKU_NAME") == "普通預金")
    assert tag(deposit, "KANJYOKAMOKU_ZANDAKA") == "916000"
    assert tag(deposit, "HOJYOKAMOKU/HOJYOKAMOKU_NAME") == "サンプル銀行"
    assert tag(deposit, "HOJYOKAMOKU/HOJYOKAMOKU_ZANDAKA") == "916000"
    counts = [k.get("Count") for k in ug13.findall("DATA/KANJYOKAMOKU")]
    assert counts == [str(n) for n in range(1, len(counts) + 1)]
    receivable = next(k for k in ug13.findall("DATA/KANJYOKAMOKU") if tag(k, "KANJYOKAMOKU_NAME") == "売掛金")
    assert tag(receivable, "KANJYOKAMOKU_ZANDAKA") == "110000"


def test_company_name_is_written(closed_year, capsys):
    """init --company-name で入れた会社名が、決算書XMLの KOMONSAKINAME に載り、警告が消える。"""
    year = closed_year
    assert main(["init", "--year-dir", str(year), "--start", "2025-04-01", "--end", "2026-03-31",
                 "--company-name", "テスト商事株式会社"]) == 0
    capsys.readouterr()
    assert main(["statements", "--year-dir", str(year)]) == 0
    assert "会社名が未設定" not in capsys.readouterr().out
    assert main(["export-tatsujin", "--year-dir", str(year), "--kind", "kessan"]) == 0
    root = read_xml(year / "output" / "tatsujin" / "kessan-HJ21-20260331.xml")
    assert tag(root, "BASIC/KOMONSAKINAME") == "テスト商事株式会社"
