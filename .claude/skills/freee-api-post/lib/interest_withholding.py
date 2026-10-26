"""預金利息の源泉税を「振替伝票」で別建て起票するための純粋関数群（全freee社共通）。

背景：
純額入金型の銀行口座（源泉税を差し引いた後の金額だけが入金される。例：ネット銀行）は、
入金額をそのまま受取利息にすると総額決算・所得税額控除の把握がずれる。一方 freee の
自動登録ルールには動的な金額計算が無いため、割戻しをルールに埋め込めない。

そこで2本立てにする：
  ① 明細は自動登録ルール（act=1）で「受取利息1行（入金額そのまま）」として自動計上
  ② 源泉分だけを振替伝票で追加計上する ← このモジュールが対象を見つけて起票内容を組む
      借方 法人税・住民税及び事業税（品目「利息源泉」）／貸方 受取利息
合計すると受取利息＝総額になり、従来の「1取引・2明細」と同じ残高になる。

総額入金型の口座（利息の総額が入金され、同日・同口座に「国税」等の出金明細が別に立つ。
例：一部のネット銀行）は割戻し不要なので、対象から除外する。

API呼び出し・ファイルI/Oは freee_cli.py（interestサブコマンド）が持ち、ここには置かない。
"""
from datetime import datetime

# 復興特別所得税込みの源泉徴収率 15.315%（所得税15% + 復興特別所得税0.315%）。
# 地方税の利子割5%は2016年度に廃止済みで、法人の預金利息はこの国税のみが源泉徴収される。
RATE_NUM, RATE_DEN = 15315, 100000
NET_NUM, NET_DEN = RATE_DEN - RATE_NUM, RATE_DEN   # 84685 / 100000

INTEREST_ACCOUNT_NAME = "受取利息"
# 借方の科目。会社によって名前が違うので、この順に探して最初に見つかったものを使う。
TAX_ACCOUNT_NAMES = ("法人税・住民税及び事業税", "法人税等")
# 品目（補助科目相当）。会社の過去の利息仕訳から推定できなかった時だけ、この順に名前で探す。
# 「源泉所得税」は給与・報酬の源泉にも使われる汎用的な名前なので、専用の名前の後に置く。
WITHHOLDING_ITEM_NAMES = ("利息源泉", "利息源泉税", "預金源泉", "源泉所得税")
# 同日・同口座にこれらを含む出金明細があれば「総額入金型」とみなし、割戻しをしない。
GROSS_TYPE_KEYWORDS = ("国税", "所得税", "利子税", "源泉")
# 税金の出金明細が利息の入金と別日に立つ銀行もあるため、金額が一致する場合はこの日数まで許容する。
# 摘要だけで判定するときは同日のみ（無関係な納税明細を掴まないため）。
GROSS_TYPE_DATE_TOLERANCE_DAYS = 3

EXEMPT_TAX_NAME = "対象外"


def gross_from_net(net):
    """税引後の入金額から、源泉徴収前の総額を返す（総額 = floor(入金額 ÷ 0.84685)）。

    整数演算で行う（浮動小数点の丸め誤差で1円ずれるのを避けるため）。
    実運用（純額入金型の口座）の実績11件すべてで、実際に登録されていた総額と一致することを
    確認した式。"""
    if net <= 0:
        return 0
    return net * NET_DEN // NET_NUM


def withholding_from_net(net):
    """税引後の入金額から、源泉徴収税額を返す。"""
    return gross_from_net(net) - net


def is_consistent(net):
    """逆算した総額が、順算（総額×15.315%の1円未満切り捨て）と整合するか。

    1円未満切り捨てのため、入金額から総額が一意に定まらない場合がある。整合しない
    入金額は自動起票せず「要確認」に回すためのガード。"""
    gross = gross_from_net(net)
    if gross <= 0:
        return False
    return gross * RATE_NUM // RATE_DEN == gross - net


def find_account_id(account_items, names):
    """勘定科目一覧から、names の順に名前が一致するものを探して (id, name) を返す。無ければ (None, None)。"""
    by_name = {a.get("name"): a.get("id") for a in account_items}
    for name in names:
        if by_name.get(name):
            return by_name[name], name
    return None, None


def find_item_id(items, names):
    """品目一覧から、names の順に名前が一致するものを探して (id, name) を返す。無ければ (None, None)。"""
    by_name = {i.get("name"): i.get("id") for i in items}
    for name in names:
        if by_name.get(name):
            return by_name[name], name
    return None, None


