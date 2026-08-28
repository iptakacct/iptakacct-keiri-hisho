"""invox-api CLIエントリポイント。
Bashラッパースクリプト、およびmf-fee-post/SKILL.mdのinvox承認ステップから呼ばれる薄いグルー層。
ここに書くのは「accounts.jsonの引き当て」「ファイルパスの解決」「ログ書き込み」など
CLI固有のつなぎ役だけで、invox APIそのものとのやりとりはinvox_api.pyに任せる。
承認基準・全項目チェックの判断ロジックはここには置かない（mf-fee-post/invox-samplegroup-approval.md参照）。"""
import argparse
import json
import os
import sys
from datetime import datetime

import invox_api

# Windowsのコンソールコードページによっては日本語出力が文字化けするため、
# 明示的にUTF-8へ切り替える。
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


class ConfigError(Exception):
    """accounts.json関連の設定エラー。"""


def load_account_config(accounts_json_path: str, account_key: str) -> dict:
    with open(accounts_json_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    for account in config["accounts"]:
        if account["name"] == account_key:
            return account
    raise ConfigError(f"accounts.jsonに見つからないアカウントキー: {account_key}")


def resolve_path(repo_root: str, relative_path: str) -> str:
    return os.path.join(repo_root, relative_path)


def append_log(repo_root: str, instance: str, message: str) -> None:
    log_dir = os.path.join(repo_root, instance, "work")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "invox-api.log")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


def cmd_authorize_start(args: argparse.Namespace) -> int:
    account = load_account_config(args.accounts_json, args.account_key)
    credentials_path = resolve_path(args.repo_root, account["credentials_path"])
    creds = invox_api.load_credentials(credentials_path)
    url = invox_api.build_authorize_url(creds["client_id"])
    print(f"以下のURLをブラウザで開き、許可してください（{account['name']}）。")
    print(url)
    print("許可すると https://web.invox.jp/?code=... （またはそれに類する形）へリダイレクトされます。")
    print("アドレスバーのURLから code パラメータの値をコピーし、")
    print("invox-authorize-finish.sh に渡してください。")
    return 0


def cmd_authorize_finish(args: argparse.Namespace) -> int:
    account = load_account_config(args.accounts_json, args.account_key)
    credentials_path = resolve_path(args.repo_root, account["credentials_path"])
    creds = invox_api.load_credentials(credentials_path)

    token_response = invox_api.exchange_code_for_token(
        creds["client_id"], creds["client_secret"], args.code
    )
    creds = invox_api.merge_token_response(creds, token_response)
    invox_api.save_credentials_atomic(credentials_path, creds)

    # 疎通確認：件数だけ見る軽い呼び出し（承認状態等は問わない）
    result = invox_api.api_call(
        creds["access_token"], "GET", "invoice_receive/invoice/list",
        params={"invox_company_code": account["invox_company_code"]},
    )
    append_log(args.repo_root, account["instance"],
               f"authorize-finish 成功: {account['name']}（疎通確認: {result.get('count', '?')}件）")
    print(f"認可成功：{account['name']}。疎通確認できました（該当請求書 {result.get('count', '?')}件）。")
    return 0


def cmd_healthcheck(args: argparse.Namespace) -> int:
    with open(args.accounts_json, "r", encoding="utf-8") as f:
        config = json.load(f)
    failures = []
    for account in config["accounts"]:
        credentials_path = resolve_path(args.repo_root, account["credentials_path"])
        try:
            token = invox_api.ensure_access_token(credentials_path)
            invox_api.api_call(
                token, "GET", "invoice_receive/invoice/list",
                params={"invox_company_code": account["invox_company_code"]},
            )
            append_log(args.repo_root, account["instance"], f"healthcheck OK: {account['name']}")
            print(f"OK: {account['name']}")
        except Exception as e:
            append_log(args.repo_root, account["instance"], f"healthcheck NG: {account['name']}: {e}")
            print(f"NG: {account['name']}: {e}", file=sys.stderr)
            failures.append(f"{account['name']}: {e}")
    if failures:
        print("\n".join(failures))
        return 1
    return 0


