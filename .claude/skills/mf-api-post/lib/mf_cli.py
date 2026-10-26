"""mf-api-post CLIエントリポイント（読み取り専用）。
Bashラッパースクリプトから呼ばれる薄いグルー層。ここに書くのは
「companies.jsonの引き当て」「ファイルパスの解決」「ログ書き込み」など
CLI固有のつなぎ役だけで、マネーフォワードAPIそのものとのやりとりはmf_api.pyに任せる。"""
import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

import mf_api
import mf_paging

# MFの秒間制限があるため、GETの呼び出し間隔を1.5秒空ける。
_API_CALL_INTERVAL_SECONDS = 1.5

# Windowsのコンソールコードページによっては日本語出力が文字化けするため、
# 明示的にUTF-8へ切り替える。
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


class ConfigError(Exception):
    """companies.json関連の設定エラー。"""


def load_company_config(companies_json_path: str, company_key: str) -> dict:
    with open(companies_json_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    for company in config["companies"]:
        if company["name"] == company_key or str(company["office_id"]) == company_key:
            return company
    raise ConfigError(f"companies.jsonに見つからない会社キー: {company_key}")


def resolve_path(repo_root: str, relative_path: str) -> str:
    return os.path.join(repo_root, relative_path)


def append_log(repo_root: str, instance: str, message: str) -> None:
    log_dir = os.path.join(repo_root, instance, "work")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "mf-api-post.log")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


def cmd_authorize_start(args: argparse.Namespace) -> int:
    company = load_company_config(args.companies_json, args.company_key)
    credentials_path = resolve_path(args.repo_root, company["credentials_path"])
    creds = mf_api.load_credentials(credentials_path)
    url = mf_api.build_authorize_url(creds["client_id"])
    print(f"以下のURLをブラウザで開き、事業所選択で必ず「{company['name']}」を選んでください。")
    print(url)
    print("許可後、ブラウザは http://localhost:8765/callback?code=... へリダイレクトされ")
    print("「サイトにアクセスできません」的な表示になりますが、これは想定通りです。")
    print("アドレスバーのURLから code パラメータの値をコピーし、")
    print("mf-authorize-finish.sh に渡してください。")
    return 0


def cmd_authorize_finish(args: argparse.Namespace) -> int:
    company = load_company_config(args.companies_json, args.company_key)
    credentials_path = resolve_path(args.repo_root, company["credentials_path"])
    creds = mf_api.load_credentials(credentials_path)

    token_response = mf_api.exchange_code_for_token(
        creds["client_id"], creds["client_secret"], args.code
    )
    creds = mf_api.merge_token_response(creds, token_response)
    mf_api.save_credentials_atomic(credentials_path, creds)

    result = mf_api.api_call(creds["access_token"], "GET", "/offices")
    mf_api.verify_single_office(result, company["office_id"])

    append_log(args.repo_root, company["instance"],
               f"authorize-finish 成功: {company['name']}（office_id: {company['office_id']}）")
    print(f"認可成功：{company['name']}（office_id: {company['office_id']}）のみで疎通確認できました。")
    return 0


def cmd_healthcheck(args: argparse.Namespace) -> int:
    with open(args.companies_json, "r", encoding="utf-8") as f:
        config = json.load(f)
    failures = []
    for company in config["companies"]:
        credentials_path = resolve_path(args.repo_root, company["credentials_path"])
        try:
            token = mf_api.ensure_access_token(credentials_path)
            office = mf_api.api_call(token, "GET", "/offices")
            mf_api.verify_single_office(office, company["office_id"])
            append_log(args.repo_root, company["instance"],
                       f"healthcheck OK: {company['name']}（office_id: {company['office_id']}）")
            print(f"OK: {company['name']}")
        except Exception as e:
            append_log(args.repo_root, company["instance"],
                       f"healthcheck NG: {company['name']}（office_id: {company['office_id']}）: {e}")
            print(f"NG: {company['name']}: {e}", file=sys.stderr)
            failures.append(f"{company['name']}（office_id: {company['office_id']}）: {e}")
    if failures:
        print("\n".join(failures))
        return 1
    return 0


