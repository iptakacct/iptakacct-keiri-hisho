"""消費税の設定・区分・経過措置（段階3a）。period.yaml の `消費税` を読む。免税の年度では他のキーを見ない。"""
from dataclasses import dataclass
from pathlib import Path

import yaml

from common import PERIOD_FILE, KessanError, parse_date

STATUSES = ("免税", "課税")
METHODS = ("原則", "簡易", "2割特例")
CALCS = ("割戻し", "積上げ")
ALLOCATIONS = ("全額控除", "個別対応", "一括比例配分")


@dataclass(frozen=True)
class TaxSettings:
    status: str
    method: str
    calc: str
    allocation: str
    small_exemption: bool

    @property
    def taxable(self):
        return self.status == "課税"


def _choice(raw, key, choices):
    if raw not in choices:
        raise KessanError(f"{PERIOD_FILE} の消費税: {key}は {'／'.join(choices)} のどれかで書いてください（現在: {raw!r}）")
    return raw


def parse_tax_settings(raw):
    """`消費税` の dict（またはNone）を検証して TaxSettings にする。免税なら他のキーは無視する。"""
    if raw is None:
        return TaxSettings("免税", "", "", "", False)
    if not isinstance(raw, dict):
        raise KessanError(f"{PERIOD_FILE} の消費税の形式が不正です（区分・方式・計算・控除・少額特例を書く）")
    status = _choice(raw.get("区分"), "区分", STATUSES)
    if status == "免税":
        return TaxSettings("免税", "", "", "", False)
    method = _choice(raw.get("方式"), "方式", METHODS)
    calc = _choice(raw.get("計算"), "計算", CALCS)
    allocation = ""
    if method == "原則":
        allocation = _choice(raw.get("控除"), "控除", ALLOCATIONS)
    small = raw.get("少額特例", False)
    if not isinstance(small, bool):
        raise KessanError(f"{PERIOD_FILE} の消費税: 少額特例は true／false で書いてください（現在: {small!r}）")
    return TaxSettings("課税", method, calc, allocation, small)


def tax_to_dict(settings):
    """period.yaml に書く形。免税は区分だけ。"""
    if not settings.taxable:
        return {"区分": "免税"}
    d = {"区分": "課税", "方式": settings.method, "計算": settings.calc}
    if settings.allocation:
        d["控除"] = settings.allocation
    d["少額特例"] = settings.small_exemption
    return d


def load_tax_settings(year_dir):
    path = Path(year_dir) / PERIOD_FILE
    if not path.exists():
        raise KessanError(f"{PERIOD_FILE} がありません（init --start --end で作成）: {Path(year_dir)}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise KessanError(f"{PERIOD_FILE} を読めません: {e}") from None
    if not isinstance(data, dict):
        raise KessanError(f"{PERIOD_FILE} の形式が不正です（期首日・期末日を書く）")
    return parse_tax_settings(data.get("消費税"))


# ---- 税区分 ----

@dataclass(frozen=True)
class TaxCategory:
    name: str
    kind: str
    rate: int | None


def _build_categories():
    cats = {}
    for kind in ("課税売上", "課税売上返還", "課税仕入", "課税仕入返還", "貸倒", "貸倒回収"):
        for rate, suffix in ((10, "10%"), (8, "8%軽")):
            cats[kind + suffix] = TaxCategory(kind + suffix, kind, rate)
    for kind in ("免税売上", "非課税売上", "非課税資産輸出", "非課税仕入", "対象外"):
        cats[kind] = TaxCategory(kind, kind, None)
    return cats


TAX_CATEGORIES = _build_categories()
INVOICE_VALUES = ("適格", "経過80", "経過70", "経過50", "経過30", "少額特例", "なし")
ALLOCATION_VALUES = ("課税対応", "非課税対応", "共通")


def with_rate(name, rate):
    """同じ kind で税率を替えた区分名。税率の無い区分はそのまま返す。"""
    cat = TAX_CATEGORIES.get(name)
    if cat is None or cat.rate is None:
        return name
    return cat.kind + ("10%" if rate == 10 else "8%軽")


# ---- インボイス経過措置 ----

TRANSITION_PATH = Path(__file__).parents[3] / "config/consumption-tax/invoice-transition.yaml"
SMALL_EXEMPTION_LIMIT = 10000  # 少額特例は税込1万円未満


@dataclass(frozen=True)
class Transition:
    start: str
    end: str
    label: str
    ratio: int


def load_transitions(path=TRANSITION_PATH):
    """経過措置の設定ファイルを読む。日付が不正・開始>終了・期間の重なりは止める。"""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise KessanError(f"経過措置の設定を読めません（{path}）: {e}") from None
    rows = data.get("経過措置") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise KessanError(f"経過措置の設定の形式が不正です（経過措置のリストが必要）: {path}")
    out = []
    for r in rows:
        if not isinstance(r, dict):
            raise KessanError(f"経過措置の設定の形式が不正です（開始・終了・区分・割合）: {path}")
        start, end = parse_date(r.get("開始")), parse_date(r.get("終了"))
        if not start or not end:
            raise KessanError(f"経過措置の開始・終了は YYYY-MM-DD で書いてください: {r}")
        if start > end:
            raise KessanError(f"経過措置の開始が終了より後です: {start}〜{end}")
        label, ratio = r.get("区分"), r.get("割合")
        if label not in INVOICE_VALUES or isinstance(ratio, bool) or not isinstance(ratio, int) \n                or not label.startswith("経過") or label != f"経過{ratio}":
            raise KessanError(f"経過措置の区分・割合が不正です: {r}")
        out.append(Transition(start, end, label, ratio))
    out.sort(key=lambda t: t.start)
    for a, b in zip(out, out[1:]):
        if b.start <= a.end:
            raise KessanError(f"経過措置の期間が重なっています: {a.start}〜{a.end} と {b.start}〜{b.end}")
    return out


def invoice_class(date, has_registration, amount, small_exemption, transitions):
    """仕入のインボイス区分。登録番号あり→適格、少額特例（税込1万円未満）、経過措置の期間、どれでもなければ なし。"""
    if has_registration:
        return "適格"
    if small_exemption and amount < SMALL_EXEMPTION_LIMIT:
        return "少額特例"
    for t in transitions:
        if t.start <= date <= t.end:
            return t.label
    return "なし"
