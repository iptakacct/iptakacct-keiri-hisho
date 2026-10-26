"""達人シリーズへ渡すXML（段階3c）：決算書（HJ21）・内訳書（UG13）・概況書（UG16）・消費税（SY35、原則課税の年度だけ）。

会計ソフト（MF・freee）が達人へ渡している「中間ファイル」と同じ形で出す（docs/kessan/tatsujin-import.md §3.5）。
- 書式は実物どおり：Shift_JIS（cp932）、CRLF、2字下げ、空の要素は `<X></X>`、属性だけの要素は `<X A="…"/>`
- 要素の並びが固定のもの（UG16・SY35）は config/tatsujin/<名前>-skeleton.xml を読んで fill_skeleton で値を入れる。
  科目で中身が変わるもの（HJ21・UG13）はコードで組み立てる
- 金額は円の整数。0・該当なしは空の要素（区分の Amount 属性だけは実物どおり "0"）
- cp932 で表せない文字があれば、どの科目・補助のどの文字かを示して KessanError（何も書かない。勝手に置き換えない）

種類を足すときは KINDS に1行登録する（Kind(GYOUMUPREFIX, 組み立て関数)）。組み立て関数は Context を受けて Node を返す。
"""
import copy
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from common import KessanError, cannot_write, load_company_name, load_period, swap_in

SKELETON_DIR = Path(__file__).parents[3] / "config/tatsujin"
OUTPUT_DIR = "output/tatsujin"
DECLARATION = '<?xml version="1.0" encoding="Shift_JIS"?>'
NEWLINE = "\r\n"
INDENT = "  "
PROGRAM_NAME = "kessan"
PROGRAM_VERSION = "1.0.0"
# XML 1.0 で書けない文字（タブ・改行以外の制御文字など）。cp932 では表せても達人が読めなくなる
_XML_FORBIDDEN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")
_ESCAPES = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)  # 並び順を保つ
    text: str | None = None
    children: list = field(default_factory=list)
    where: str | None = None  # cp932 で書けないときに示す出どころ（例：科目「…」）


def walk(node):
    """node 自身と子孫を、文書の順に返す。"""
    yield node
    for c in node.children:
        yield from walk(c)


# ---- 書き出し ----

def _check_cp932(text, node, what):
    bad = _XML_FORBIDDEN.search(text)
    if bad:
        where = node.where or f"要素 {node.tag} の{what}"
        raise KessanError(f"達人XMLに書けない文字があります：{where} の「{text!r}」に制御文字（U+{ord(bad.group()):04X}）が"
                          "入っています。名前を直してから出し直してください（XMLは書いていません）")
    try:
        text.encode("cp932")
    except UnicodeEncodeError as e:
        ch = text[e.start]
        where = node.where or f"要素 {node.tag} の{what}"
        raise KessanError(f"達人XMLに書けない文字があります：{where} の「{text}」の「{ch}」（U+{ord(ch):04X}）は"
                          "Shift_JIS（cp932）で表せません。名前を直してから出し直してください（XMLは書いていません）") from None


def _escape(text, node, what):
    _check_cp932(text, node, what)
    return text.translate(_ESCAPES)


def _lines(node, depth, out):
    pad = INDENT * depth
    attrs = "".join(f' {k}="{_escape(str(v), node, f"属性 {k}")}"' for k, v in node.attrs.items())
    if node.children:
        out.append(f"{pad}<{node.tag}{attrs}>")
        for c in node.children:
            _lines(c, depth + 1, out)
        out.append(f"{pad}</{node.tag}>")
    elif node.text:
        out.append(f"{pad}<{node.tag}{attrs}>{_escape(node.text, node, '値')}</{node.tag}>")
    elif attrs:
        out.append(f"{pad}<{node.tag}{attrs}/>")
    else:
        out.append(f"{pad}<{node.tag}></{node.tag}>")


def render(root):
    """Node を達人の中間ファイルの書式のバイト列にする。cp932 で表せない文字があれば KessanError。"""
    out = [DECLARATION]
    _lines(root, 0, out)
    return (NEWLINE.join(out) + NEWLINE).encode("cp932")


# ---- 雛形 ----

