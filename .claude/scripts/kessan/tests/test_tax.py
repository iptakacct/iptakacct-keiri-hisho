import pytest

from common import KessanError, init_year_dir, load_period
from tax import TaxSettings, load_tax_settings

TAXABLE = {"区分": "課税", "方式": "原則", "計算": "割戻し", "控除": "全額控除", "少額特例": False}


def write_period(year_dir, extra=""):
    year_dir.mkdir(parents=True, exist_ok=True)
    (year_dir / "period.yaml").write_text(
        '期首日: "2025-04-01"\n期末日: "2026-03-31"\n' + extra, encoding="utf-8")


def test_default_is_exempt(tmp_path):
    write_period(tmp_path)
    s = load_tax_settings(tmp_path)
    assert s == TaxSettings("免税", "", "", "", False)
    assert not s.taxable


def test_load_taxable_settings(tmp_path):
    write_period(tmp_path, "消費税:\n  区分: 課税\n  方式: 原則\n  計算: 割戻し\n  控除: 全額控除\n  少額特例: true\n")
    s = load_tax_settings(tmp_path)
    assert s == TaxSettings("課税", "原則", "割戻し", "全額控除", True)
    assert s.taxable


def test_invalid_value_rejected(tmp_path):
    write_period(tmp_path, "消費税:\n  区分: 課税事業者\n")
    with pytest.raises(KessanError, match="区分は 免税／課税"):
        load_tax_settings(tmp_path)


@pytest.mark.parametrize("key,value,msg", [
    ("方式", "本則", "方式は 原則／簡易／2割特例"),
    ("計算", "積み上げ", "計算は 割戻し／積上げ"),
    ("控除", "なし", "控除は 全額控除／個別対応／一括比例配分"),
    ("少額特例", "はい", "少額特例は true／false"),
])
def test_invalid_other_values_rejected(tmp_path, key, value, msg):
    s = dict(TAXABLE, **{key: value})
    init_dir = tmp_path / "y"
    write_period(init_dir)
    import yaml
    (init_dir / "period.yaml").write_text(
        '期首日: "2025-04-01"\n期末日: "2026-03-31"\n' + yaml.safe_dump({"消費税": s}, allow_unicode=True),
        encoding="utf-8")
    with pytest.raises(KessanError, match=msg):
        load_tax_settings(init_dir)


def test_kanni_without_allocation_ok(tmp_path):
    write_period(tmp_path, "消費税:\n  区分: 課税\n  方式: 簡易\n  計算: 割戻し\n")
    s = load_tax_settings(tmp_path)
    assert s.method == "簡易" and s.allocation == "" and s.small_exemption is False


def test_gensoku_requires_allocation(tmp_path):
    write_period(tmp_path, "消費税:\n  区分: 課税\n  方式: 原則\n  計算: 割戻し\n")
    with pytest.raises(KessanError, match="控除"):
        load_tax_settings(tmp_path)


def test_exempt_ignores_other_keys(tmp_path):
    write_period(tmp_path, "消費税:\n  区分: 免税\n  方式: でたらめ\n")
    assert load_tax_settings(tmp_path) == TaxSettings("免税", "", "", "", False)


def test_init_writes_tax_and_period_still_loads(tmp_path):
    y = tmp_path / "y"
    init_year_dir(y, "2025-04-01", "2026-03-31")
    init_year_dir(y, "2025-04-01", "2026-03-31", tax=TAXABLE)
    assert load_period(y) == ("2025-04-01", "2026-03-31")
    assert load_tax_settings(y) == TaxSettings("課税", "原則", "割戻し", "全額控除", False)


def test_init_rejects_invalid_tax_before_writing(tmp_path):
    y = tmp_path / "y"
    with pytest.raises(KessanError):
        init_year_dir(y, "2025-04-01", "2026-03-31", tax=dict(TAXABLE, 方式="本則"))
    assert not (y / "period.yaml").exists()
