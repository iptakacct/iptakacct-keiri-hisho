"""消費税の設定・区分・経過措置（段階3a）。period.yaml の `消費税` を読む。免税の年度では他のキーを見ない。"""
from dataclasses import dataclass
from pathlib import Path

import yaml

from common import PERIOD_FILE, KessanError

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