def _from_element(e):
    text = (e.text or "").strip() or None
    return Node(e.tag, dict(e.attrib), text, [_from_element(c) for c in e])


def load_skeleton(name):
    """config/tatsujin/<name>-skeleton.xml を Node にする（コメントは捨てる。空白だけの値は None）。"""
    path = SKELETON_DIR / f"{name}-skeleton.xml"
    try:
        return _from_element(ET.parse(path).getroot())
    except (OSError, ET.ParseError) as e:
        raise KessanError(f"達人XMLの雛形 {path} を読めません: {e}") from None


def _value_text(value):
    if isinstance(value, bool):
        raise KessanError(f"達人XMLの値が不正です（プログラムの誤り）: {value!r}")
    if isinstance(value, int):
        return str(value) if value else None
    return str(value) or None


def _ancestors_map(root):
    parents = {}
    for n in walk(root):
        for c in n.children:
            parents[id(c)] = n
    return parents


def _has_ancestor(node, tag, parents):
    p = parents.get(id(node))
    while p is not None:
        if p.tag == tag:
            return True
        p = parents.get(id(p))
    return False


def fill_skeleton(root, values, section=None):
    """雛形の写しに値を要素名で入れて返す（雛形自体は変えない）。

    values のキーは要素名か (区分, 要素名)。区分は祖先の要素名で、同じ要素名が複数の区分にあるときに使う。
    section を渡すと、要素名だけのキーもその区分の中から探す。0 は空の要素にする。
    雛形に無い要素名・複数当たる要素名・子を持つ要素への値は、プログラムの誤りとして KessanError。
    """
    root = copy.deepcopy(root)
    parents = _ancestors_map(root)
    nodes = list(walk(root))
    for key, value in values.items():
        scope, tag = key if isinstance(key, tuple) else (section, key)
        hits = [n for n in nodes if n.tag == tag and (scope is None or _has_ancestor(n, scope, parents))]
        label = f"{scope}/{tag}" if scope else tag
        if not hits:
            raise KessanError(f"達人XMLの雛形に要素 {label} がありません（プログラムの誤り）")
        if len(hits) > 1:
            raise KessanError(f"達人XMLの雛形に要素 {label} が{len(hits)}か所あります。区分を指定してください（プログラムの誤り）")
        if hits[0].children:
            raise KessanError(f"達人XMLの要素 {label} は子を持つため値を入れられません（プログラムの誤り）")
        hits[0].text = _value_text(value)
    return root


# ---- 共通の外枠 ----

def _yyyymmdd(date):
    return date.replace("-", "")


def header(prefix, function_id, company, period):
    """SYSTEM と BASIC の共通部分。BASIC には種類ごとの要素（PATTERN など）を後ろに足してよい。"""
    system = Node("SYSTEM", children=[
        Node("GYOUMUPREFIX", text=prefix),
        Node("VERSION", text="1"),
        Node("FUNCTIONID", text=str(function_id)),
        Node("CONNECTEDPROGRAMVER", text=PROGRAM_VERSION),
        Node("PROGRAMNAME", text=PROGRAM_NAME),
        Node("PROGRAMVER", text=PROGRAM_VERSION),
    ])
    basic = Node("BASIC", children=[
        Node("KOMONSAKINAME", text=company or None, where="会社名（period.yaml）"),
        Node("JIGYOUNENDOFROM", text=_yyyymmdd(period[0])),
        Node("JIGYOUNENDOTO", text=_yyyymmdd(period[1])),
    ])
    return system, basic


def fill_header(root, company, period):
    """雛形（UG16・SY35）の SYSTEM・BASIC の共通部分を埋める。BASIC の固有の要素（雛形の値）はそのまま。"""
    filled = fill_skeleton(root, {
        ("SYSTEM", "CONNECTEDPROGRAMVER"): PROGRAM_VERSION,
        ("SYSTEM", "PROGRAMNAME"): PROGRAM_NAME,
        ("SYSTEM", "PROGRAMVER"): PROGRAM_VERSION,
        ("BASIC", "KOMONSAKINAME"): company or "",
        ("BASIC", "JIGYOUNENDOFROM"): _yyyymmdd(period[0]),
        ("BASIC", "JIGYOUNENDOTO"): _yyyymmdd(period[1]),
    })
    next(n for n in walk(filled) if n.tag == "KOMONSAKINAME").where = "会社名（period.yaml）"
    return filled


