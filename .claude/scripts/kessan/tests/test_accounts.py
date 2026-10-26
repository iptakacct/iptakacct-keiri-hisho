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


@pytest.mark.parametrize("name, category, side, section", [
    ("役員貸付金", "資産", "借", "流動資産"),
    ("有価証券", "資産", "借", "流動資産"),
    ("土地", "資産", "借", "有形固定資産"),
    ("投資有価証券", "資産", "借", "投資その他の資産"),
    ("出資金", "資産", "借", "投資その他の資産"),
    ("長期前払費用", "資産", "借", "投資その他の資産"),
    ("前受金", "負債", "貸", "流動負債"),
    ("資本準備金", "純資産", "貸", "株主資本"),
    ("受取配当金", "収益", "貸", "営業外収益"),
    ("有価証券売却益", "収益", "貸", "営業外収益"),
    ("固定資産売却益", "収益", "貸", "特別利益"),
    ("広告宣伝費", "費用", "借", "販売費及び一般管理費"),
    ("福利厚生費", "費用", "借", "販売費及び一般管理費"),
    ("修繕費", "費用", "借", "販売費及び一般管理費"),
    ("車両費", "費用", "借", "販売費及び一般管理費"),
    ("リース料", "費用", "借", "販売費及び一般管理費"),
    ("寄付金", "費用", "借", "販売費及び一般管理費"),
    ("有価証券売却損", "費用", "借", "営業外費用"),
    ("固定資産売却損", "費用", "借", "特別損失"),
])
def test_master_has_added_accounts(accounts, name, category, side, section):
    assert (accounts[name].category, accounts[name].normal_side, accounts[name].section) == (category, side, section)


# 区分（資産→負債→純資産→収益→費用）ごとに、決算書の表示区分の順に並ぶ
SECTION_ORDER = [
    "流動資産", "有形固定資産", "無形固定資産", "投資その他の資産", "流動負債", "固定負債", "株主資本",
    "売上高", "営業外収益", "特別利益", "販売費及び一般管理費", "営業外費用", "特別損失", "法人税等",
]


def test_master_is_grouped_by_category_and_section(accounts):
    order = [SECTION_ORDER.index(a.section) for a in accounts.values()]
    assert order == sorted(order)


def test_master_file_keeps_bom():
    from accounts import MASTER_PATH
    assert MASTER_PATH.read_bytes().startswith(b"\xef\xbb\xbf")


def test_default_tax_loaded(accounts):
    assert accounts["売上高"].default_tax == "課税売上10%"
    assert accounts["給料手当"].default_tax == "対象外"
    assert accounts["普通預金"].default_tax == ""
    assert accounts["固定資産売却益"].default_tax == ""
    assert accounts["固定資産売却損"].default_tax == ""


def test_invalid_default_tax_rejected(tmp_path):
    path = tmp_path / "master.csv"
    path.write_text("科目,区分,正常残高,表示区分,既定税区分\n売上高,収益,貸,売上高,課税10\n", encoding="utf-8-sig")
    with pytest.raises(KessanError, match="既定税区分"):
        load_accounts(path)


def test_new_master_accounts(accounts):
    a = accounts["未払消費税等"]
    assert (a.category, a.normal_side, a.section, a.default_tax) == ("負債", "貸", "流動負債", "")
    a = accounts["仮払税金"]
    assert (a.category, a.normal_side, a.section, a.default_tax) == ("資産", "借", "流動資産", "")


EXTRA_YAML = """\
accounts:
  - {科目: 業務委託料, 区分: 費用, 正常残高: 借, 表示区分: 販売費及び一般管理費, 既定税区分: 課税仕入10%}
  - {科目: 預り保証金, 区分: 負債, 正常残高: 貸, 表示区分: 固定負債}
"""


def write_extra(tmp_path, text):
    path = tmp_path / "kessan-accounts.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_extra_accounts_loaded(tmp_path):
    accounts = load_accounts(extra_path=write_extra(tmp_path, EXTRA_YAML))
    assert accounts["業務委託料"].default_tax == "課税仕入10%"
    assert accounts["預り保証金"].normal_side == "貸" and accounts["預り保証金"].default_tax == ""
    assert list(accounts)[-2:] == ["業務委託料", "預り保証金"]
    assert list(accounts)[:-2] == list(load_accounts())


def test_extra_path_missing_or_comments_only_adds_nothing(tmp_path):
    base = load_accounts()
    assert load_accounts(extra_path=tmp_path / "none.yaml") == base
    assert load_accounts(extra_path=write_extra(tmp_path, "# コメントだけ\n")) == base


def test_extra_account_same_name_rejected(tmp_path):
    path = write_extra(tmp_path, "accounts:\n  - {科目: 現金, 区分: 資産, 正常残高: 借, 表示区分: 流動資産}\n")
    with pytest.raises(KessanError, match="科目マスタと同じ名前の科目は足せない"):
        load_accounts(extra_path=path)


@pytest.mark.parametrize("item", [
    "{科目: 甲, 区分: 費用, 正常残高: 右, 表示区分: 販売費及び一般管理費}",
    "{科目: 甲, 区分: その他, 正常残高: 借, 表示区分: 販売費及び一般管理費}",
    "{科目: 甲, 区分: 費用, 正常残高: 借, 表示区分: 販売費及び一般管理費, 既定税区分: 課税10}",
    "{区分: 費用, 正常残高: 借, 表示区分: 販売費及び一般管理費}",
])
def test_extra_account_bad_value_rejected(tmp_path, item):
    with pytest.raises(KessanError, match="kessan-accounts.yaml"):
        load_accounts(extra_path=write_extra(tmp_path, f"accounts:\n  - {item}\n"))


def test_extra_file_with_wrong_root_rejected(tmp_path):
    with pytest.raises(KessanError, match="accounts:"):
        load_accounts(extra_path=write_extra(tmp_path, "- 業務委託料\n"))
