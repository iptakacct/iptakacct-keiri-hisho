"""グループ会社間の入出金を、未消込の計上と突き合わせる純粋関数。
ネットワーク・ファイルには触らない（仕訳の配列を受け取るだけ）。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence

# 未消込を探す範囲（直近6か月）
SETTLE_LOOKBACK_DAYS = 183

ASSET_ACCOUNTS = ("売掛金", "未収入金", "立替金", "仮払金", "前払金", "前渡金")
LIABILITY_ACCOUNTS = ("未払金", "未払費用", "買掛金", "預り金", "前受金", "仮受金")


def normalize_name(s: Any) -> str:
    """会社名の表記ゆれをそろえる：NFKC、㈱/(株)→株式会社、空白を除く。"""
    t = unicodedata.normalize("NFKC", str(s or ""))  # ㈱ は NFKC で (株) になる
    t = t.replace("(株)", "株式会社").replace("(有)", "有限会社").replace("(同)", "合同会社")
    return re.sub(r"\s+", "", t)


def is_group(sub_name: Any, group_names: Sequence[str]) -> bool:
    """補助科目名がグループ会社名（一覧）のいずれかと一致するか。推測で広げない（完全一致）。"""
    n = normalize_name(sub_name)
    if not n:
        return False
    return any(n == normalize_name(g) for g in group_names or [])


def _kind(account_name: str) -> Optional[str]:
    if account_name in ASSET_ACCOUNTS:
        return "asset"
    if account_name in LIABILITY_ACCOUNTS:
        return "liability"
    return None


def reduces_balance(account_name: str, line_side: str) -> bool:
    """その行が残高を減らすか（資産は貸方、負債は借方で減る）。消込科目でなければ False。"""
    kind = _kind(account_name)
    return (kind == "asset" and line_side == "credit") or (kind == "liability" and line_side == "debit")


OPENING = "JOURNAL_TYPE_OPENING"


def _opening_lines(j, account_id, sub_account_id, kind):
    """開始仕訳の行を、同じ補助科目の借方と貸方で差し引いてから返す。

    MFの開始仕訳は、1つの補助科目に借方行と貸方行を両方持つことがある（ある会社で
    借方9,478,447／貸方5,938,620＋1,448,947＝残高2,090,880 という補助科目があった）。行のまま
    突き合わせると期首残高が借方の額で残り、未消込を誤る。
    同額の増減は打ち消し、減る側が余れば増える側を1件にまとめて差し引く。
    減る側が余らなければ、増える行は1件ずつ残す（毎月同額の計上が2ヶ月分繰り越された場合など）。
    """
    inc, dec = [], []
    for b in j.get("branches") or []:
        for side in ("debitor", "creditor"):
            p = b.get(side) or {}
            if str(p.get("account_id")) != str(account_id):
                continue
            if str(p.get("sub_account_id") or "") != str(sub_account_id or ""):
                continue
            v = int(p.get("value") or 0) + int(p.get("tax_value") or 0)
            ((inc if (kind == "asset") == (side == "debitor") else dec)).append(v)
    for v in list(dec):
        if v in inc:
            inc.remove(v)
            dec.remove(v)
    d, n = str(j.get("transaction_date") or ""), j.get("number")
    if dec:
        net = sum(inc) - sum(dec)
        if net > 0:
            return [(d, n, net, True)]
        return [(d, n, -net, False)] if net < 0 else []
    return [(d, n, v, True) for v in inc]


def _signed_lines(journals, account_id, sub_account_id, account_name):
    """対象の科目×補助科目の行を (日付, 仕訳番号, 金額, 増えるか) で返す。"""
    kind = _kind(account_name)
    out = []
    for j in journals or []:
        if j.get("entered_by") == OPENING:
            out.extend(_opening_lines(j, account_id, sub_account_id, kind))
            continue
        for b in j.get("branches") or []:
            for side in ("debitor", "creditor"):
                p = b.get(side) or {}
                if str(p.get("account_id")) != str(account_id):
                    continue
                if str(p.get("sub_account_id") or "") != str(sub_account_id or ""):
                    continue
                increases = (kind == "asset") == (side == "debitor")
                out.append((str(j.get("transaction_date") or ""), j.get("number"),
                            int(p.get("value") or 0) + int(p.get("tax_value") or 0), increases))
    out.sort(key=lambda r: (r[0], str(r[1])))
    return out


def open_items(journals, account_id, sub_account_id, account_name, *, since: str) -> List[Dict[str, Any]]:
    """未消込の計上：同額の計上と消込を日付の古い順に1対1で対にし、残った計上（since 以降）。"""
    pending: List[Dict[str, Any]] = []
    for date, number, value, increases in _signed_lines(journals, account_id, sub_account_id, account_name):
        if increases:
            pending.append({"number": number, "date": date, "value": value})
            continue
        for i, item in enumerate(pending):  # 同額の最も古い計上と対にする
            if item["value"] == value:
                del pending[i]
                break
    return [i for i in pending if i["date"] >= since]


def balance(journals, account_id, sub_account_id, account_name) -> int:
    """渡された仕訳の範囲での増減の合計（増える向きを正）。"""
    return sum(v if inc else -v for _, _, v, inc in
               _signed_lines(journals, account_id, sub_account_id, account_name))


def matching(items: Sequence[Dict[str, Any]], value: int) -> List[Dict[str, Any]]:
    """未消込のうち、金額が明細とぴったり同じもの。"""
    return [i for i in items or [] if int(i.get("value") or 0) == int(value)]
