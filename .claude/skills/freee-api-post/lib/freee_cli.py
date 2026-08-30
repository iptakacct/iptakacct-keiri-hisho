"""freee-api-post CLIエントリポイント。
Bashラッパースクリプトから呼ばれる薄いグルー層。ここに書くのは
「companies.jsonの引き当て」「ファイルパスの解決」「ログ書き込み」など
CLI固有のつなぎ役だけで、freee APIそのものとのやりとりはfreee_api.pyに任せる。"""
import argparse
import json
import os
import sys
from datetime import datetime

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

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