def infer_item_id_from_history(deals, interest_account_ids, tax_account_ids):
    """過去の利息仕訳（受取利息と法人税系が同居する取引）の借方に付いている品目を推定する。

    品目名は会社ごとに違う（例：「利息源泉」「源泉所得税」）ため、
    名前の決め打ちより、その会社が実際に使ってきた品目に合わせるほうが確実。
    最も多く使われている品目IDを返す。履歴が無ければ None。"""
    interest_account_ids = set(interest_account_ids)
    tax_account_ids = set(tax_account_ids)
    counts = {}
    for deal in deals:
        details = deal.get("details") or []
        if not any(d.get("account_item_id") in interest_account_ids for d in details):
            continue
        for detail in details:
            if detail.get("account_item_id") in tax_account_ids and detail.get("item_id"):
                counts[detail["item_id"]] = counts.get(detail["item_id"], 0) + 1
    if not counts:
        return None
    return max(sorted(counts), key=lambda item_id: counts[item_id])


def find_tax_code(tax_codes, name_ja):
    """税区分一覧（GET /api/1/taxes/codes）から name_ja に一致するコードを返す。無ければ None。"""
    for tax in tax_codes:
        if tax.get("name_ja") == name_ja:
            return tax.get("code")
    return None


def _interest_lines(deal, interest_account_ids):
    return [d for d in (deal.get("details") or [])
            if d.get("account_item_id") in interest_account_ids and d.get("entry_side") == "credit"]


def _deal_walletable_id(deal):
    payments = deal.get("payments") or []
    if payments:
        return payments[0].get("from_walletable_id") or payments[0].get("to_walletable_id")
    return None


def withholding_on_gross(gross):
    """総額に対する源泉税額（総額 × 15.315% の1円未満切り捨て）。"""
    if gross <= 0:
        return 0
    return gross * RATE_NUM // RATE_DEN


