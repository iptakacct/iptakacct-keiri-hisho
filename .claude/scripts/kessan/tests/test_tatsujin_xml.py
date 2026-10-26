"""達人XMLの書き出し（tatsujin_xml.py）と決算書XML（HJ21）。

決算書の数字は test_financials.py の架空の会社（手計算の正解はそちらの docstring）:
  流動資産 普通預金 2,000,000／有形固定資産 工具器具備品 200,000・減価償却累計額 −90,000 → 110,000／資産の部 2,110,000
  流動負債 未払法人税等 150,000／純資産 資本金 500,000・繰越利益剰余金 1,460,000 → 1,960,000
  売上高 1,500,000／販管費 外注費 400,000・減価償却費 40,000 → 440,000／法人税等 150,000
  売上総利益 1,500,000・営業利益＝経常利益＝税引前 1,060,000・当期純利益 910,000
  変動計算書 資本金 500,000→500,000／繰越利益剰余金 650,000 → 剰余金の配当 −100,000・当期純利益 910,000（計 810,000）→ 1,460,000
"""
import xml.etree.ElementTree as ET
from decimal import Decimal

import pytest

import tatsujin_xml
from accounts import load_accounts
from common import JOURNAL_COLUMNS, KessanError, write_rows
from helpers import line
from tatsujin_xml import Node, render
from test_financials import _journal, make_books

CRLF = "\r\n"


def parse(data):
    """cp932 で読んで、宣言の行を除いて ElementTree に渡す（expat は Shift_JIS を直接読めない）。"""
    text = data.decode("cp932")
    first, rest = text.split(CRLF, 1)
    assert first == '<?xml version="1.0" encoding="Shift_JIS"?>'
    return ET.fromstring(rest)


def test_render_format():
    root = Node("REPORT", children=[
        Node("SYSTEM", children=[Node("GYOUMUPREFIX", text="HJ21"), Node("CONNECTEDPROGRAMVER")]),
        Node("DATA", children=[
            Node("CurrentAssets", {"Label": "流動資産", "Amount": "188867"},
                 children=[Node("Item", {"Label": "現金及び預金"}, "188867")]),
            Node("IntangibleAssets", {"Label": "無形固定資産", "Amount": "0"}),
            Node("Empty", text=""),
        ]),
    ])
    expected = CRLF.join([
        '<?xml version="1.0" encoding="Shift_JIS"?>',
        "<REPORT>",
        "  <SYSTEM>",
        "    <GYOUMUPREFIX>HJ21</GYOUMUPREFIX>",
        "    <CONNECTEDPROGRAMVER></CONNECTEDPROGRAMVER>",
        "  </SYSTEM>",
        "  <DATA>",
        '    <CurrentAssets Label="流動資産" Amount="188867">',
        '      <Item Label="現金及び預金">188867</Item>',
        "    </CurrentAssets>",
        '    <IntangibleAssets Label="無形固定資産" Amount="0"/>',
        "    <Empty></Empty>",
        "  </DATA>",
        "</REPORT>",
        "",
    ]).encode("cp932")
    assert render(root) == expected


def test_xml_escapes_names():
    name = 'A&B<商事>"'
    root = Node("HOJYOKAMOKU", children=[
        Node("HOJYOKAMOKU_NAME", text=name),
        Node("Item", {"Label": name}, "1"),
    ])
    data = render(root)
    assert b"A&amp;B&lt;" in data and "&gt;&quot;".encode() in data
    tree = parse(data)
    assert tree.find("HOJYOKAMOKU_NAME").text == name
    assert tree.find("Item").get("Label") == name


def test_render_unencodable_names_where_and_char():
    root = Node("DATA", children=[Node("Item", {"Label": "𠮷田商店"}, "1", where="科目「𠮷田商店」")])
    with pytest.raises(KessanError) as e:
        render(root)
    msg = str(e.value)
    assert "科目「𠮷田商店」" in msg and "「𠮷」" in msg


def test_render_rejects_xml_control_chars():
    root = Node("DATA", children=[Node("Reason", text="配当\x01", where="変動の理由「配当」")])
    with pytest.raises(KessanError, match="U.0001"):
        render(root)


def _yoshida_accounts(tmp_path):
    path = tmp_path / "kessan-accounts.yaml"
    path.write_text("accounts:\n  - {科目: 𠮷田商店, 区分: 資産, 正常残高: 借, 表示区分: 流動資産}\n", encoding="utf-8")
    return load_accounts(extra_path=path)


def _yoshida_books(year_dir):
    make_books(year_dir)
    extra = [line("4", "2025-09-01", debit=("𠮷田商店", "", 10000), credit=("普通預金", "", 10000), 摘要="貸付")]
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, _journal(extra))
    return year_dir


