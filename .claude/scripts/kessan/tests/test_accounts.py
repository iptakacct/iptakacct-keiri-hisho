import pytest

from accounts import load_accounts, normal_delta
from common import KessanError


def test_master_has_basic_accounts(accounts):
    assert accounts["普通預金"].category == "資産"
    assert accounts["普通預金"].normal_side == "借"
    assert accounts["未払金"].normal_side == "貸"
    assert accounts["減価償却累計額"].normal_side == "貸"
    assert accounts["繰越利益剰余金"].category == "純資産"
    assert accounts["法人税等"].category == "費用"


def test_master_values_are_valid(accounts):
    assert {a.category for a in accounts.values()} == {"資産", "負債", "純資産", "収益", "費用"}
    assert {a.normal_side for a in accounts.values()} == {"借", "貸"}


def test_master_order_is_kept(accounts):
    names = list(accounts)
    assert names.index("普通預金") < names.index("未払金") < names.index("資本金")
    assert names.index("売上高") < names.index("支払手数料") < names.index("法人税等")


def test_normal_delta(accounts):
    assert normal_delta(accounts["普通預金"], debit=100, credit=30) == 70
    assert normal_delta(accounts["未払金"], debit=100, credit=30) == -70


def test_invalid_master_raises(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("科目,区分,正常残高,表示区分\n現金,資産,右,流動資産\n", encoding="utf-8-sig")
    with pytest.raises(KessanError, match="科目マスタ"):
        load_accounts(path)
