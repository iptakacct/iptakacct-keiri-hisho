"""消費税の集計表（段階3a）。journal.csv＋adjustments.csv の税区分つきの行から、区分ごとの税額と概算納付税額を出す。

金額は税込のまま帳簿に持つので、税抜額・税額は集計時に計算する（端数は切り捨て）。計算は整数と Decimal だけ（float は使わない）。
"""
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from common import KessanError, ensure_writable, parse_amount, replace_rows
from ledger import entry_rows, entry_where
from tax import TAX_CATEGORIES

# 貸方を＋とする区分（売上系）。それ以外（仕入系・返還・貸倒）は借方を＋とする
CREDIT_POSITIVE_KINDS = ("課税売上", "免税売上", "非課税売上", "非課税資産輸出", "貸倒回収")
KIND_ORDER = ("課税売上", "課税売上返還", "免税売上", "非課税売上", "非課税資産輸出", "貸倒", "貸倒回収",
              "課税仕入", "課税仕入返還", "非課税仕入")
PURCHASE_KINDS = ("課税仕入", "課税仕入返還")
FULL_RATIO = 100
TAXABLE_SALES_LIMIT = 500_000_000  # 課税売上高（税抜）がこれを超えると全額控除できない

SUMMARY_FILE = "consumption-tax-summary"
SUMMARY_COLUMNS = ["区分", "税区分", "税率", "インボイス", "個別対応", "件数", "税込", "税抜", "消費税額", "国税", "地方税",
                   "控除割合", "控除対象税額"]


@dataclass(frozen=True)
class SummaryLine:
    kind: str
    category: str
    rate: int | None
    invoice: str
    allocation: str
    count: int
    gross: int
    net: int
    tax: int
    national: int
    local: int
    ratio: int
    deductible: int


@dataclass
class TaxSummary:
    lines: list
    taxable_sales_ratio: Decimal
    estimate: int | None
    notes: list = field(default_factory=list)


def _sign(n):
    return -1 if n < 0 else 1