def test_cp932_unencodable_names_abort(year_dir, tmp_path):
    accounts = _yoshida_accounts(tmp_path)
    _yoshida_books(year_dir)
    with pytest.raises(KessanError) as e:
        tatsujin_xml.export(year_dir, accounts, None)
    msg = str(e.value)
    assert "𠮷田商店" in msg and "「𠮷」" in msg
    out = year_dir / "output" / "tatsujin"
    assert not out.exists() or not any(out.iterdir())


def test_load_skeleton_drops_comment_and_blank_values():
    root = tatsujin_xml.load_skeleton("hj21")
    assert root.tag == "REPORT"
    system = root.children[0]
    assert [c.tag for c in system.children] == ["GYOUMUPREFIX", "VERSION", "FUNCTIONID", "CONNECTEDPROGRAMVER",
                                                "PROGRAMNAME", "PROGRAMVER"]
    assert system.children[0].text == "HJ21" and system.children[3].text is None
    assets = root.children[2].children[0].children[0]
    assert assets.tag == "Assets" and list(assets.attrs.items()) == [("Label", "資産の部"), ("Amount", "")]


def test_fill_skeleton_values_and_zero():
    root = tatsujin_xml.load_skeleton("ug16")
    filled = tatsujin_xml.fill_skeleton(root, {"URIAGE_SYUUNYUUDAKA": 1500000, "GAICHUUHI": 0, "KOMONSAKINAME": "テスト"})
    flat = {n.tag: n.text for n in tatsujin_xml.walk(filled)}
    assert flat["URIAGE_SYUUNYUUDAKA"] == "1500000"
    assert flat["GAICHUUHI"] is None
    assert flat["KOMONSAKINAME"] == "テスト"
    # 元の雛形は書き換えない
    assert {n.tag: n.text for n in tatsujin_xml.walk(root)}["URIAGE_SYUUNYUUDAKA"] is None


def test_fill_skeleton_section_keys():
    root = tatsujin_xml.load_skeleton("sy35")
    filled = tatsujin_xml.fill_skeleton(root, {("D020114", "KAZEIURIAGEZEINUKI10_WARIMODOSHI"): 1000})
    d = next(n for n in tatsujin_xml.walk(filled) if n.tag == "D020114")
    assert next(c for c in d.children if c.tag == "KAZEIURIAGEZEINUKI10_WARIMODOSHI").text == "1000"
    filled = tatsujin_xml.fill_skeleton(root, {"KAZEIURIAGEZEINUKI10_WARIMODOSHI": 7}, section="D020114")
    d = next(n for n in tatsujin_xml.walk(filled) if n.tag == "D020114")
    assert next(c for c in d.children if c.tag == "KAZEIURIAGEZEINUKI10_WARIMODOSHI").text == "7"
    with pytest.raises(KessanError):  # 区分の中に無い要素名
        tatsujin_xml.fill_skeleton(root, {("D020113", "KAZEIURIAGEZEINUKI10_WARIMODOSHI"): 1})


def test_fill_skeleton_rejects_unknown_tag():
    root = tatsujin_xml.load_skeleton("hj21")
    with pytest.raises(KessanError, match="NOSUCHTAG"):
        tatsujin_xml.fill_skeleton(root, {"NOSUCHTAG": 1})
    with pytest.raises(KessanError):  # 子を持つ要素には値を入れない
        tatsujin_xml.fill_skeleton(root, {"PATTERN": 1})


def test_header():
    system, basic = tatsujin_xml.header("UG13", 1, "テスト商事", ("2025-04-01", "2026-03-31"))
    assert [(c.tag, c.text) for c in system.children] == [
        ("GYOUMUPREFIX", "UG13"), ("VERSION", "1"), ("FUNCTIONID", "1"), ("CONNECTEDPROGRAMVER", "1.0.0"),
        ("PROGRAMNAME", "kessan"), ("PROGRAMVER", "1.0.0")]
    assert [(c.tag, c.text) for c in basic.children] == [
        ("KOMONSAKINAME", "テスト商事"), ("JIGYOUNENDOFROM", "20250401"), ("JIGYOUNENDOTO", "20260331")]


def _section(parent, tag):
    node = parent.find(tag)
    assert node is not None, tag
    return node


def _items(node):
    return [(i.get("Label"), i.text) for i in node.findall("Item")]


