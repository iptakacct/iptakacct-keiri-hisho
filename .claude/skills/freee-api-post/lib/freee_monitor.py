"""freee 未処理明細・未添付証憑の無人監視（フェーズ3）。

役割は「登録」ではなく「気づかせる」こと。freee APIから取得したデータ（APIレスポンスと
同じ構造のdict/list）を受け取る純粋関数だけを置き、API呼び出し・ファイルI/Oは
freee_cli.py（monitorサブコマンド）が担当する。運用方針は
`.claude/rules/output-format.md`「freee会計の自動処理方針」参照。

検知するもの（会社ごと）：
1. 未処理明細の滞留：status=1（消込待ち）の明細のうち min_days 以上経過したもの
2. 初めて見る摘要パターン：正規化した摘要が状態ファイルの known_patterns に無いもの
   （accounting-rules.md に確立パターンとして追記する候補）
3. ファイルボックスの未添付証憑：どの取引にも紐づいていない receipts。メタデータ
   （金額・日付）があるものは、証憑なしの取引と金額一致・日付近傍で「添付候補」を出す
4. 口座連携の異常：walletables に同期状態の項目があり、かつ synced でないもの、および
   API連携口座なのに最新の明細日付が max_days より古いもの（明細の途絶を代替指標にする。
   freee API の walletables には同期状態・最終同期日時が無く、update_date は同期しても
   変わらないことを実機で確認済み）

同じものを毎日繰り返し報告しないため、報告済みID・既知パターンを状態（state）に持つ。
"""
import re
from datetime import date, datetime, timedelta

UNTREATED_STATUS = 1          # freee wallet_txns.status: 1=消込待ち（未処理）
RECEIPT_SKIP_STATUSES = {"deleted", "ignored"}
ORIGIN_LABELS = {"public_api": "API一括登録", "mobile_camera": "スマホ撮影", "web": "Web", "email": "メール",
                 "unknown": "不明"}


def normalize_description(text):
    """摘要の照合用に、空白・数字（月替わりで変わる部分）・記号を取り除いた文字列を返す。
    全角数字・全角空白も対象。"""
    if not text:
        return ""
    text = text.translate(str.maketrans("０１２３４５６７８９　", "0123456789 "))
    return re.sub(r"[\s0-9/\-\.:]", "", text)


def _parse_date(value):
    if not value:
        return None
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def find_stale_untreated(wallet_txns, today=None, min_days=3):
    """未処理（status=1）で、明細日付から min_days 以上経過したものを古い順に返す。"""
    today = today or date.today()
    stale = []
    for txn in wallet_txns:
        if txn.get("status") != UNTREATED_STATUS:
            continue
        txn_date = _parse_date(txn.get("date"))
        if txn_date is None:
            continue
        if (today - txn_date).days >= min_days:
            stale.append(txn)
    stale.sort(key=lambda t: (t.get("date") or "", t.get("id") or 0))
    return stale


def find_new_patterns(wallet_txns, known_patterns):
    """正規化した摘要が known_patterns に無いものを、パターンごとに1件にまとめて返す。
    戻り値: [{"pattern", "sample"（最初の明細）, "count"}]"""
    found = {}
    for txn in wallet_txns:
        pattern = normalize_description(txn.get("description"))
        if not pattern or pattern in known_patterns:
            continue
        if pattern not in found:
            found[pattern] = {"pattern": pattern, "sample": txn, "count": 0}
        found[pattern]["count"] += 1
    return list(found.values())


def find_unattached_receipts(receipts, deals):
    """どの取引の receipts[] にも含まれていない証憑を返す（削除済み・無視済みは除く）。"""
    attached = set()
    for deal in deals:
        for r in deal.get("receipts") or []:
            attached.add(r.get("id"))
    return [r for r in receipts
            if r.get("id") not in attached and r.get("status") not in RECEIPT_SKIP_STATUSES]


def match_receipt_candidates(unattached_receipts, deals, window_days=3):
    """メタデータ（金額・日付）を持つ未添付証憑について、証憑なしの取引のうち金額が一致し
    日付が window_days 以内のものを添付候補として返す。
    戻り値: [{"receipt", "deals": [...]}]（候補のある証憑のみ）"""
    deals_without_receipts = [d for d in deals if not (d.get("receipts") or [])]
    candidates = []
    for receipt in unattached_receipts:
        meta = receipt.get("receipt_metadatum") or {}
        amount = meta.get("amount")
        issue_date = _parse_date(meta.get("issue_date"))
        if amount is None or issue_date is None:
            continue
        matched = []
        for deal in deals_without_receipts:
            deal_date = _parse_date(deal.get("issue_date"))
            if deal_date is None or deal.get("amount") != amount:
                continue
            if abs((deal_date - issue_date).days) <= window_days:
                matched.append(deal)
        if matched:
            candidates.append({"receipt": receipt, "deals": matched})
    return candidates


CONNECTED_TYPES = {"bank_account", "credit_card"}


def check_walletables_sync(walletables):
    """同期状態の項目（sync_status）があり、かつ synced でない口座を返す。項目が無い
    APIバージョンでは判定できないので何も返さない（誤検知より無報告を選ぶ）。
    実際のAPI応答にはこの項目が無いため、find_stalled_walletables と併用する。"""
    issues = []
    for w in walletables:
        status = w.get("sync_status")
        if status is not None and str(status).lower() != "synced":
            issues.append({**w, "reason": str(status)})
    return issues