def cmd_list_invoices(args: argparse.Namespace) -> int:
    account = load_account_config(args.accounts_json, args.account_key)
    credentials_path = resolve_path(args.repo_root, account["credentials_path"])
    token = invox_api.ensure_access_token(credentials_path)
    params = {"invox_company_code": account["invox_company_code"]}
    if getattr(args, "department_code", None):
        params["department_code"] = args.department_code
    # ページ番号・件数は指定された時だけ渡す（未指定ならAPI側のデフォルト＝1ページ目に任せる）。
    # 全件を取るには呼び出し側（invox-samplegroup-approval.md §1）が
    # レスポンスのcountとlen(results)を比較し、足りなければpageを増やして再呼び出しする。
    if getattr(args, "page", None):
        params["page"] = args.page
    if getattr(args, "limit", None):
        params["limit"] = args.limit
    # 日付フィルタ（YYYY/MM/DD）。全件は5,000件超あるため、通常は取込日（create_date）で絞る。
    # fixed_only はAPI既定がtrue（確定済みのみ）。未申請・承認待ちも含めて見たい時は "false" を渡す。
    for name in ("create_date_from", "create_date_to", "payment_plan_date_from",
                 "payment_plan_date_to", "fixed_only"):
        value = getattr(args, name, None)
        if value:
            params[name] = value
    result = invox_api.api_call(
        token, "GET", "invoice_receive/invoice/list", params=params
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_get_invoice(args: argparse.Namespace) -> int:
    """請求書1件の詳細を取得する（一覧APIは invoice_id しか返さないため、内容確認はこちらを使う）。"""
    account = load_account_config(args.accounts_json, args.account_key)
    credentials_path = resolve_path(args.repo_root, account["credentials_path"])
    token = invox_api.ensure_access_token(credentials_path)
    params = {
        "invox_company_code": account["invox_company_code"],
        "invoice_id": args.invoice_id,
    }
    if getattr(args, "include_journal_info", False):
        params["include_journal_info"] = "true"
    result = invox_api.api_call(
        token, "GET", "invoice_receive/invoice/get", params=params
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_confirm_message(args: argparse.Namespace) -> int:
    """請求書の注意メッセージ（get-invoiceの messages[].message_id）を確認済みにする。
    未確認の注意が残っていると画面の一括承認ができないため、承認候補に対して使う。"""
    account = load_account_config(args.accounts_json, args.account_key)
    credentials_path = resolve_path(args.repo_root, account["credentials_path"])
    token = invox_api.ensure_access_token(credentials_path)
    result = invox_api.api_call(
        token, "POST", "invoice_receive/invoice/message/confirm",
        json_body={
            "invox_company_code": account["invox_company_code"],
            "invoice_id": args.invoice_id,
            "message_id": int(args.message_id),
        },
    )
    response_json = json.dumps(result, ensure_ascii=False)
    append_log(
        args.repo_root, account["instance"],
        f"message confirm OK: {args.invoice_id} message_id={args.message_id}（{account['name']}） response={response_json}",
    )
    print(f"注意を確認済みにしました：{args.invoice_id} message_id={args.message_id} response={response_json}")
    return 0


def cmd_approve_invoice(args: argparse.Namespace) -> int:
    account = load_account_config(args.accounts_json, args.account_key)
    credentials_path = resolve_path(args.repo_root, account["credentials_path"])
    token = invox_api.ensure_access_token(credentials_path)
    body = {
        "invox_company_code": account["invox_company_code"],
        "invoice_id": args.invoice_id,
        "approve_task_name": args.approve_task_name,
    }
    # appr_path_idは公式ドキュメント上は任意（型はnumber）。2026-08-28の実機確認では、承認済み分はnull、
    # 承認待ち（wait_approval）分には数値（例：8254）が入っていた。指定された時だけ送り、
    # 数字だけの値はAPI仕様どおり数値に変換し、それ以外は受け取った文字列のまま渡す（tracebackさせない）。
    appr_path_id = getattr(args, "appr_path_id", None)
    if appr_path_id:
        body["appr_path_id"] = int(appr_path_id) if str(appr_path_id).isdigit() else appr_path_id
    result = invox_api.api_call(
        token, "POST", "invoice_receive/invoice/approve", json_body=body,
    )
    # HTTP 2xxだけで成功と報告せず、応答本体（承認後の状態等）を記録・出力する。
    response_json = json.dumps(result, ensure_ascii=False)
    append_log(
        args.repo_root, account["instance"],
        f"approve OK: {args.invoice_id}（{account['name']}） response={response_json}",
    )
    print(f"承認成功: {args.invoice_id}（{account['name']}） response={response_json}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="invox-api CLI")
    parser.add_argument("--accounts-json", required=True)
    parser.add_argument("--repo-root", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("authorize-start")
    p_start.add_argument("account_key")
    p_start.set_defaults(func=cmd_authorize_start)

    p_finish = sub.add_parser("authorize-finish")
    p_finish.add_argument("account_key")
    p_finish.add_argument("code")
    p_finish.set_defaults(func=cmd_authorize_finish)

    p_health = sub.add_parser("healthcheck")
    p_health.set_defaults(func=cmd_healthcheck)

    p_list = sub.add_parser("list-invoices")
    p_list.add_argument("account_key")
    p_list.add_argument("--department-code", default=None)
    p_list.add_argument("--page", default=None)
    p_list.add_argument("--limit", default=None)
    p_list.add_argument("--create-date-from", default=None)
    p_list.add_argument("--create-date-to", default=None)
    p_list.add_argument("--payment-plan-date-from", default=None)
    p_list.add_argument("--payment-plan-date-to", default=None)
    p_list.add_argument("--fixed-only", default=None, help='"true"/"false"（API既定はtrue）')
    p_list.set_defaults(func=cmd_list_invoices)

    p_get = sub.add_parser("get-invoice")
    p_get.add_argument("account_key")
    p_get.add_argument("invoice_id")
    p_get.add_argument("--include-journal-info", action="store_true")
    p_get.set_defaults(func=cmd_get_invoice)

    p_confirm = sub.add_parser("confirm-message")
    p_confirm.add_argument("account_key")
    p_confirm.add_argument("invoice_id")
    p_confirm.add_argument("message_id")
    p_confirm.set_defaults(func=cmd_confirm_message)

    p_approve = sub.add_parser("approve-invoice")
    p_approve.add_argument("account_key")
    p_approve.add_argument("invoice_id")
    p_approve.add_argument("approve_task_name")
    # 2026-08-28の実機確認では appr_path_id は全件 null（公式ドキュメント上も省略可）のため任意
    p_approve.add_argument("appr_path_id", nargs="?", default=None)
    p_approve.set_defaults(func=cmd_approve_invoice)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as e:
        # accounts.jsonにアカウントキーが見つからない等、設定側の誤り。
        print(f"設定エラー: {e}", file=sys.stderr)
        return 1
    except invox_api.InvoxApiError as e:
        # invox APIとの通信・認証エラー（ネットワーク障害・トークン失効等）。
        print(f"invox APIエラー: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        # accounts.json・認証情報ファイルが見つからない。
        print(f"ファイルが見つかりません: {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        # accounts.json・認証情報ファイルの中身がJSONとして壊れている。
        print(f"設定ファイルをJSONとして解釈できませんでした: {e}", file=sys.stderr)
        return 1
    except KeyError as e:
        # 認証情報ファイルに必要なキーが無い（例：認可コード交換前で
        # client_id/client_secretしか保存されていないトークンファイル）。
        print(f"設定ファイルに必要な項目がありません: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
