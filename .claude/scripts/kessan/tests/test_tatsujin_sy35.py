"""消費税の達人へ渡すXML（SY35）。

帳簿は test_tax_summary.py と同じ作り方（税込で持ち、税抜・税額は集計時に割り戻す。端数切り捨て）。
手計算の正解は各テストの docstring。税額＝floor(税込×税率÷(100＋税率))、税抜＝税込−税額。
"""
import xml.etree.ElementTree as ET

import pytest

import tatsujin_xml
from common import JOURNAL_COLUMNS, KessanError, init_year_dir, write_rows
from helpers import line
from tatsujin_xml import build_sy35, render
from test_financials import make_books
from test_tatsujin_xml import parse
from test_tax_summary import purchase, run, sale, settings

PERIOD = ("2025-04-01", "2026-03-31")
TAXABLE = {"区分": "課税", "方式": "原則", "計算": "割戻し", "控除": "全額控除"}


def sy35(year_dir, accounts, rows, st=None):
    """帳簿 → 集計 → SY35。(XMLの木, 警告) を返す。"""
    st = st or settings()
    summary = run(year_dir, accounts, rows, st)
    root, warnings = build_sy35(summary, st, "テスト株式会社", PERIOD)
    return parse(render(root)), warnings


def filled(root):
    """DATA の中で値の入った要素 → {(区分, 要素名): 値}。"""
    out = {}
    for section in root.find("DATA"):
        for e in section.iter():
            if e is not section and e.text:
                out[(section.tag, e.tag)] = e.text
    return out


def test_sy35_sales_and_purchases_10_and_8(year_dir, accounts):
    """全額控除・割戻し。
    売上10% 110,000 → 税抜100,000・税額10,000／売上8%軽 10,800 → 10,000・800
    仕入10% 適格22,000（20,000・2,000）＋少額特例5,500（5,000・500）→ 同じ要素に合計 25,000・2,500
    仕入8%軽 適格10,800 → 10,000・800。インボイスなし11,000は控除できないので入れない。税込の要素は空
    """
    root, warnings = sy35(year_dir, accounts, [
        sale("1", 110000),
        sale("2", 10800, cat="課税売上8%軽"),
        purchase("3", 22000),
        purchase("4", 5500, invoice="少額特例"),
        purchase("5", 10800, "課税仕入8%軽"),
        purchase("6", 11000, invoice="なし"),
    ])
    assert filled(root) == {
        ("D020114", "KAZEIURIAGEZEINUKI10_WARIMODOSHI"): "100000",
        ("D020114", "KAZEIURIAGEZEIGAKU10_WARIMODOSHI"): "10000",
        ("D020113", "KAZEIURIAGEZEINUKI8K_WARIMODOSHI"): "10000",
        ("D020113", "KAZEIURIAGEZEIGAKU8K_WARIMODOSHI"): "800",
        ("D020124", "SHIIREKAZEIZEINUKI10_WARIMODOSHI"): "25000",
        ("D020124", "SHIIREKAZEIZEIGAKU10_WARIMODOSHI"): "2500",
        ("D020123", "SHIIREKAZEIZEINUKI8K_WARIMODOSHI"): "10000",
        ("D020123", "SHIIREKAZEIZEIGAKU8K_WARIMODOSHI"): "800",
    }
    assert warnings == []
    assert root.find("SYSTEM/GYOUMUPREFIX").text == "SY35"
    assert root.find("SYSTEM/FUNCTIONID").text == "1"
    assert root.find("SYSTEM/PROGRAMNAME").text == "kessan"
    basic = root.find("BASIC")
    assert [(e.tag, e.text) for e in basic] == [
        ("KOMONSAKINAME", "テスト株式会社"), ("JIGYOUNENDOFROM", "20250401"), ("JIGYOUNENDOTO", "20260331"),
        ("HOUJINKOJINKUBUN", "1"), ("KAZEIKUBUN", "1"), ("KEIKASHOCHIKAZEISHISAN", "2")]