def test_hj21_structure(year_dir, accounts):
    import financials
    st = financials.build_statements(make_books(year_dir), accounts)
    tree = parse(render(tatsujin_xml.build_hj21(st)))
    system = {c.tag: c.text for c in _section(tree, "SYSTEM")}
    assert system == {"GYOUMUPREFIX": "HJ21", "VERSION": "1", "FUNCTIONID": "3", "CONNECTEDPROGRAMVER": "1.0.0",
                      "PROGRAMNAME": "kessan", "PROGRAMVER": "1.0.0"}
    basic = _section(tree, "BASIC")
    assert (basic.find("KOMONSAKINAME").text, basic.find("JIGYOUNENDOFROM").text, basic.find("JIGYOUNENDOTO").text) == \
        ("テスト株式会社", "20250401", "20260331")
    assert [(e.tag, e.text) for e in basic.find("PATTERN").iter() if e.text and e.text.strip()] == [
        ("Doubtful", "A2"), ("DepImp", "B5"), ("Doubtful", "C2"), ("DepImp", "D5"), ("Sales", "E1"), ("Cos", "F3"),
        ("Sga", "G1")]

    bs = tree.find("DATA/BalanceSheet")
    assets = _section(bs, "Assets")
    assert (assets.get("Label"), assets.get("Amount")) == ("資産の部", "2110000")
    current = _section(assets, "CurrentAssets")
    assert current.get("Amount") == "2000000" and _items(current) == [("普通預金", "2000000")]
    nca = _section(assets, "NoncurrentAssets")
    assert nca.get("Amount") == "110000"
    assert [(c.tag, c.get("Amount")) for c in nca] == [
        ("PropertyPlantAndEquipment", "110000"), ("IntangibleAssets", "0"), ("InvestmentsAndOtherAssets", "0")]
    assert list(_section(nca, "IntangibleAssets")) == []
    assert _section(assets, "DeferredAssets").get("Amount") == "0"
    liab = _section(bs, "Liabilities")
    assert liab.get("Amount") == "150000"
    assert _items(_section(liab, "CurrentLiabilities")) == [("未払法人税等", "150000")]
    assert _section(liab, "NoncurrentLiabilities").get("Amount") == "0"
    net = _section(bs, "NetAssets")
    assert (net.get("Label"), net.get("Amount")) == ("純資産の部", "1960000")
    assert _items(net) == [("資本金", "500000"), ("繰越利益剰余金", "1460000")]

    pl = tree.find("DATA/IncomeStatement")
    assert [(c.tag, c.get("Label"), c.get("Amount")) for c in pl] == [
        ("NetSales", "売上高", "1500000"), ("CostOfSales", "売上原価", "0"), ("GrossProfit", "売上総利益", "1500000"),
        ("SellingGeneralAndAdministrativeExpenses", "販売費及び一般管理費", "440000"),
        ("OperatingIncome", "営業利益", "1060000"), ("NonOperatingIncome", "営業外収益", "0"),
        ("NonOperatingExpenses", "営業外費用", "0"), ("OrdinaryIncome", "経常利益", "1060000"),
        ("ExtraordinaryIncome", "特別利益", "0"), ("ExtraordinaryLoss", "特別損失", "0"),
        ("IncomeBeforeIncomeTaxes", "税引前当期純利益", "1060000"), ("IncomeTaxes", "法人税等", "150000"),
        ("NetIncome", "当期純利益", "910000")]
    assert _items(_section(pl, "SellingGeneralAndAdministrativeExpenses")) == [("外注費", "400000"), ("減価償却費", "40000")]
    assert _items(_section(pl, "NetSales")) == [("売上高", "1500000")]
    assert list(_section(pl, "GrossProfit")) == [] and list(_section(pl, "NetIncome")) == []

    eq = tree.find("DATA/StatementChangesNetAssets")
    assert eq.find("Select").text == "1"
    rows = eq.findall("ChangesNetAssetsItem")
    assert [[c.tag for c in r] for r in rows] == [
        ["AccountList", "BalanceEndPreviousPeriod", "BalanceEndCurrentPeriod"],
        ["AccountList", "BalanceEndPreviousPeriod", "ChangesItemsDuringPeriod", "BalanceEndCurrentPeriod"]]
    assert [rows[0].find(t).text for t in ("AccountList", "BalanceEndPreviousPeriod", "BalanceEndCurrentPeriod")] == \
        ["資本金", "500000", "500000"]
    re_ = rows[1]
    assert [re_.find(t).text for t in ("AccountList", "BalanceEndPreviousPeriod", "BalanceEndCurrentPeriod")] == \
        ["繰越利益剰余金", "650000", "1460000"]
    during = re_.find("ChangesItemsDuringPeriod")
    assert [c.tag for c in during] == ["TotalChangesItemsDuringPeriod", "ChangesItem", "ChangesItem"]
    assert during.find("TotalChangesItemsDuringPeriod").text == "810000"
    assert [(c.find("Reason").text, c.find("Amount").text) for c in during.findall("ChangesItem")] == [
        ("剰余金の配当", "-100000"), ("当期純利益", "910000")]