def fetch_all(get, path: str, key: str, *, per_page: int = 500,
               sleep=time.sleep) -> list:
    """ページを最後まで取る。

    実体は `mf_paging.fetch_all`（`mf_register._find_journal` の日付範囲フォールバックとも
    共有する共通ロジック）へ委譲する。このラッパーは、CLI側からの既存の呼び出し（引数の並び・
    既定値）をそのまま保つためだけに残してある。

    呼び出し元がさらに外側で複数のAPI呼び出しを連続させる場合は、呼び出し元の責任で
    同様の間隔を空けること（`cmd_build_patterns` 参照）。
    """
    return mf_paging.fetch_all(get, path, key, per_page=per_page, sleep=sleep,
                               interval_seconds=_API_CALL_INTERVAL_SECONDS)


def _settle_since(tx_date: str, periods) -> str:
    """未消込を探す開始日：基準日の SETTLE_LOOKBACK_DAYS 日前。ただしMFは会計期間をまたぐ指定を
    400で弾くので、基準日を含む会計期間の期首より前には戻らない。"""
    import mf_settle
    d = date.fromisoformat(tx_date)
    since = (d - timedelta(days=mf_settle.SETTLE_LOOKBACK_DAYS)).isoformat()
    for p in periods:
        if p.get("start_date") and p.get("end_date") and p["start_date"] <= tx_date <= p["end_date"]:
            return max(since, p["start_date"])
    return since


def cmd_open_items(args: argparse.Namespace, *, sleep=None) -> int:
    """科目の未消込の計上を補助科目ごとに並べる（読み取り専用）。
    入金・出金の中身を調べるとき（同額の売掛金・未払金があるか）に使う。"""
    import mf_settle
    company = load_company_config(args.companies_json, args.company_key)
    token = mf_api.ensure_access_token(resolve_path(args.repo_root, company["credentials_path"]))

    def get(path):
        return mf_api.api_call(token, "GET", path)

    office = get("/offices")
    mf_api.verify_single_office(office, company["office_id"])
    accounts = (get("/accounts") or {}).get("accounts") or []
    acct = [a for a in accounts if a.get("name") == args.account]
    if len(acct) != 1:
        print(f"科目「{args.account}」が1つに決まりません（{len(acct)}件）")
        return 1
    acct = acct[0]
    since = _settle_since(args.date, office.get("accounting_periods") or [])
    journals = fetch_all(get, f"/journals?account_id={acct['id']}&start_date={since}&end_date={args.date}",
                         "journals")
    print(f"{company['name']} {args.account}：{since}〜{args.date} の未消込")
    for sub in [{"id": None, "name": "（補助科目なし）"}] + (acct.get("sub_accounts") or []):
        items = mf_settle.open_items(journals, acct["id"], sub["id"], args.account, since=since)
        for i in items:
            print(f"  {sub['name']}\t#{i['number']}\t{i['date']}\t{i['value']:,}")
    return 0