def _floor_mul_div(n, num, den):
    """floor(n × num ÷ den)。マイナスは絶対値で計算して符号を戻す（0に近い側へ切り捨てる）。"""
    return _sign(n) * (abs(n) * num // den)


def _tax_in(gross, rate):
    """税込額から消費税額を割り戻す。"""
    return _floor_mul_div(gross, rate, 100 + rate)


def _national(net, rates, rate):
    value = (Decimal(abs(net)) * rates[rate]["国税"] / 100).to_integral_value(rounding=ROUND_FLOOR)
    return _sign(net) * int(value)


def _ratio_of(invoice, ratios, where):
    """インボイス区分ごとの控除割合。空・適格・少額特例は100、なしは0、経過XXは設定ファイルの割合。"""
    if invoice in ("", "適格", "少額特例"):
        return FULL_RATIO
    if invoice == "なし":
        return 0
    if invoice in ratios:
        return ratios[invoice]
    raise KessanError(f"{where}: インボイス「{invoice}」の割合が経過措置の設定にありません")


def _collect(year_dir, settings):
    """(税区分, インボイス, 個別対応) ごとの税込合計・件数・（積上げなら）行ごとの税額の合計。"""
    groups = defaultdict(lambda: {"count": 0, "gross": 0, "row_tax": 0})
    for r in entry_rows(year_dir):
        where = entry_where(r)
        for side in ("借方", "貸方"):
            name = r[f"{side}税区分"].strip()
            category = TAX_CATEGORIES.get(name)
            if category is None:
                if name:
                    raise KessanError(f"{where}: {side}税区分「{name}」は使えない値です")
                continue
            if category.kind == "対象外":
                continue
            amount = parse_amount(r[f"{side}金額"], where)
            positive_side = "貸方" if category.kind in CREDIT_POSITIVE_KINDS else "借方"
            signed = amount if side == positive_side else -amount
            g = groups[(name, r[f"{side}インボイス"].strip(), r[f"{side}個別対応"].strip())]
            g["count"] += 1
            g["gross"] += signed
            if settings.calc == "積上げ" and category.kind in PURCHASE_KINDS:
                g["row_tax"] += _tax_in(signed, category.rate)
    return groups


def _build_lines(groups, settings, ratios, rates):
    lines = []
    for (name, invoice, allocation), g in groups.items():
        category = TAX_CATEGORIES[name]
        gross = g["gross"]
        if category.rate is None:
            tax, national, local = 0, 0, 0
            net = gross
        else:
            tax = g["row_tax"] if settings.calc == "積上げ" and category.kind in PURCHASE_KINDS else _tax_in(gross, category.rate)
            net = gross - tax
            if category.rate not in rates:
                raise KessanError(f"税率 {category.rate}% が税率の設定にありません")
            national = _national(net, rates, category.rate)
            local = tax - national
        ratio, deductible = FULL_RATIO, 0
        if category.kind in PURCHASE_KINDS:
            ratio = _ratio_of(invoice, ratios, f"{name}")
            deductible = _floor_mul_div(tax, ratio, 100)
        lines.append(SummaryLine(category.kind, name, category.rate, invoice, allocation, g["count"], gross, net, tax,
                                 national, local, ratio, deductible))
    lines.sort(key=lambda x: (KIND_ORDER.index(x.kind), x.category, x.invoice, x.allocation))
    return lines


def _sum(lines, kind, attr):
    return sum(getattr(x, attr) for x in lines if x.kind == kind)


def _deductible_total(lines, allocation=None):
    """課税仕入の控除対象税額（返還を引く）。allocation を指定するとその個別対応の行だけ。"""
    def pick(kind):
        return sum(x.deductible for x in lines if x.kind == kind and (allocation is None or x.allocation == allocation))
    return pick("課税仕入") - pick("課税仕入返還")


def summarize(year_dir, accounts, settings, transitions, rates):
    """課税の年度の集計。accounts は他の関数と形をそろえるための引数（集計では使わない）。"""
    ratios = {t.label: t.ratio for t in transitions}
    lines = _build_lines(_collect(year_dir, settings), settings, ratios, rates)
    notes = []
    if settings.calc == "積上げ":
        notes.append("売上は割戻しで計算（積上げは請求書ごとの税額が要るため未対応）")

    numerator = (_sum(lines, "課税売上", "net") - _sum(lines, "課税売上返還", "net")
                 + _sum(lines, "免税売上", "net") + _sum(lines, "非課税資産輸出", "net"))
    denominator = numerator + _sum(lines, "非課税売上", "net")
    ratio = Decimal(numerator) / Decimal(denominator) if denominator else Decimal(1)
    if not denominator:
        numerator = denominator = 1

    sales_tax = (_sum(lines, "課税売上", "tax") - _sum(lines, "課税売上返還", "tax")
                 - _sum(lines, "貸倒", "tax") + _sum(lines, "貸倒回収", "tax"))
    estimate = None
    if settings.method == "2割特例":
        estimate = sales_tax - _floor_mul_div(sales_tax, 80, 100)
    elif settings.method == "簡易":
        notes.append("簡易課税は事業区分が要るため概算しない（達人で計算）")
    else:
        if settings.allocation == "個別対応":
            deductible = _deductible_total(lines, "課税対応") + _floor_mul_div(
                _deductible_total(lines, "共通"), numerator, denominator)
        elif settings.allocation == "一括比例配分":
            deductible = _floor_mul_div(_deductible_total(lines), numerator, denominator)
        else:
            deductible = _deductible_total(lines)
            if _sum(lines, "課税売上", "net") - _sum(lines, "課税売上返還", "net") > TAXABLE_SALES_LIMIT:
                notes.append("課税売上高が5億円を超えているため全額控除はできません。個別対応方式か一括比例配分方式に直してください")
            if numerator * 100 < denominator * 95:
                notes.append(f"課税売上割合が95%未満（{ratio * 100:.2f}%）なのに控除が全額控除になっています。"
                             "個別対応方式か一括比例配分方式に直してください")
        estimate = sales_tax - deductible
    if any(x.invoice in ("経過70", "経過30") and x.count for x in lines):
        notes.append("経過措置70%・30%分は消費税の達人のXMLに欄が無いため、達人に手で入れる")
    return TaxSummary(lines, ratio, estimate, notes)


def _output_rows(summary):
    rows = []
    for x in summary.lines:
        rows.append({
            "区分": x.kind, "税区分": x.category, "税率": "" if x.rate is None else x.rate, "インボイス": x.invoice,
            "個別対応": x.allocation, "件数": x.count, "税込": x.gross, "税抜": x.net, "消費税額": x.tax,
            "国税": x.national, "地方税": x.local, "控除割合": x.ratio, "控除対象税額": x.deductible,
        })
    return rows


def _tail_rows(summary):
    """末尾の項目。区分に項目名、税区分に値を入れる。"""
    tail = [{"区分": "課税売上割合", "税区分": f"{summary.taxable_sales_ratio * 100:.2f}%"},
            {"区分": "概算納付税額", "税区分": "概算しない" if summary.estimate is None else summary.estimate}]
    tail += [{"区分": "注記", "税区分": n} for n in summary.notes]
    return tail


def write_summary(year_dir, summary):
    """output/consumption-tax-summary.csv と .xlsx（同じ内容）を書く。"""
    out = Path(year_dir) / "output"
    csv_path, xlsx_path = out / f"{SUMMARY_FILE}.csv", out / f"{SUMMARY_FILE}.xlsx"
    ensure_writable(year_dir, [f"output/{csv_path.name}"])
    rows = _output_rows(summary) + _tail_rows(summary)
    wb = Workbook()
    ws = wb.active
    ws.title = "消費税の集計表"
    ws.append(SUMMARY_COLUMNS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for row in rows:
        ws.append([row.get(c, "") for c in SUMMARY_COLUMNS])
    out.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(xlsx_path)
    except OSError:
        raise KessanError(f"{xlsx_path.name} に書き込めません（Excelで開いていたら閉じてから再実行）") from None
    replace_rows(csv_path, SUMMARY_COLUMNS, rows)
    return csv_path, xlsx_path