def test_hj21_depreciation_accumulated_is_negative_item(year_dir, accounts):
    import financials
    st = financials.build_statements(make_books(year_dir), accounts)
    tree = parse(render(tatsujin_xml.build_hj21(st)))
    ppe = tree.find("DATA/BalanceSheet/Assets/NoncurrentAssets/PropertyPlantAndEquipment")
    assert _items(ppe) == [("工具器具備品", "200000"), ("減価償却累計額", "-90000")]
    assert ppe.get("Amount") == "110000"


def test_hj21_zero_values_are_empty(year_dir, accounts):
    """期首残高0の純資産（期中に増資）→ BalanceEndPreviousPeriod は空要素。会社名なし → KOMONSAKINAME は空。"""
    import financials
    make_books(year_dir, company="")
    extra = [line("4", "2025-09-01", debit=("普通預金", "", 300000), credit=("資本準備金", "", 300000), 摘要="増資")]
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, _journal(extra))
    st = financials.build_statements(year_dir, accounts)
    data = render(tatsujin_xml.build_hj21(st))
    assert "<KOMONSAKINAME></KOMONSAKINAME>".encode() in data
    tree = parse(data)
    row = next(r for r in tree.iter("ChangesNetAssetsItem") if r.find("AccountList").text == "資本準備金")
    assert row.find("BalanceEndPreviousPeriod").text is None
    assert row.find("BalanceEndCurrentPeriod").text == "300000"


def test_export_writes_kessan(year_dir, accounts):
    paths = tatsujin_xml.export(make_books(year_dir), accounts, None)
    out = year_dir / "output" / "tatsujin"
    assert paths == [out / "kessan-HJ21-20260331.xml", out / "uchiwake-UG13-20260331.xml",
                     out / "gaikyo-UG16-20260331.xml"]
    assert [parse(p.read_bytes()).find("SYSTEM/GYOUMUPREFIX").text for p in paths] == ["HJ21", "UG13", "UG16"]
    gaikyo = parse(paths[2].read_bytes())
    assert gaikyo.find("DATA/SYUYOUKAMOKU/URIAGE_SYUUNYUUDAKA").text == "1500000"
    assert gaikyo.find("BASIC/KOMONSAKINAME").text == "テスト株式会社"
    uchiwake = parse(paths[1].read_bytes())
    assert uchiwake.find("DATA/KANJYOKAMOKU[2]/KANJYOKAMOKU_ZANDAKA").text == "2000000"  # 普通預金
    assert not [p for p in paths[0].parent.iterdir() if p.suffix != ".xml"]  # 一時ファイルを残さない


def test_export_unknown_kind(year_dir, accounts):
    with pytest.raises(KessanError, match="未対応"):
        tatsujin_xml.export(make_books(year_dir), accounts, ["nosuchkind"])


def test_export_writes_nothing_on_failure(year_dir, accounts, monkeypatch):
    """2つ目の種類の組み立てに失敗 → 1つ目（kessan）も書かない。既存のファイルはそのまま。"""
    make_books(year_dir)
    out = year_dir / "output" / "tatsujin"
    out.mkdir(parents=True)
    existing = out / "kessan-HJ21-20260331.xml"
    existing.write_bytes(b"old")

    def broken(ctx):
        raise KessanError("組み立て失敗")

    kinds = dict(tatsujin_xml.KINDS)
    kinds["broken"] = tatsujin_xml.Kind("XX99", broken)
    monkeypatch.setattr(tatsujin_xml, "KINDS", kinds)
    with pytest.raises(KessanError, match="組み立て失敗"):
        tatsujin_xml.export(year_dir, accounts, ["kessan", "broken"])
    assert existing.read_bytes() == b"old"
    assert sorted(p.name for p in out.iterdir()) == ["kessan-HJ21-20260331.xml"]


def test_export_uses_given_statements(year_dir, accounts, monkeypatch):
    import financials
    st = financials.build_statements(make_books(year_dir), accounts)
    monkeypatch.setattr(financials, "build_statements", lambda *a: pytest.fail("組み立て直さない"))
    assert tatsujin_xml.export(year_dir, accounts, ["kessan"], st=st)



@pytest.mark.parametrize("value", [1.5, Decimal("100"), True, None, [1]])
def test_fill_skeleton_rejects_non_int_str_values(value):
    """値は円の整数か文字列だけ。小数・Decimal・真偽値などはプログラムの誤りとして止める。"""
    root = tatsujin_xml.load_skeleton("ug16")
    with pytest.raises(KessanError, match="プログラムの誤り"):
        tatsujin_xml.fill_skeleton(root, {"URIAGE_SYUUNYUUDAKA": value})