def _date_diff_days(a, b):
    """YYYY-MM-DD 同士の日数差の絶対値。片方でも読めなければ None。"""
    try:
        da = datetime.strptime(str(a)[:10], "%Y-%m-%d").date()
        db = datetime.strptime(str(b)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    return abs((da - db).days)


def has_gross_type_marker(wallet_txns, date, walletable_id, deposit=None):
    """総額入金型の口座か（利息の総額が入金され、源泉税が別の出金明細で引かれる型）。

    一部のネット銀行のように「利息の入金」と「税金の出金」が**2本の明細**で出てくる銀行がある。
    この場合、入金額がすでに総額なので割り戻してはいけない（割り戻すと受取利息・法人税等が
    ともに過大になる。実運用で過大計上を実際に発見したことがある）。

    判定は2通り。誤って本物の納税明細を掴まないよう、**金額の一致を主**に置く：
      1. 同じ口座で、日付が ±GROSS_TYPE_DATE_TOLERANCE_DAYS 以内の出金明細のうち、
         金額が「入金額 × 15.315%（1円未満切り捨て）」と一致するもの（摘要は問わない）
      2. 同じ口座・同じ日で、摘要に税金を示す語（GROSS_TYPE_KEYWORDS）を含む出金明細
    金額が分からない場合（deposit が None）は 2 だけで判定する。

    口座が特定できない取引（walletable_id が None）では、日付だけで判定すると別口座の
    納税明細を拾ってしまうため、判定しない（False を返す）。"""
    if walletable_id is None:
        return False
    expected_tax = withholding_on_gross(deposit) if deposit else None
    for txn in wallet_txns:
        if txn.get("walletable_id") != walletable_id or txn.get("entry_side") != "expense":
            continue
        amount = txn.get("amount") or 0
        description = txn.get("description") or ""
        if expected_tax and amount == expected_tax:
            diff = _date_diff_days(txn.get("date"), date)
            if diff is not None and diff <= GROSS_TYPE_DATE_TOLERANCE_DAYS:
                return True
        if txn.get("date") == date and any(k in description for k in GROSS_TYPE_KEYWORDS):
            return True
    return False


def deal_marker(deal_id):
    """振替伝票の摘要に入れる、対象取引の目印。次回以降の重複起票を防ぐために使う。"""
    return f"対象取引{deal_id}"


def already_journaled(manual_journals, deal, interest_account_ids, tax_account_ids, withholding):
    """この取引に対する源泉振替が既に起票済みか。

    2通りで判定する：
      1. 摘要に対象取引の目印（deal_marker）がある伝票
      2. 同じ日付で「貸方 受取利息 × 借方 法人税系」の組み合わせ、かつ金額が一致する伝票
         （目印を持たない手起票の伝票を二重に起こさないための保険）"""
    marker = deal_marker(deal.get("id"))
    issue_date = deal.get("issue_date")
    for journal in manual_journals:
        details = journal.get("details") or []
        if any(marker in (d.get("description") or "") for d in details):
            return True
        if journal.get("issue_date") != issue_date:
            continue
        credit = sum(d.get("amount") or 0 for d in details
                     if d.get("account_item_id") in interest_account_ids and d.get("entry_side") == "credit")
        debit = sum(d.get("amount") or 0 for d in details
                    if d.get("account_item_id") in tax_account_ids and d.get("entry_side") == "debit")
        if credit and credit == debit == withholding:
            return True
    return False


def find_targets(deals, manual_journals, wallet_txns, interest_account_ids, tax_account_ids):
    """源泉振替を起票すべき取引と、起票しなかった取引（理由付き）を返す。

    戻り値: {"targets": [...], "excluded": [...]}
      targets の各要素: {"deal_id", "issue_date", "net", "gross", "withholding", "tax_code",
                        "partner_id", "walletable_id"}
      excluded の各要素: {"deal_id", "issue_date", "net", "reason"}
        reason: already_gross（既に総額で計上済み）／gross_type（総額入金型の口座）／
                already_journaled（起票済み）／too_small（源泉0円）／inconsistent（逆算が不整合）
    """
    interest_account_ids = set(interest_account_ids)
    tax_account_ids = set(tax_account_ids)
    targets, excluded = [], []
    for deal in sorted(deals, key=lambda d: (d.get("issue_date") or "", d.get("id") or 0)):
        lines = _interest_lines(deal, interest_account_ids)
        if not lines:
            continue
        net = sum(line.get("amount") or 0 for line in lines)
        base = {"deal_id": deal.get("id"), "issue_date": deal.get("issue_date"), "net": net}
        if any(d.get("account_item_id") in tax_account_ids for d in (deal.get("details") or [])):
            excluded.append({**base, "reason": "already_gross"})
            continue
        walletable_id = _deal_walletable_id(deal)
        if has_gross_type_marker(wallet_txns, deal.get("issue_date"), walletable_id, net):
            excluded.append({**base, "reason": "gross_type"})
            continue
        withholding = withholding_from_net(net)
        if withholding <= 0:
            excluded.append({**base, "reason": "too_small"})
            continue
        if already_journaled(manual_journals, deal, interest_account_ids, tax_account_ids, withholding):
            excluded.append({**base, "reason": "already_journaled"})
            continue
        if not is_consistent(net):
            excluded.append({**base, "reason": "inconsistent"})
            continue
        targets.append({**base,
                        "gross": gross_from_net(net),
                        "withholding": withholding,
                        "tax_code": lines[0].get("tax_code"),
                        "partner_id": lines[0].get("partner_id") or deal.get("partner_id"),
                        "walletable_id": walletable_id})
    return {"targets": targets, "excluded": excluded}


def build_journal_payload(company_id, target, interest_account_id, tax_account_id,
                          exempt_tax_code, item_id=None):
    """POST /api/1/manual_journals に送る本体を組み立てる。

    借方 法人税系（対象外・品目「利息源泉」）／貸方 受取利息（取引の利息行と同じ税区分）。"""
    note = (f"普通預金利息 源泉所得税（総額{target['gross']}円・入金{target['net']}円・"
            f"{deal_marker(target['deal_id'])}）")
    debit = {"entry_side": "debit", "account_item_id": tax_account_id, "tax_code": exempt_tax_code,
             "amount": target["withholding"], "description": note}
    credit = {"entry_side": "credit", "account_item_id": interest_account_id,
              "tax_code": target["tax_code"], "amount": target["withholding"], "description": note}
    if item_id:
        debit["item_id"] = item_id
    if target.get("partner_id"):
        debit["partner_id"] = target["partner_id"]
        credit["partner_id"] = target["partner_id"]
    return {"company_id": company_id, "issue_date": target["issue_date"],
            "adjustment": False, "details": [debit, credit]}


SKIP_LABELS = {
    "already_gross": "総額で計上済み",
    "gross_type": "総額入金型の口座（割戻し不要）",
    "already_journaled": "源泉振替が起票済み",
    "too_small": "源泉0円",
    "inconsistent": "逆算が不整合（要確認）",
}

# 人の判断が要るもの（Slackで報告する）。それ以外のスキップは正常なので黙って通す。
ATTENTION_REASONS = ("inconsistent",)


def build_report(company_name, registered, attention):
    """Slack投稿用の本文を返す。報告するものが無ければ空文字。

    registered: 起票した伝票 [{"issue_date", "net", "gross", "withholding", "journal_id"}]
    attention:  人の判断が要るもの [{"issue_date", "net", "reason"}]（inconsistent 等）"""
    if not registered and not attention:
        return ""
    lines = [f"■ 預金利息の源泉振替（{company_name}）"]
    for item in registered:
        lines.append(f"・{item['issue_date']} 入金{item['net']}円 → 総額{item['gross']}円／"
                     f"源泉{item['withholding']}円を起票（伝票id {item['journal_id']}）")
    for item in attention:
        lines.append(f"・{item['issue_date']} 入金{item['net']}円："
                     f"{SKIP_LABELS.get(item['reason'], item['reason'])}")
    return "\n".join(lines)