def _allocation_rows():
    return [
        sale("1", 110000),
        sale("2", 100000, cat="非課税売上"),
        purchase("3", 22000, allocation="課税対応"),
        purchase("4", 11000, allocation="共通"),
        purchase("5", 5500, allocation="非課税対応"),
    ]


def test_sy35_individual_allocation(year_dir, accounts):
    """個別対応：課税対応22,000 → KAZEI 20,000・2,000／共通11,000 → KYOTUU 10,000・1,000／
    非課税対応5,500 → HIKAZEI 5,000・500。非課税売上100,000 → HIKAZEIURIAGEYUUKAIGAIZEINUKI10。
    一括比例配分なら個別対応の区分は使わず全部 KAZEI：35,000・3,500。
    """
    root, _ = sy35(year_dir, accounts, _allocation_rows(), settings(allocation="個別対応"))
    values = filled(root)
    assert {k: v for k, v in values.items() if k[0] == "D020124"} == {
        ("D020124", "SHIIREKAZEIZEINUKI10_WARIMODOSHI"): "20000",
        ("D020124", "SHIIREKAZEIZEIGAKU10_WARIMODOSHI"): "2000",
        ("D020124", "SHIIREKYOTUUZEINUKI10_WARIMODOSHI"): "10000",
        ("D020124", "SHIIREKYOTUUZEIGAKU10_WARIMODOSHI"): "1000",
        ("D020124", "SHIIREHIKAZEIZEINUKI10_WARIMODOSHI"): "5000",
        ("D020124", "SHIIREHIKAZEIZEIGAKU10_WARIMODOSHI"): "500",
    }
    assert values[("D020114", "HIKAZEIURIAGEYUUKAIGAIZEINUKI10")] == "100000"

    root, _ = sy35(year_dir, accounts, _allocation_rows(), settings(allocation="一括比例配分"))
    assert {k: v for k, v in filled(root).items() if k[0] == "D020124"} == {
        ("D020124", "SHIIREKAZEIZEINUKI10_WARIMODOSHI"): "35000",
        ("D020124", "SHIIREKAZEIZEIGAKU10_WARIMODOSHI"): "3500",
    }


def test_sy35_individual_allocation_requires_allocation(year_dir, accounts):
    """個別対応方式なのに個別対応の区分が空の課税仕入 → どの要素に入れるか決まらないので止める。
    インボイスなし（入れない行）は区分が空でも止めない。
    """
    with pytest.raises(KessanError, match="個別対応"):
        sy35(year_dir, accounts, [purchase("1", 11000)], settings(allocation="個別対応"))
    root, _ = sy35(year_dir, accounts, [purchase("1", 11000, invoice="なし")], settings(allocation="個別対応"))
    assert filled(root) == {}


def test_sy35_tsumiage_purchases(year_dir, accounts):
    """積上げ：仕入10% 1,000円×3行 → 行ごとの税額 floor(1,000×10÷110)=90 ×3 = 270、税抜 3,000−270 = 2,730
    （割戻しなら 272）。売上は積上げの年度でも割戻し：110,000 → 100,000・10,000。
    """
    root, warnings = sy35(year_dir, accounts, [
        sale("1", 110000),
        purchase("2", 1000), purchase("3", 1000), purchase("4", 1000),
    ], settings(calc="積上げ"))
    assert filled(root) == {
        ("D020114", "KAZEIURIAGEZEINUKI10_WARIMODOSHI"): "100000",
        ("D020114", "KAZEIURIAGEZEIGAKU10_WARIMODOSHI"): "10000",
        ("D020124", "SHIIREKAZEIZEINUKI10_TSUMIAGE"): "2730",
        ("D020124", "SHIIREKAZEIZEIGAKU10_TSUMIAGE"): "270",
    }
    assert warnings == []


