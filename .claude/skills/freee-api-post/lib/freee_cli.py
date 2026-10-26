"""freee-api-post CLIエントリポイント。
Bashラッパースクリプトから呼ばれる薄いグルー層。ここに書くのは
「companies.jsonの引き当て」「ファイルパスの解決」「ログ書き込み」など
CLI固有のつなぎ役だけで、freee APIそのものとのやりとりはfreee_api.pyに任せる。"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import freee_api

# Windowsのコンソールコードページによっては日本語出力が文字化けするため、
# 明示的にUTF-8へ切り替える（.claude/hooks/guard.pyと同じ対処）。
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


class ConfigError(Exception):
    """companies.json関連の設定エラー。"""


def load_company_config(companies_json_path: str, company_key: str) -> dict:
    with open(companies_json_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    for company in config["companies"]:
        if company["name"] == company_key or str(company["company_id"]) == company_key:
            return company
    raise ConfigError(f"companies.jsonに見つからない会社キー: {company_key}")


def resolve_path(repo_root: str, relative_path: str) -> str:
    return os.path.join(repo_root, relative_path)


def append_log(repo_root: str, instance: str, message: str) -> None:
    log_dir = os.path.join(repo_root, instance, "work")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "freee-api-post.log")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


def cmd_authorize_start(args: argparse.Namespace) -> int:
    company = load_company_config(args.companies_json, args.company_key)
    credentials_path = resolve_path(args.repo_root, company["credentials_path"])
    creds = freee_api.load_credentials(credentials_path)
    url = freee_api.build_authorize_url(creds["client_id"])
    print(f"以下のURLをブラウザで開き、事業所選択で必ず「{company['name']}」を選んでください。")
    print(url)
    print("表示された認可コードを freee-authorize-finish.sh に渡してください。")
    return 0


def cmd_authorize_finish(args: argparse.Namespace) -> int:
    company = load_company_config(args.companies_json, args.company_key)
    credentials_path = resolve_path(args.repo_root, company["credentials_path"])
    creds = freee_api.load_credentials(credentials_path)

    token_response = freee_api.exchange_code_for_token(
        creds["client_id"], creds["client_secret"], args.code
    )
    creds = freee_api.merge_token_response(creds, token_response)
    freee_api.save_credentials_atomic(credentials_path, creds)

    result = freee_api.api_call(
        creds["access_token"], company["company_id"], "GET", "/api/1/companies"
    )
    freee_api.verify_single_company(result["companies"], company["company_id"],
                                            company.get("ignored_company_ids"))

    append_log(args.repo_root, company["instance"],
               f"authorize-finish 成功: {company['name']}（company_id: {company['company_id']}）")
    print(f"認可成功：{company['name']}（company_id: {company['company_id']}）のみで疎通確認できました。")
    return 0


def cmd_healthcheck(args: argparse.Namespace) -> int:
    with open(args.companies_json, "r", encoding="utf-8") as f:
        config = json.load(f)
    failures = []
    for company in config["companies"]:
        credentials_path = resolve_path(args.repo_root, company["credentials_path"])
        try:
            token = freee_api.ensure_access_token(credentials_path)
            result = freee_api.api_call(token, company["company_id"], "GET", "/api/1/companies")
            # トークンが有効なだけでなく、そのトークンで見える事業所が対象1社だけであることまで
            # 毎回確かめる（authorize-finishと同じ検証）。想定外の事業所が混ざっていれば
            # FreeeApiErrorが送出され、下のexceptでNGとして記録される。
            freee_api.verify_single_company(result["companies"], company["company_id"],
                                            company.get("ignored_company_ids"))
            append_log(args.repo_root, company["instance"],
                       f"healthcheck OK: {company['name']}（company_id: {company['company_id']}）")
            print(f"OK: {company['name']}")
        except Exception as e:
            append_log(args.repo_root, company["instance"],
                       f"healthcheck NG: {company['name']}（company_id: {company['company_id']}）: {e}")
            print(f"NG: {company['name']}: {e}", file=sys.stderr)
            failures.append(f"{company['name']}（company_id: {company['company_id']}）: {e}")
    if failures:
        print("\n".join(failures))
        return 1
    return 0


def _fetch_paginated(token: int, company_id: int, path_with_query: str, key: str, limit: int = 100) -> list:
    """offset/limit ページングで全件取得する（読み取り専用）。"""
    items = []
    offset = 0
    sep = "&" if "?" in path_with_query else "?"
    while True:
        result = freee_api.api_call(
            token, company_id, "GET", f"{path_with_query}{sep}offset={offset}&limit={limit}")
        batch = result.get(key, []) or []
        items.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return items


def monitor_options(company: dict) -> dict:
    """companies.json の会社エントリから監視の有効/無効を読む（任意項目 monitor）。
    {"enabled": 会社全体を監視するか, "receipts": ファイルボックスの未添付証憑を見るか}。
    項目が無ければ全て有効。"""
    mon = company.get("monitor") or {}
    return {"enabled": mon.get("enabled", True) is not False,
            "receipts": mon.get("receipts", True) is not False}


def _state_path(repo_root: str, instance: str) -> str:
    return os.path.join(repo_root, instance, "work", "freee-monitor-state.json")


def _load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {"reported_txn_ids": [], "reported_receipt_ids": [], "known_patterns": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    state = dict(state)
    state["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def cmd_monitor(args: argparse.Namespace) -> int:
    """フェーズ3：未処理明細・未添付証憑・口座連携の監視。登録は一切しない（読み取り専用）。
    標準出力に、報告すべき会社ごとの本文を JSON 行（{"company", "notify_webhook_env", "text"}）で
    出す。報告するものが無い会社は出力しない。--dry-run では状態ファイルを更新しない。"""
    import freee_monitor

    with open(args.companies_json, "r", encoding="utf-8") as f:
        config = json.load(f)
    start_date, end_date = freee_monitor.default_window(days=args.days)
    failures = []
    for company in config["companies"]:
        name = company["name"]
        company_id = company["company_id"]
        options = monitor_options(company)
        if not options["enabled"]:
            print(f"monitor: {name} → 監視対象外（companies.json の monitor.enabled=false）", file=sys.stderr)
            continue
        try:
            credentials_path = resolve_path(args.repo_root, company["credentials_path"])
            token = freee_api.ensure_access_token(credentials_path)
            txns = _fetch_paginated(token, company_id,
                                    f"/api/1/wallet_txns?start_date={start_date}&end_date={end_date}",
                                    "wallet_txns")
            deals = _fetch_paginated(token, company_id,
                                     f"/api/1/deals?start_issue_date={start_date}&end_issue_date={end_date}",
                                     "deals")
            receipts = []
            if options["receipts"]:
                receipts = _fetch_paginated(token, company_id,
                                            f"/api/1/receipts?start_date={start_date}&end_date={end_date}",
                                            "receipts")
            walletables = (freee_api.api_call(token, company_id, "GET", "/api/1/walletables")
                           .get("walletables") or [])

            state_path = _state_path(args.repo_root, company["instance"])
            state = _load_state(state_path)
            untreated = [t for t in txns if t.get("status") == freee_monitor.UNTREATED_STATUS]
            stale = freee_monitor.find_stale_untreated(untreated, min_days=args.min_days)
            new_patterns = freee_monitor.find_new_patterns(untreated, set(state.get("known_patterns") or []))
            unattached = freee_monitor.find_unattached_receipts(receipts, deals)
            candidates = freee_monitor.match_receipt_candidates(unattached, deals)
            sync_issues = (freee_monitor.check_walletables_sync(walletables)
                           + freee_monitor.find_stalled_walletables(walletables, txns, max_days=args.stall_days))
            result = freee_monitor.build_company_report(
                name, stale, new_patterns, unattached, candidates, sync_issues, state)

            summary = (f"monitor: {name} 未処理{len(untreated)}件（滞留{len(stale)}件）"
                       f" 新パターン{len(new_patterns)}件"
                       + (f" 未添付証憑{len(unattached)}件（候補あり{len(candidates)}件）"
                          if options["receipts"] else " 未添付証憑=対象外")
                       + f" 連携異常{len(sync_issues)}件"
                       f" → {'報告あり' if result['text'] else '報告なし'}")
            if not args.dry_run:
                _save_state(state_path, result["state"])
                append_log(args.repo_root, company["instance"], summary)
            print(summary, file=sys.stderr)
            if result["text"]:
                print(json.dumps({"company": name,
                                  "notify_webhook_env": company.get("notify_webhook_env"),
                                  "text": result["text"]}, ensure_ascii=False))
        except Exception as e:
            if not args.dry_run:
                append_log(args.repo_root, company["instance"], f"monitor NG: {name}（company_id: {company_id}）: {e}")
            print(f"NG: {name}: {e}", file=sys.stderr)
            failures.append(f"{name}（company_id: {company_id}）: {e}")
    if failures:
        print("FAILURES: " + " / ".join(failures), file=sys.stderr)
        return 1
    return 0


def cmd_interest(args: argparse.Namespace) -> int:
    """預金利息の源泉振替（借方 法人税系／貸方 受取利息）を起票する。

    純額入金型の口座の利息取引を探し、まだ源泉分が計上されていない
    ものだけ振替伝票を1本ずつ登録する。総額入金型（同日・同口座に「国税」等の出金明細が
    ある口座）と、既に総額で計上済みの取引は対象外。--dry-run では登録しない。

    標準出力に、報告すべき会社ごとの本文を JSON 行（{"company", "notify_webhook_env", "text"}）で
    出す（freee-monitor と同じ形。報告するものが無い会社は出力しない）。"""
    import interest_withholding as iw

    with open(args.companies_json, "r", encoding="utf-8") as f:
        config = json.load(f)
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=args.days)
    failures = []
    for company in config["companies"]:
        name = company["name"]
        company_id = company["company_id"]
        # companies.json で auto_post: false の会社には、こちらから仕訳を書き込まない
        # （会計を他の担当者が見ている会社。監視・読み取りは対象のまま）
        if company.get("auto_post") is False:
            print(f"interest: {name} 対象外（auto_post: false）", file=sys.stderr)
            continue
        try:
            credentials_path = resolve_path(args.repo_root, company["credentials_path"])
            token = freee_api.ensure_access_token(credentials_path)
            accounts = (freee_api.api_call(token, company_id, "GET", "/api/1/account_items")
                        .get("account_items") or [])
            interest_id, _ = iw.find_account_id(accounts, (iw.INTEREST_ACCOUNT_NAME,))
            tax_id, tax_name = iw.find_account_id(accounts, iw.TAX_ACCOUNT_NAMES)
            if not interest_id or not tax_id:
                raise RuntimeError(f"科目が見つかりません（受取利息={interest_id} 法人税系={tax_id}）")
            tax_codes = (freee_api.api_call(token, company_id, "GET", "/api/1/taxes/codes")
                         .get("taxes") or [])
            exempt_code = iw.find_tax_code(tax_codes, iw.EXEMPT_TAX_NAME)
            if exempt_code is None:
                raise RuntimeError(f"税区分「{iw.EXEMPT_TAX_NAME}」が見つかりません")

            # account_item_id で受取利息の取引だけに絞る（取引数の多い会社で必須の高速化）
            deals = _fetch_paginated(token, company_id,
                                     f"/api/1/deals?account_item_id={interest_id}"
                                     f"&start_issue_date={start_date}&end_issue_date={end_date}"
                                     "&accruals=with", "deals")
            journals = _fetch_paginated(token, company_id,
                                        f"/api/1/manual_journals?start_issue_date={start_date}"
                                        f"&end_issue_date={end_date}", "manual_journals")
            txns = _fetch_paginated(token, company_id,
                                    f"/api/1/wallet_txns?start_date={start_date}&end_date={end_date}",
                                    "wallet_txns")

            item_id = iw.infer_item_id_from_history(deals, {interest_id}, {tax_id})
            if item_id is None:
                items = (freee_api.api_call(token, company_id, "GET", "/api/1/items").get("items") or [])
                item_id, _ = iw.find_item_id(items, iw.WITHHOLDING_ITEM_NAMES)
            result = iw.find_targets(deals, journals, txns, {interest_id}, {tax_id})

            registered = []
            for target in result["targets"]:
                payload = iw.build_journal_payload(company_id, target, interest_id, tax_id,
                                                   exempt_code, item_id)
                if args.dry_run:
                    registered.append({**target, "journal_id": "(dry-run)"})
                    continue
                created = freee_api.api_call(token, company_id, "POST", "/api/1/manual_journals", payload)
                journal_id = (created.get("manual_journal") or {}).get("id")
                registered.append({**target, "journal_id": journal_id})
                append_log(args.repo_root, company["instance"],
                           f"interest 起票: {name} {target['issue_date']} 入金{target['net']}円 → "
                           f"総額{target['gross']}円／源泉{target['withholding']}円"
                           f"（伝票id {journal_id}、対象取引 {target['deal_id']}、科目 {tax_name}）")
            attention = [s for s in result["excluded"] if s["reason"] in iw.ATTENTION_REASONS]

            summary = (f"interest: {name} 対象{len(result['targets'])}件"
                       f"（起票{len([r for r in registered if not args.dry_run])}件）"
                       f" 対象外{len(result['excluded'])}件 要確認{len(attention)}件")
            print(summary, file=sys.stderr)
            if not args.dry_run and not registered and not attention:
                append_log(args.repo_root, company["instance"], summary)
            text = iw.build_report(name, registered, attention)
            if text:
                print(json.dumps({"company": name,
                                  "notify_webhook_env": company.get("notify_webhook_env"),
                                  "text": text}, ensure_ascii=False))
        except Exception as e:
            if not args.dry_run:
                append_log(args.repo_root, company["instance"],
                           f"interest NG: {name}（company_id: {company_id}）: {e}")
            print(f"NG: {name}: {e}", file=sys.stderr)
            failures.append(f"{name}（company_id: {company_id}）: {e}")
    if failures:
        print("FAILURES: " + " / ".join(failures), file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="freee-api-post CLI")
    parser.add_argument("--companies-json", required=True)
    parser.add_argument("--repo-root", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("authorize-start")
    p_start.add_argument("company_key")
    p_start.set_defaults(func=cmd_authorize_start)

    p_finish = sub.add_parser("authorize-finish")
    p_finish.add_argument("company_key")
    p_finish.add_argument("code")
    p_finish.set_defaults(func=cmd_authorize_finish)

    p_health = sub.add_parser("healthcheck")
    p_health.set_defaults(func=cmd_healthcheck)

    p_monitor = sub.add_parser("monitor")
    p_monitor.add_argument("--dry-run", action="store_true", help="状態ファイル・ログを更新しない")
    p_monitor.add_argument("--days", type=int, default=90, help="取得期間（日数、既定90）")
    p_monitor.add_argument("--min-days", type=int, default=3, help="未処理明細を滞留とみなす日数（既定3）")
    p_monitor.add_argument("--stall-days", type=int, default=30,
                           help="API連携口座の最新明細がこの日数より古ければ連携停止の疑いとして報告（既定30）")
    p_monitor.set_defaults(func=cmd_monitor)

    p_interest = sub.add_parser("interest")
    p_interest.add_argument("--dry-run", action="store_true", help="登録・ログ書き込みをしない")
    p_interest.add_argument("--days", type=int, default=90, help="取得期間（日数、既定90）")
    p_interest.set_defaults(func=cmd_interest)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