def find_stalled_walletables(walletables, txns, today=None, max_days=30):
    """API連携口座（bank_account / credit_card で bank_id あり）のうち、取得期間内に明細が
    あるのに最新の明細日付が max_days より古いもの（＝動いていた口座が止まった）を返す。
    連携切れ・同期停止の代替指標なので報告は「要確認」の扱い。取得期間内に明細が1件も無い
    口座は、使っていないだけか連携切れかを区別できないので報告しない（未使用カードが多い会社で
    雑音になるため）。"""
    today = today or date.today()
    latest = {}
    for t in txns:
        key = (t.get("walletable_type"), t.get("walletable_id"))
        d = t.get("date")
        if d and (key not in latest or d > latest[key]):
            latest[key] = d
    issues = []
    for w in walletables:
        if w.get("type") not in CONNECTED_TYPES or not w.get("bank_id"):
            continue
        d = latest.get((w.get("type"), w.get("id")))
        if d is None:
            continue
        days = (today - date.fromisoformat(d)).days
        if days > max_days:
            issues.append({**w, "last_txn_date": d, "reason": f"最新明細 {d}、{days}日間更新なし"})
    return issues


def _fmt_amount(value):
    try:
        return f"{int(value):,}円"
    except (TypeError, ValueError):
        return f"{value}円"


def _receipt_label(receipt):
    meta = receipt.get("receipt_metadatum") or {}
    parts = [f"証憑ID {receipt.get('id')}"]
    if meta.get("issue_date"):
        parts.append(str(meta["issue_date"])[:10])
    if meta.get("amount") is not None:
        parts.append(_fmt_amount(meta["amount"]))
    if meta.get("partner_name"):
        parts.append(str(meta["partner_name"]))
    if not meta:
        parts.append("メタデータなし・目視要")
    return " / ".join(parts)


def build_company_report(company_name, stale, new_patterns, unattached, candidates,
                         sync_issues, state, max_lines=8):
    """1社分の報告文（Slack mrkdwn）と更新後のstateを返す。報告するものが無ければ text=None。
    state: {"reported_txn_ids": [...], "reported_receipt_ids": [...], "known_patterns": [...]}"""
    reported_txn = set(state.get("reported_txn_ids") or [])
    reported_receipt = set(state.get("reported_receipt_ids") or [])
    known = set(state.get("known_patterns") or [])

    lines = []

    new_stale = [t for t in stale if t.get("id") not in reported_txn]
    if new_stale:
        lines.append(f"• 未処理明細：新規{len(new_stale)}件（残{len(stale)}件、最古 {stale[0].get('date')}）")
        for t in new_stale[:max_lines]:
            lines.append(f"    - {t.get('date')} {_fmt_amount(t.get('amount'))} {t.get('description') or '(摘要なし)'}")
        if len(new_stale) > max_lines:
            lines.append(f"    - …他{len(new_stale) - max_lines}件")

    fresh_patterns = [p for p in new_patterns if p["pattern"] not in known]
    if fresh_patterns:
        lines.append(f"• 初めて見る摘要パターン：{len(fresh_patterns)}件（accounting-rules.md への追記候補）")
        for p in fresh_patterns[:max_lines]:
            s = p["sample"]
            lines.append(f"    - 「{s.get('description')}」 {s.get('date')} {_fmt_amount(s.get('amount'))}（{p['count']}件）")

    candidate_by_receipt = {c["receipt"].get("id"): c["deals"] for c in candidates}
    new_unattached = [r for r in unattached if r.get("id") not in reported_receipt]
    if new_unattached:
        origins = {}
        for r in unattached:
            origins[r.get("origin") or "unknown"] = origins.get(r.get("origin") or "unknown", 0) + 1
        origin_desc = "、".join(f"{ORIGIN_LABELS.get(k, k)}{v}件" for k, v in sorted(origins.items(), key=lambda kv: -kv[1]))
        lines.append(f"• ファイルボックスの未添付の証憑：新規{len(new_unattached)}件（残{len(unattached)}件：{origin_desc}）")
        for r in new_unattached[:max_lines]:
            deals = candidate_by_receipt.get(r.get("id"))
            if deals:
                ids = "、".join(f"取引ID {d.get('id')}（{d.get('issue_date')}）" for d in deals[:3])
                lines.append(f"    - {_receipt_label(r)} → 添付候補：{ids}")
            else:
                lines.append(f"    - {_receipt_label(r)}")
        if len(new_unattached) > max_lines:
            lines.append(f"    - …他{len(new_unattached) - max_lines}件")

    if sync_issues:
        names = "、".join(f"{w.get('name')}（{w.get('reason') or w.get('sync_status')}）" for w in sync_issues)
        lines.append(f"• 口座連携の異常（要確認）：{names}")
        lines.append("    - freee画面「口座」の最終同期日時を確認し、必要なら口座ごとの「同期」を押してください（連携切れなら再認証）")

    new_state = {
        "reported_txn_ids": sorted(reported_txn | {t.get("id") for t in stale if t.get("id") is not None}),
        "reported_receipt_ids": sorted(reported_receipt | {r.get("id") for r in unattached if r.get("id") is not None}),
        "known_patterns": sorted(known | {p["pattern"] for p in new_patterns}),
    }
    if not lines:
        return {"text": None, "state": new_state}
    text = f"*{company_name}*\n" + "\n".join(lines)
    return {"text": text, "state": new_state}


def default_window(today=None, days=90):
    """取得期間（開始日, 終了日）を "YYYY-MM-DD" で返す。"""
    today = today or date.today()
    return (today - timedelta(days=days)).isoformat(), today.isoformat()