def test_sy35_transition_80_50(year_dir, accounts):
    """経過80 10% 55,000 → 50,000・5,000／経過50 10% 22,000 → 20,000・2,000／経過80 8%軽 10,800 → 10,000・800。
    税額は控除割合を掛ける前の額。積上げの年度でも割戻し80/50の欄に入れて警告する。
    """
    rows = [purchase("1", 55000, invoice="経過80"), purchase("2", 22000, invoice="経過50"),
            purchase("3", 10800, "課税仕入8%軽", invoice="経過80")]
    root, warnings = sy35(year_dir, accounts, rows)
    assert filled(root) == {
        ("D020124", "SHIIREKAZEIZEINUKI10_INVOICEIGAI_WARIMODOSHI80"): "50000",
        ("D020124", "SHIIREKAZEIZEIGAKU10_INVOICEIGAI_WARIMODOSHI80"): "5000",
        ("D020124", "SHIIREKAZEIZEINUKI10_INVOICEIGAI_WARIMODOSHI50"): "20000",
        ("D020124", "SHIIREKAZEIZEIGAKU10_INVOICEIGAI_WARIMODOSHI50"): "2000",
        ("D020123", "SHIIREKAZEIZEINUKI8K_INVOICEIGAI_WARIMODOSHI80"): "10000",
        ("D020123", "SHIIREKAZEIZEIGAKU8K_INVOICEIGAI_WARIMODOSHI80"): "800",
    }
    assert warnings == []

    # 積上げ：1行ずつなので税額は割戻しと同じ（55,000 → 5,000）
    root, warnings = sy35(year_dir, accounts, rows[:1], settings(calc="積上げ"))
    assert filled(root) == {
        ("D020124", "SHIIREKAZEIZEINUKI10_INVOICEIGAI_WARIMODOSHI80"): "50000",
        ("D020124", "SHIIREKAZEIZEIGAKU10_INVOICEIGAI_WARIMODOSHI80"): "5000",
    }
    assert len(warnings) == 1 and "積上げの年度の経過措置分は割戻しの欄に入れた" in warnings[0]


def test_sy35_transition_70_30_warned_not_written(year_dir, accounts):
    """経過70 11,000・経過30 3,300 は欄が無いので入れず、税込合計14,300円を警告に出す。適格22,000 → 20,000・2,000。
    export を通すと警告が notes に入る（CLI が表示する）。
    """
    rows = [purchase("1", 11000, invoice="経過70"), purchase("2", 3300, invoice="経過30"), purchase("3", 22000)]
    root, warnings = sy35(year_dir, accounts, rows)
    assert filled(root) == {
        ("D020124", "SHIIREKAZEIZEINUKI10_WARIMODOSHI"): "20000",
        ("D020124", "SHIIREKAZEIZEIGAKU10_WARIMODOSHI"): "2000",
    }
    assert len(warnings) == 1
    assert "経過措置70%・30%分（税込14,300円" in warnings[0] and "達人に手で入れる" in warnings[0]

    init_year_dir(year_dir, *PERIOD, tax=TAXABLE)
    notes = []
    paths = tatsujin_xml.export(year_dir, accounts, ["shohizei"], notes=notes)
    assert [p.name for p in paths] == ["shohizei-SY35-20260331.xml"]
    assert any("税込14,300円" in n for n in notes)


def test_sy35_returns_and_bad_debts(year_dir, accounts):
    """課税売上返還10% 11,000 → 10,000・1,000／課税仕入返還10% 適格5,500 → 5,000・500／
    貸倒8%軽 10,800 → 10,000・800（D020133）／貸倒回収10% 22,000 → 20,000・2,000（D020134）。
    """
    root, _ = sy35(year_dir, accounts, [
        sale("1", 11000, cat="課税売上返還10%", side="debit"),
        line("2", "2025-06-03", debit=("現金", "", 5500), credit=("消耗品費", "", 5500),
             貸方税区分="課税仕入返還10%", 貸方インボイス="適格"),
        line("3", "2025-06-04", debit=("貸倒損失", "", 10800), credit=("売掛金", "", 10800), 借方税区分="貸倒8%軽"),
        line("4", "2025-06-05", debit=("現金", "", 22000), credit=("雑収入", "", 22000), 貸方税区分="貸倒回収10%"),
    ])
    assert filled(root) == {
        ("D020114", "KAZEIURIAGEHENKANZEINUKI10_WARIMODOSHI"): "10000",
        ("D020114", "KAZEIURIAGEHENKANZEIGAKU10_WARIMODOSHI"): "1000",
        ("D020124", "SHIIREHENKANKAZEIZEINUKI10_WARIMODOSHI"): "5000",
        ("D020124", "SHIIREHENKANKAZEIZEIGAKU10_WARIMODOSHI"): "500",
        ("D020133", "KASHIDAOREZEINUKI8K_WARIMODOSHI"): "10000",
        ("D020133", "KASHIDAOREZEIGAKU8K_WARIMODOSHI"): "800",
        ("D020134", "KASHIDAOREKAISHUZEINUKI10_WARIMODOSHI"): "20000",
        ("D020134", "KASHIDAOREKAISHUZEIGAKU10_WARIMODOSHI"): "2000",
    }


