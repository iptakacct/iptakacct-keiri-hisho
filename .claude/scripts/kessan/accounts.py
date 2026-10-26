"""勘定科目マスタ。科目の区分と、残高が正常に積み上がる側（借/貸）を持つ。"""
from dataclasses import dataclass
from pathlib import Path

import yaml

from common import KessanError, read_rows
from tax import TAX_CATEGORIES

MASTER_PATH = Path(__file__).with_name("accounts-master.csv")
CATEGORIES = ("資産", "負債", "純資産", "収益", "費用")
# 表示区分 → 科目の区分（決算書の区分。マスタも会社別の科目もここにある表示区分だけ使える）
SECTIONS = {
    "流動資産": "資産", "有形固定資産": "資産", "無形固定資産": "資産", "投資その他の資産": "資産", "繰延資産": "資産",
    "流動負債": "負債", "固定負債": "負債",
    "株主資本": "純資産", "評価・換算差額等": "純資産",
    "売上高": "収益", "営業外収益": "収益", "特別利益": "収益",
    "売上原価": "費用", "販売費及び一般管理費": "費用", "営業外費用": "費用", "特別損失": "費用", "法人税等": "費用",
}
# 法人事業概況説明書に書く項目（科目の概況書項目に使える値）
GAIKYO_TAGS = (
    "現金預金", "受取手形", "売掛金", "棚卸資産", "貸付金", "建物", "機械装置", "車両船舶", "土地",
    "支払手形", "買掛金", "個人借入金", "その他借入金",
    "仕入", "労務費", "外注費", "役員報酬", "従業員給料", "人件費", "交際費", "減価償却費", "地代家賃", "租税公課", "支払利息",
)


@dataclass(frozen=True)
class Account:
    name: str
    category: str
    normal_side: str
    section: str
    default_tax: str = ""
    gaikyo: str = ""


def _validate(acc, where):
    if acc.category not in CATEGORIES or acc.normal_side not in ("借", "貸"):
        raise KessanError(f"{where}の値が不正です: {acc}")
    if acc.default_tax and acc.default_tax not in TAX_CATEGORIES:
        raise KessanError(f"{where}の既定税区分が不正です（{acc.name}: {acc.default_tax}）")
    if acc.section not in SECTIONS:
        raise KessanError(f"{where}の表示区分が不正です（{acc.name}: {acc.section}）。使える値: {'・'.join(SECTIONS)}")
    if SECTIONS[acc.section] != acc.category:
        raise KessanError(f"{where}の表示区分「{acc.section}」は区分「{acc.category}」の科目には使えません（{acc.name}）")
    if acc.gaikyo and acc.gaikyo not in GAIKYO_TAGS:
        raise KessanError(f"{where}の概況書項目が不正です（{acc.name}: {acc.gaikyo}）。使える値: {'・'.join(GAIKYO_TAGS)}")


def _text(value):
    return "" if value is None else str(value).strip()


def _load_extra(path, accounts):
    """会社別の追加科目（kessan-accounts.yaml）を accounts の後ろに足す。1件でも不正なら何も足さず例外。"""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise KessanError(f"{path.name} を読めません: {e}") from None
    except UnicodeDecodeError:
        raise KessanError(f"{path.name} が UTF-8 で読めません") from None
    except OSError as e:
        raise KessanError(f"{path.name} を開けません: {e}") from None
    if not isinstance(data, dict):
        raise KessanError(f"{path.name}: 「accounts:」の下に科目を並べる形で書く")
    items = data.get("accounts")
    if items is None:
        return {}
    if not isinstance(items, list):
        raise KessanError(f"{path.name}: accounts は「- {{科目: …, 区分: …}}」の並び（リスト）で書く")
    extra, errors = {}, []
    for i, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"{i}番目: 「科目: …」などの項目で書く")
            continue
        name = _text(item.get("科目"))
        where = f"{path.name} {i}番目（{name}）"
        acc = Account(name, _text(item.get("区分")), _text(item.get("正常残高")), _text(item.get("表示区分")),
                      _text(item.get("既定税区分")), _text(item.get("概況書項目")))
        try:
            if not name or not acc.section:
                raise KessanError(f"{where}: 科目と表示区分は必須です")
            _validate(acc, where)
            if name in accounts:
                raise KessanError(f"{where}: 科目マスタと同じ名前の科目は足せない")
            if name in extra:
                raise KessanError(f"{where}: 同じ名前の科目が重複しています")
        except KessanError as e:
            errors.append(str(e))
            continue
        extra[name] = acc
    if errors:
        raise KessanError("会社別の科目を読めません:\n" + "\n".join(errors))
    return extra


def load_accounts(path=MASTER_PATH, extra_path=None):
    accounts = {}
    for r in read_rows(path):
        acc = Account(r["科目"].strip(), r["区分"].strip(), r["正常残高"].strip(), r["表示区分"].strip(),
                      (r.get("既定税区分") or "").strip(), (r.get("概況書項目") or "").strip())
        _validate(acc, "科目マスタ")
        accounts[acc.name] = acc
    if extra_path is not None and Path(extra_path).exists():
        accounts.update(_load_extra(extra_path, accounts))
    return accounts


def normal_delta(account, debit, credit):
    return debit - credit if account.normal_side == "借" else credit - debit