def cmd_find_journals(args: argparse.Namespace, *, sleep=None) -> int:
    """仕訳を金額・摘要の文字で探す（読み取り専用）。調査用。
    期間は会計期間をまたげない（MFの仕様）。"""
    company = load_company_config(args.companies_json, args.company_key)
    token = mf_api.ensure_access_token(resolve_path(args.repo_root, company["credentials_path"]))

    def get(path):
        return mf_api.api_call(token, "GET", path)

    mf_api.verify_single_office(get("/offices"), company["office_id"])
    journals = fetch_all(get, f"/journals?start_date={args.from_date}&end_date={args.to_date}", "journals")
    hits = 0
    for j in journals:
        for b in j.get("branches") or []:
            legs = [b.get("debitor") or {}, b.get("creditor") or {}]
            amounts = {int(l.get("value") or 0) + int(l.get("tax_value") or 0) for l in legs}
            text = str(b.get("remark") or "") + str(j.get("memo") or "")
            if args.amount and int(args.amount) not in amounts:
                continue
            if args.text and args.text not in text:
                continue
            if getattr(args, "account", None) and args.account not in (
                    str(legs[0].get("account_name")), str(legs[1].get("account_name"))):
                continue
            hits += 1
            d, c = legs
            print(f"#{j.get('number')}\t{j.get('transaction_date')}\t借:{d.get('account_name')}（{d.get('sub_account_name') or '-'}）"
                  f"\t貸:{c.get('account_name')}（{c.get('sub_account_name') or '-'}）"
                  f"\t{int(d.get('value') or 0) + int(d.get('tax_value') or 0):,}\t{b.get('remark') or ''}"
                  f"\t{j.get('entered_by') or ''}")
    print(f"該当 {hits}行")
    return 0



def cmd_show_journal(args: argparse.Namespace, *, sleep=None) -> int:
    """仕訳番号で1件を読み、科目・補助科目・税区分を名前にして表示する（読み取り専用）。

    照合のために、ライブラリを直接呼ぶその場限りのスクリプトを書かずに済むようにする。GETしか呼ばない。
    金額は「本体（value）・税額（tax_value）・税込（合計）」を並べる（MFのAPIは課税行を税抜で返すため）。
    """
    sleep = sleep or time.sleep
    company = load_company_config(args.companies_json, args.company_key)
    credentials_path = resolve_path(args.repo_root, company["credentials_path"])
    token = mf_api.ensure_access_token(credentials_path)

    def get(path):
        return mf_api.api_call(token, "GET", path)

    mf_api.verify_single_office(get("/offices"), company["office_id"])
    sleep(_API_CALL_INTERVAL_SECONDS)
    accounts = (get("/accounts") or {}).get("accounts") or []
    sleep(_API_CALL_INTERVAL_SECONDS)
    taxes = (get("/taxes") or {}).get("taxes") or []
    sleep(_API_CALL_INTERVAL_SECONDS)
    journals = fetch_all(get, f"/journals?start_date={args.date}&end_date={args.date}",
                         "journals", sleep=sleep)

    target = str(args.number).lstrip("#")
    found = [j for j in journals if str(j.get("number")) == target]
    if not found:
        print(f"{company['name']}：{args.date} に仕訳#{target} が見つかりません"
              f"（その日の仕訳は{len(journals)}件）")
        return 1

    acc_names = {a.get("id"): a.get("name") for a in accounts}
    sub_names = {s.get("id"): s.get("name") for a in accounts for s in (a.get("sub_accounts") or [])}
    tax_names = {t.get("id"): t.get("name") for t in taxes}

    for j in found:
        print(f"{company['name']} 仕訳#{j.get('number')} {j.get('transaction_date')} "
              f"種別:{j.get('journal_type')} 明細ID:{j.get('transaction_id') or '-'}")
        print("| 行 | 貸借 | 科目 | 補助科目 | 税区分 | 本体 | 税額 | 税込 | 摘要 |")
        print("|---|---|---|---|---|---|---|---|---|")
        for i, b in enumerate(j.get("branches") or [], 1):
            for side, label in (("debitor", "借方"), ("creditor", "貸方")):
                x = b.get(side) or {}
                if not x.get("account_id"):
                    continue
                value = int(x.get("value") or 0)
                tax_value = int(x.get("tax_value") or 0)
                print(f"| {i} | {label} | {acc_names.get(x.get('account_id'), x.get('account_id'))} | "
                      f"{sub_names.get(x.get('sub_account_id'), '-') if x.get('sub_account_id') else '-'} | "
                      f"{tax_names.get(x.get('tax_id'), x.get('tax_id') or '-')} | "
                      f"{value:,} | {tax_value:,} | {value + tax_value:,} | {b.get('remark') or ''} |")
    return 0