# ---- 決算書（HJ21） ----

# 貸借対照表・損益計算書の区分の要素 → Statements の区分（表示区分）
HJ21_SECTIONS = {
    "CurrentAssets": ("流動資産",),
    "PropertyPlantAndEquipment": ("有形固定資産",),
    "IntangibleAssets": ("無形固定資産",),
    "InvestmentsAndOtherAssets": ("投資その他の資産",),
    "DeferredAssets": ("繰延資産",),
    "CurrentLiabilities": ("流動負債",),
    "NoncurrentLiabilities": ("固定負債",),
    "NetAssets": ("株主資本", "評価・換算差額等"),
    "NetSales": ("売上高",),
    "CostOfSales": ("売上原価",),
    "SellingGeneralAndAdministrativeExpenses": ("販売費及び一般管理費",),
    "NonOperatingIncome": ("営業外収益",),
    "NonOperatingExpenses": ("営業外費用",),
    "ExtraordinaryIncome": ("特別利益",),
    "ExtraordinaryLoss": ("特別損失",),
    "IncomeTaxes": ("法人税等",),
}
# 段階利益の要素 → Statements.profits のキー（Amount だけ。Item なし）
HJ21_PROFITS = {
    "GrossProfit": "売上総利益",
    "OperatingIncome": "営業利益",
    "OrdinaryIncome": "経常利益",
    "IncomeBeforeIncomeTaxes": "税引前当期純利益",
    "NetIncome": "当期純利益",
}


def _hj21_section(node, sections, profits):
    """区分の要素に Amount と Item を入れ、Amount を返す。子の区分を持つ要素（Assets など）は子の合計。"""
    if node.tag in HJ21_SECTIONS:
        parts = [sections[name] for name in HJ21_SECTIONS[node.tag]]
        node.children = [Node("Item", {"Label": acc}, str(amount), where=f"科目「{acc}」")
                         for s in parts for acc, amount in s.items]
        amount = sum(s.total for s in parts)
    elif node.tag in HJ21_PROFITS:
        node.children = []
        amount = profits[HJ21_PROFITS[node.tag]]
    elif node.children:
        amount = sum(_hj21_section(c, sections, profits) for c in node.children)
    else:
        raise KessanError(f"決算書XMLの雛形の要素 {node.tag} の対応がありません（プログラムの誤り）")
    node.attrs["Amount"] = str(amount)
    return amount


def _amount_node(tag, amount):
    return Node(tag, text=str(amount) if amount else None)


def _equity_item(row):
    children = [Node("AccountList", text=row.account, where=f"純資産の科目「{row.account}」"),
                _amount_node("BalanceEndPreviousPeriod", row.opening)]
    if row.changes:
        during = [_amount_node("TotalChangesItemsDuringPeriod", sum(a for _, a in row.changes))]
        during += [Node("ChangesItem", children=[
            Node("Reason", text=reason, where=f"株主資本等変動計算書の変動の理由（摘要）「{reason}」"),
            _amount_node("Amount", amount),
        ]) for reason, amount in row.changes]
        children.append(Node("ChangesItemsDuringPeriod", children=during))
    children.append(_amount_node("BalanceEndCurrentPeriod", row.closing))
    return Node("ChangesNetAssetsItem", children=children)


def build_hj21(st):
    """Statements から決算書XML（貸借対照表・損益計算書・株主資本等変動計算書）を組み立てる。

    BASIC/PATTERN は雛形の値のまま（各コードの意味を実機で確かめるまで設定にしない）。
    """
    skeleton = load_skeleton("hj21")
    pattern = next(n for n in walk(skeleton) if n.tag == "PATTERN")
    data = next(n for n in skeleton.children if n.tag == "DATA")
    sections = {s.name: s for s in st.bs + st.pl}
    for part in ("BalanceSheet", "IncomeStatement"):
        for node in next(n for n in data.children if n.tag == part).children:
            _hj21_section(node, sections, st.profits)
    changes = next(n for n in data.children if n.tag == "StatementChangesNetAssets")
    changes.children = [n for n in changes.children if n.tag == "Select"] + [_equity_item(r) for r in st.equity]
    system, basic = header("HJ21", 3, st.company, st.period)
    basic.children.append(pattern)
    return Node("REPORT", children=[system, basic, data])


