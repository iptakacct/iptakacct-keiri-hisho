"""勘定科目マスタ。科目の区分と、残高が正常に積み上がる側（借/貸）を持つ。"""
from dataclasses import dataclass
from pathlib import Path

import yaml

from common import KessanError, read_rows
from tax import TAX_CATEGORIES

MASTER_PATH = Path(__file__).with_name("accounts-master.csv")
CATEGORIES = ("資産", "負債", "純資産", "収益", "費用")


@dataclass(frozen=True)
class Account:
    name: str
    category: str
    normal_side: str
    section: str
    default_tax: str = ""


def _validate(acc, where):
    if acc.category not in CATEGORIES or acc.normal_side not in ("借", "貸"):
        raise KessanError(f"{where}の値が不正です: {acc}")
    if acc.default_tax and acc.default_tax not in TAX_CATEGORIES:
        raise KessanError(f"{where}の既定税区分が不正です（{acc.name}: {acc.default_tax}）")


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
                      _text(item.get("既定税区分")))
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
                      (r.get("既定税区分") or "").strip())
        _validate(acc, "科目マスタ")
        accounts[acc.name] = acc
    if extra_path is not None and Path(extra_path).exists():
        accounts.update(_load_extra(extra_path, accounts))
    return accounts


def normal_delta(account, debit, credit):
    return debit - credit if account.normal_side == "借" else credit - debit