def cmd_read(args: argparse.Namespace, *, sleep=None) -> int:
    """MF会計APIを読むだけ（GET）。MCPの get 系ツール（getJournals・getAccounts等）の代わり
    （読み取り専用）。会社の切り替え（マネフォMCPの事業者切替）が要らない。

    許可リスト（mf_read._ALLOWED）外のパスは読まない。`--all-pages` を付けると一覧を最後まで
    取ってファイルに保存する（画面には件数と保存先だけを出す。大きな取得は
    ファイルに落として Python で解析する）。
    """
    import mf_read
    sleep = sleep or time.sleep
    company = load_company_config(args.companies_json, args.company_key)
    token = mf_api.ensure_access_token(resolve_path(args.repo_root, company["credentials_path"]))

    def get(path):
        return mf_api.api_call(token, "GET", path)

    try:
        mf_read.check_path(args.path)
    except ValueError as e:
        print(str(e))
        return 1
    mf_api.verify_single_office(get("/offices"), company["office_id"])
    sleep(_API_CALL_INTERVAL_SECONDS)
    data = mf_read.read(get, args.path, args.all_pages, sleep)
    out = args.out or mf_read.default_out(args.repo_root, company)
    out_dir = os.path.dirname(out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    key = mf_read.list_key(args.path)
    n = len(data.get(key) or []) if key else 1
    print(f"{company['name']}（{company['office_id']}）：{n}件 → {out}")
    total_pages = int((data.get("metadata") or {}).get("total_pages") or 0) if isinstance(data, dict) else 0
    if not args.all_pages and total_pages > 1:
        print(f"注意：1ページ目だけです（全{total_pages}ページ）。全件は --all-pages を付けて取り直してください")
    append_log(args.repo_root, company["instance"], f"read: {company['name']} {args.path} → {n}件 {out}")
    return 0




def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="mf-api-post CLI（読み取り専用）", allow_abbrev=False)
    parser.add_argument("--companies-json", required=True)
    parser.add_argument("--repo-root", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("authorize-start", allow_abbrev=False)
    p_start.add_argument("company_key")
    p_start.set_defaults(func=cmd_authorize_start)

    p_finish = sub.add_parser("authorize-finish", allow_abbrev=False)
    p_finish.add_argument("company_key")
    p_finish.add_argument("code")
    p_finish.set_defaults(func=cmd_authorize_finish)

    p_health = sub.add_parser("healthcheck", allow_abbrev=False)
    p_health.set_defaults(func=cmd_healthcheck)

    p_open = sub.add_parser("open-items", allow_abbrev=False)
    p_open.add_argument("company_key")
    p_open.add_argument("--account", required=True)
    p_open.add_argument("--date", required=True)
    p_open.set_defaults(func=cmd_open_items)

    p_find = sub.add_parser("find-journals", allow_abbrev=False)
    p_find.add_argument("company_key")
    p_find.add_argument("--from", dest="from_date", required=True)
    p_find.add_argument("--to", dest="to_date", required=True)
    p_find.add_argument("--amount", default=None)
    p_find.add_argument("--text", default=None)
    p_find.add_argument("--account", default=None)
    p_find.set_defaults(func=cmd_find_journals)

    p_show = sub.add_parser("show-journal", allow_abbrev=False)
    p_show.add_argument("company_key")
    p_show.add_argument("number")
    p_show.add_argument("--date", required=True)
    p_show.set_defaults(func=cmd_show_journal)

    p_read = sub.add_parser("read", allow_abbrev=False)
    p_read.add_argument("company_key")
    p_read.add_argument("path")
    p_read.add_argument("--all-pages", dest="all_pages", action="store_true")
    p_read.add_argument("--out", default=None)
    p_read.set_defaults(func=cmd_read)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