# ---- 内訳書（UG13） ----

def _amount_text(amount):
    return str(amount) if amount else None


def build_ug13(items, company, period):
    """uchiwake.build の結果から内訳書XMLを組み立てる。科目・補助ごとに Count は1からの通し番号。"""
    rows = []
    for count, item in enumerate(items, start=1):
        where = f"科目「{item.name}」"
        children = [
            Node("KANJYOKAMOKU_CODE", text=item.code),
            Node("KANJYOKAMOKU_NAME", text=item.name, where=where),
            Node("KANJYOKAMOKU_ZANDAKA", text=_amount_text(item.balance)),
        ]
        children += [Node("HOJYOKAMOKU", {"Count": str(j)}, children=[
            Node("HOJYOKAMOKU_CODE", text=code),
            Node("HOJYOKAMOKU_NAME", text=sub, where=f"{where}の補助「{sub}」"),
            Node("HOJYOKAMOKU_ZANDAKA", text=_amount_text(amount)),
        ]) for j, (code, sub, amount) in enumerate(item.subs, start=1)]
        rows.append(Node("KANJYOKAMOKU", {"Count": str(count)}, children=children))
    system, basic = header("UG13", 1, company, period)
    return Node("REPORT", children=[system, basic, Node("DATA", children=rows)])


def _build_uchiwake(ctx):
    import uchiwake
    return build_ug13(uchiwake.build(ctx.year_dir, ctx.accounts), load_company_name(ctx.year_dir),
                      load_period(ctx.year_dir))


# ---- 概況書（UG16） ----

def build_ug16(g, company, period):
    """gaikyo.build の結果を UG16 の雛形に入れる。月別は _01〜_12（期間外の月は空のまま）。"""
    values = {("SYUYOUKAMOKU", tag): amount for tag, amount in g.main.items()}
    for n, month in enumerate(g.monthly, start=1):
        values.update({("TUKIBETUURIAGEDAKA", f"{tag}_{n:02d}"): amount for tag, amount in month.items()})
    return fill_skeleton(fill_header(load_skeleton("ug16"), company, period), values)


def _build_gaikyo(ctx):
    import gaikyo
    st = ctx.statements
    return build_ug16(gaikyo.build(ctx.year_dir, ctx.accounts, st), st.company, st.period)


# ---- 消費税（SY35） ----

# 区分（親要素）：(売上, 仕入, 貸倒) × 税率。税率の無い売上（免税・非課税・非課税資産輸出）は10%の区分
SY35_SECTIONS = {10: ("D020114", "D020124", "D020134"), 8: ("D020113", "D020123", "D020133")}
SY35_RATE_TAG = {10: "10", 8: "8K"}
SY35_ALLOCATION = {"課税対応": "KAZEI", "非課税対応": "HIKAZEI", "共通": "KYOTUU"}
SY35_CALC = {"割戻し": "WARIMODOSHI", "積上げ": "TSUMIAGE"}
SY35_NO_RATE_SALES = {"免税売上": "MENZEIURIAGEZEINUKI10", "非課税売上": "HIKAZEIURIAGEYUUKAIGAIZEINUKI10",
                      "非課税資産輸出": "HIKAZEISHISANYUSHUTSUZEINUKI10"}
# 課税売上・返還・貸倒は常に割戻しの欄（売上は割戻しで集計している：tax_summary と同じ）
SY35_SALES_PREFIX = {"課税売上": "KAZEIURIAGE", "課税売上返還": "KAZEIURIAGEHENKAN",
                     "貸倒": "KASHIDAORE", "貸倒回収": "KASHIDAOREKAISHU"}