def test_sy35_exempt_and_nontaxable_sales(year_dir, accounts):
    """免税売上50,000・非課税売上30,000・非課税資産輸出20,000 は税率が無いので D020114 に税抜＝税込で入れる。
    非課税仕入7,000 は入れない。
    """
    root, _ = sy35(year_dir, accounts, [
        sale("1", 50000, cat="免税売上"),
        sale("2", 30000, cat="非課税売上"),
        sale("3", 20000, cat="非課税資産輸出"),
        purchase("4", 7000, "非課税仕入", invoice=""),
    ])
    assert filled(root) == {
        ("D020114", "MENZEIURIAGEZEINUKI10"): "50000",
        ("D020114", "HIKAZEIURIAGEYUUKAIGAIZEINUKI10"): "30000",
        ("D020114", "HIKAZEISHISANYUSHUTSUZEINUKI10"): "20000",
    }


@pytest.mark.parametrize("tax, message", [
    (None, "免税"),
    ({"区分": "課税", "方式": "簡易", "計算": "割戻し"}, "簡易課税・2割特例は集計表を見て達人に手で入れる"),
    ({"区分": "課税", "方式": "2割特例", "計算": "割戻し"}, "簡易課税・2割特例は集計表を見て達人に手で入れる"),
])
def test_sy35_not_written_for_kanni_2wari_exempt(year_dir, accounts, tax, message):
    """--kind 省略時は飛ばして理由を notes に出す。--kind shohizei を明示したら KessanError。"""
    make_books(year_dir)
    if tax is not None:
        init_year_dir(year_dir, *PERIOD, tax=tax)
    notes = []
    paths = tatsujin_xml.export(year_dir, accounts, None, notes=notes)
    assert [p.name for p in paths] == ["kessan-HJ21-20260331.xml", "uchiwake-UG13-20260331.xml",
                                       "gaikyo-UG16-20260331.xml"]
    assert any(n.startswith("shohizei は出しません") and message in n for n in notes)
    with pytest.raises(KessanError, match=message):
        tatsujin_xml.export(year_dir, accounts, ["shohizei"])


def test_sy35_written_with_others_for_gensoku(year_dir, accounts):
    """原則課税の年度は --kind 省略で4種類とも書く。"""
    make_books(year_dir)
    init_year_dir(year_dir, *PERIOD, tax=TAXABLE)
    paths = tatsujin_xml.export(year_dir, accounts, None)
    assert [p.name for p in paths][-1] == "shohizei-SY35-20260331.xml"
    root = parse(paths[-1].read_bytes())
    assert root.find("BASIC/KOMONSAKINAME").text == "テスト株式会社"


def test_sy35_skeleton_order_preserved(year_dir, accounts):
    """書いたXMLの要素の並びは雛形と同じ（値を入れても要素を足し引きしない）。"""
    root, _ = sy35(year_dir, accounts, [sale("1", 110000), purchase("2", 22000, invoice="経過80")])
    skeleton = ET.parse(tatsujin_xml.SKELETON_DIR / "sy35-skeleton.xml").getroot()
    assert [e.tag for e in root.iter()] == [e.tag for e in skeleton.iter()]