SY35_PURCHASE_PREFIX = {"課税仕入": "SHIIRE", "課税仕入返還": "SHIIREHENKAN"}
SY35_FULL_INVOICES = ("", "適格", "少額特例", "制度前")  # 全額控除の欄
SY35_TRANSITION = {"経過80": "80", "経過50": "50"}  # 欄のある経過措置
SY35_NO_COLUMN = ("経過70", "経過30")  # SY35に欄が無い経過措置（達人に手で入れる）
SY35_MANUAL_METHODS = "簡易課税・2割特例は集計表を見て達人に手で入れる"


def _sy35_purchase_column(x, settings):
    """課税仕入・返還の行の (区分の要素名の頭, 末尾) か None（入れない行）。"""
    if x.invoice in SY35_NO_COLUMN or x.invoice == "なし":
        return None
    if settings.allocation == "個別対応":
        if x.allocation not in SY35_ALLOCATION:
            raise KessanError(f"個別対応方式なのに、{x.kind}（税区分 {x.category}・インボイス「{x.invoice}」）に"
                              "個別対応の区分（課税対応／非課税対応／共通）が入っていない行があります。直してから出し直してください")
        a = SY35_ALLOCATION[x.allocation]
    else:
        a = "KAZEI"
    head = SY35_PURCHASE_PREFIX[x.kind] + a
    if x.invoice in SY35_FULL_INVOICES:
        return head, SY35_CALC[settings.calc]
    if x.invoice in SY35_TRANSITION:
        return head, f"INVOICEIGAI_WARIMODOSHI{SY35_TRANSITION[x.invoice]}"
    raise KessanError(f"{x.category} のインボイス「{x.invoice}」は消費税XMLのどの欄に入れるか決まっていません（プログラムの誤り）")


def build_sy35(summary, settings, company, period):
    """消費税の集計（tax_summary.summarize の結果）を SY35 の雛形に入れる。(Node, 警告の一覧) を返す。

    各行の税抜を …ZEINUKI…、税額（控除割合を掛ける前）を …ZEIGAKU… に足し込む。税込の欄は空（会計ソフトの実物どおり）。
    経過70・30 は欄が無いので入れず警告、インボイスなし・非課税仕入は入れない。
    """
    values = {}
    warnings = []
    no_column = {}  # (区分, インボイス) → 税込
    transition_in_tsumiage = False

    def add(section, tag, amount):
        values[(section, tag)] = values.get((section, tag), 0) + amount

    for x in summary.lines:
        if x.kind in SY35_NO_RATE_SALES:
            add(SY35_SECTIONS[10][0], SY35_NO_RATE_SALES[x.kind], x.net)
            continue
        if x.kind not in SY35_SALES_PREFIX and x.kind not in SY35_PURCHASE_PREFIX:
            continue  # 非課税仕入
        sales_sec, purchase_sec, bad_debt_sec = SY35_SECTIONS[x.rate]
        r = SY35_RATE_TAG[x.rate]
        if x.kind in SY35_SALES_PREFIX:
            section = bad_debt_sec if x.kind.startswith("貸倒") else sales_sec
            head, tail = SY35_SALES_PREFIX[x.kind], "WARIMODOSHI"
        else:
            if x.invoice in SY35_NO_COLUMN:
                no_column[(x.kind, x.invoice)] = no_column.get((x.kind, x.invoice), 0) + x.gross
            column = _sy35_purchase_column(x, settings)
            if column is None:
                continue
            section, (head, tail) = purchase_sec, column
            if x.invoice in SY35_TRANSITION and settings.calc == "積上げ":
                transition_in_tsumiage = True
        add(section, f"{head}ZEINUKI{r}_{tail}", x.net)
        add(section, f"{head}ZEIGAKU{r}_{tail}", x.tax)

    if transition_in_tsumiage:
        warnings.append("積上げの年度の経過措置分は割戻しの欄に入れた（達人で確認）")
    for kind in SY35_PURCHASE_PREFIX:
        parts = [(label, no_column[(kind, label)]) for label in SY35_NO_COLUMN if (kind, label) in no_column]
        if parts:
            total = sum(a for _, a in parts)
            detail = "・".join(f"{label} {a:,}円" for label, a in parts)
            warnings.append(f"{kind}の経過措置70%・30%分（税込{total:,}円。うち{detail}）は"
                            "SY35に欄が無いため達人に手で入れる")
    return fill_skeleton(fill_header(load_skeleton("sy35"), company, period), values), warnings


def _build_shohizei(ctx):
    from tax import load_rates, load_tax_settings, load_transitions
    from tax_summary import summarize
    settings = load_tax_settings(ctx.year_dir)
    if not settings.taxable:
        raise NotApplicable("免税の年度です（period.yaml の消費税）")
    if settings.method != "原則":
        raise NotApplicable(f"{settings.method}の年度です。{SY35_MANUAL_METHODS}")
    summary = summarize(ctx.year_dir, ctx.accounts, settings, load_transitions(), load_rates())
    root, warnings = build_sy35(summary, settings, load_company_name(ctx.year_dir), load_period(ctx.year_dir))
    ctx.notes.extend(f"消費税XML: {w}" for w in warnings)
    return root


# ---- 書き出し（種類の登録と export） ----

@dataclass
class Context:
    """組み立て関数に渡す材料。Statements は最初に使うときに作る（種類どうしで使い回す）。"""
    year_dir: Path
    accounts: dict
    st: object = None
    notes: list = field(default_factory=list)  # 組み立て関数が出す警告・飛ばした理由

    @property
    def statements(self):
        if self.st is None:
            import financials
            self.st = financials.build_statements(self.year_dir, self.accounts)
        return self.st


@dataclass
class Kind:
    prefix: str  # GYOUMUPREFIX（ファイル名にも使う）
    build: object  # Context -> Node


class NotApplicable(Exception):
    """この年度には出さない種類（理由を持つ）。--kind 省略時は飛ばして理由を表示し、明示したら KessanError。"""


KINDS = {
    "kessan": Kind("HJ21", lambda ctx: build_hj21(ctx.statements)),
    "uchiwake": Kind("UG13", _build_uchiwake),
    "gaikyo": Kind("UG16", _build_gaikyo),
    "shohizei": Kind("SY35", _build_shohizei),
}


def output_path(year_dir, kind, prefix, period):
    return Path(year_dir) / OUTPUT_DIR / f"{kind}-{prefix}-{_yyyymmdd(period[1])}.xml"


def export(year_dir, accounts, kinds=None, *, st=None, notes=None):
    """指定した種類（None なら登録済みの全部）の達人XMLを output/tatsujin/ に書き、書いたパスを返す。

    全部を組み立て・エンコードしてから一時ファイル＋置き換えで書く（1つでも失敗したら何も書かない）。
    st：作成済みの Statements（渡せば作り直さない）。notes：組み立て中の警告・飛ばした理由を足すリスト。
    """
    year_dir = Path(year_dir)
    explicit = kinds is not None
    names = list(dict.fromkeys(kinds)) if explicit else list(KINDS)
    unknown = [k for k in names if k not in KINDS]
    if unknown:
        raise KessanError(f"達人XMLの種類 {', '.join(unknown)} は未対応です（対応: {', '.join(KINDS)}）")
    ctx = Context(year_dir, accounts, st)
    period = load_period(year_dir)
    built = []
    for name in names:
        kind = KINDS[name]
        try:
            root = kind.build(ctx)
        except NotApplicable as e:
            if explicit:
                raise KessanError(str(e)) from None
            ctx.notes.append(f"{name} は出しません: {e}")
            continue
        built.append((output_path(year_dir, name, kind.prefix, period), render(root)))
    if notes is not None:
        notes.extend(ctx.notes)
    if not built:
        return []
    out = year_dir / OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    for path, _ in built:
        if path.exists():
            try:
                with open(path, "ab"):
                    pass
            except OSError:
                raise cannot_write(f"{OUTPUT_DIR}/{path.name}") from None
    pairs = []
    try:
        try:
            for path, data in built:
                fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=out)
                pairs.append((Path(tmp), path))
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
        except OSError:
            raise cannot_write(f"{OUTPUT_DIR}/") from None
        swap_in(pairs, prefix=f"{OUTPUT_DIR}/")
    finally:
        for tmp, _ in pairs:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
    return [path for path, _ in built]
