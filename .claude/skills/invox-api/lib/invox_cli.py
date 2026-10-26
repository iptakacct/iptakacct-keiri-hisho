"""invox-api CLIエントリポイント。
Bashラッパースクリプト、およびmf-fee-post/SKILL.mdのinvox承認ステップから呼ばれる薄いグルー層。
ここに書くのは「accounts.jsonの引き当て」「ファイルパスの解決」「ログ書き込み」など
CLI固有のつなぎ役だけで、invox APIそのものとのやりとりはinvox_api.pyに任せる。
承認基準・全項目チェックの判断ロジックはここには置かない（各インスタンスの承認運用メモ参照）。"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime

import invox_api
import yayoi_journal

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
    # 全件を取るには呼び出し側（各インスタンスの承認運用メモ）が
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


def cmd_update_invoice(args: argparse.Namespace) -> int:
    """請求書の出力設定（仕訳出力・振込出力・費用計上仕訳）を更新する。
    invoxのPUT invoice/updateは部分更新で、`invoice`オブジェクトに渡した項目だけが変わる
    （公式ドキュメントで確認）。渡さなかった項目は元のまま残る。
    どの仕入先をどの設定にするかの判断は、各インスタンスの運用メモに従う。"""
    account = load_account_config(args.accounts_json, args.account_key)
    credentials_path = resolve_path(args.repo_root, account["credentials_path"])
    token = invox_api.ensure_access_token(credentials_path)
    invoice: dict = {}
    for flag in ("exclude_export_invoice", "exclude_export_payment",
                 "exclude_export_expense_journal"):
        value = getattr(args, flag, None)
        if value is not None:
            invoice[flag] = value == "true"
    # メモは上書きせず、既存のメモの末尾に1行足す（担当者の確認印を消さないため）
    memo_append = getattr(args, "memo_append", None)
    if memo_append:
        current = invox_api.api_call(
            token, "GET", "invoice_receive/invoice/get",
            params={"invox_company_code": account["invox_company_code"], "invoice_id": args.invoice_id},
        )
        current = current.get("invoice", current) if isinstance(current.get("invoice"), dict) else current
        old_memo = (current.get("memo") or "").rstrip()
        if memo_append in old_memo:
            print(f"同じ文言が既にメモにあるため追記しません：{args.invoice_id}")
            return 0
        invoice["memo"] = f"{old_memo}\n{memo_append}" if old_memo else memo_append
    if not invoice:
        print("更新する項目が指定されていません（--exclude-export-* か --memo-append を指定してください）",
              file=sys.stderr)
        return 1
    result = invox_api.api_call(
        token, "PUT", "invoice_receive/invoice/update",
        json_body={
            "invox_company_code": account["invox_company_code"],
            "invoice_id": args.invoice_id,
            "invoice": invoice,
        },
    )
    response_json = json.dumps(result, ensure_ascii=False)
    changes = json.dumps(invoice, ensure_ascii=False)
    append_log(
        args.repo_root, account["instance"],
        f"update OK: {args.invoice_id}（{account['name']}） {changes} response={response_json}",
    )
    print(f"出力設定を更新しました：{args.invoice_id} {changes} response={response_json}")
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


def _export_extension(content_type: str) -> str:
    ct = (content_type or "").lower()
    if "json" in ct:
        return ".json"
    if "csv" in ct:
        return ".csv"
    return ".txt"


def save_export_chunk(out_dir: str, base_name: str, content_type: str, body: bytes,
                      meta: dict) -> str:
    """journal/exportの応答1回分を、受け取ったバイト列のまま（デコード・改行変換なし）保存する。
    一時ファイルに書いてfsync→リネームするので、途中で落ちても半端なファイルが本体名で残らない。
    併せて <同名>.meta.json にHTTPステータス・Content-Type・送ったリクエスト等を残す。
    保存したデータファイルのパスを返す。失敗したら例外をそのまま上げる（呼び出し側が中断する）。"""
    os.makedirs(out_dir, exist_ok=True)
    data_path = os.path.join(out_dir, base_name + _export_extension(content_type))
    fd, tmp_path = tempfile.mkstemp(dir=out_dir, prefix=".export_", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, data_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    meta_path = os.path.join(out_dir, base_name + ".meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({**meta, "content_type": content_type, "bytes": len(body),
                   "data_file": os.path.basename(data_path)}, f, ensure_ascii=False, indent=1)
    return data_path


def cmd_export_journal(args: argparse.Namespace) -> int:
    """仕訳データを journal/export で取得し、応答ごとに無加工で保存する。
    invoxのjournal/exportは「未出力分だけを返し、返した分は出力済みになる」（再出力する手段が無い）。
    そのため 1回分の応答を保存し終えるまで次の呼び出しをせず、保存に失敗したらその場で止める
    （各インスタンスの承認運用メモ）。1回の応答は最大100請求書分で、
    204が返るまで繰り返す（公式ドキュメント）。試し打ちは禁止＝呼んだら必ず本番として扱う。"""
    account = load_account_config(args.accounts_json, args.account_key)
    credentials_path = resolve_path(args.repo_root, account["credentials_path"])
    token = invox_api.ensure_access_token(credentials_path)
    out_dir = args.out_dir or os.path.join(args.repo_root, account["instance"], "work",
                                           "invox-journal-export")
    body = {"invox_company_code": account["invox_company_code"],
            "journal_type": args.journal_type}
    for name in ("posting_date_from", "posting_date_to", "department_code"):
        value = getattr(args, name, None)
        if value:
            body[name] = value
    run_stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    saved = []
    for seq in range(1, int(getattr(args, "max_pages", 50) or 50) + 1):
        status, content_type, payload = invox_api.api_call_raw(
            token, "POST", "invoice_receive/invoice/journal/export", json_body=body)
        if status == 204:
            break
        base_name = f"{run_stamp}-{args.journal_type}-{seq:02d}"
        try:
            data_path = save_export_chunk(
                out_dir, base_name, content_type, payload,
                {"http_status": status, "request": body, "account": account["name"],
                 "saved_at": datetime.now().isoformat(timespec="seconds"), "seq": seq})
        except Exception as e:
            # ここで止める：invox側は既に「出力済み」にしているので、続けると取り出したデータを失う。
            append_log(args.repo_root, account["instance"],
                       f"journal export SAVE FAILED: {account['name']} seq={seq} "
                       f"status={status} bytes={len(payload)} error={e}")
            print(f"journal/export の応答の保存に失敗しました（seq={seq}、{len(payload)}バイト）。"
                  f"invox側は出力済み扱いになっているため処理を中断しました。すぐに報告してください: {e}",
                  file=sys.stderr)
            return 1
        append_log(args.repo_root, account["instance"],
                   f"journal export saved: {account['name']} {args.journal_type} seq={seq} "
                   f"status={status} bytes={len(payload)} file={os.path.basename(data_path)}")
        saved.append({"file": data_path, "bytes": len(payload), "http_status": status})
    else:
        append_log(args.repo_root, account["instance"],
                   f"journal export stopped at max_pages: {account['name']} {args.journal_type} "
                   f"chunks={len(saved)}（204が返る前に上限に達した。未出力分が残っている可能性あり）")
        print(json.dumps({"account": account["name"], "journal_type": args.journal_type,
                          "request": body, "chunks": len(saved), "files": saved,
                          "incomplete": True}, ensure_ascii=False))
        print("204が返る前に --max-pages の上限に達しました。未出力の仕訳が残っている可能性があるため、"
              "上限を上げて続きを取得してください（保存済みの分はそのまま使えます）", file=sys.stderr)
        return 2
    if not saved:
        append_log(args.repo_root, account["instance"],
                   f"journal export: {account['name']} {args.journal_type} 出力対象なし（204）")
    print(json.dumps({"account": account["name"], "journal_type": args.journal_type,
                      "request": body, "chunks": len(saved), "files": saved}, ensure_ascii=False))
    return 0


def cmd_prepare_journals(args: argparse.Namespace) -> int:
    """export-journalで保存した弥生形式データを、会社別のprepared-<会社名>.jsonへ変換する。

    弥生データだけでは会社が分からない（仕訳辞書が当たると借方部門列が空になる）ため、
    請求書IDごとに invoice/get を1回引いて department_name を取り、部門対応表で振り分ける。
    貸借不一致・部門不明が1件でもあれば終了コード1を返す（判明した会社の分は出力する）。
    ここではMFへ登録しない——科目名からMFのIDへの引き当てと登録は、自前のMF会計API
    （mf-api-post の read・post-journal）で会社名を指定してClaudeが行う（各インスタンスの承認運用メモ）。
    """
    account = load_account_config(args.accounts_json, args.account_key)
    credentials_path = resolve_path(args.repo_root, account["credentials_path"])
    token = invox_api.ensure_access_token(credentials_path)
    out_dir = args.out_dir or os.path.join(args.repo_root, account["instance"], "work",
                                           "invox-journal-export")
    os.makedirs(out_dir, exist_ok=True)

    journals = []
    for path in args.files:
        journals.extend(yayoi_journal.load_journals(path))
    source_file = ", ".join(os.path.basename(p) for p in args.files)

    department_by_invoice = {}
    for invoice_id in dict.fromkeys(j["invoice_id"] for j in journals):
        detail = invox_api.api_call(
            token, "GET", "invoice_receive/invoice/get",
            params={"invox_company_code": account["invox_company_code"],
                    "invoice_id": invoice_id})
        department_by_invoice[invoice_id] = detail.get("department_name")

    department_to_company = account.get("department_to_company") or {}
    if not department_to_company:
        print("accounts.json の該当アカウントに department_to_company（invox部門名→会社名の対応表）が"
              "ありません。会社を推測できないため中断します", file=sys.stderr)
        return 1
    by_company, unknown = yayoi_journal.split_by_company(
        journals, department_by_invoice, department_to_company)
    unbalanced = yayoi_journal.unbalanced(journals)

    companies = {}
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    # 部門不明・貸借不一致が1件でもあれば、どの会社の分も書き出さない。
    # 一部だけ書き出すと、終了コードを見ない呼び出し元が欠けたまま登録してしまうため
    writable = {} if (unknown or unbalanced) else by_company
    for company, company_journals in sorted(writable.items()):
        path = yayoi_journal.write_prepared(out_dir, company, source_file, company_journals, stamp)
        prepared = yayoi_journal.build_prepared(company, source_file, company_journals)
        companies[company] = {"count": prepared["count"],
                              "total_taxin": prepared["total_taxin"],
                              "file": path}
        append_log(args.repo_root, account["instance"],
                   f"prepare-journals: {company} {prepared['count']}件 "
                   f"{prepared['total_taxin']}円 -> {os.path.basename(path)}（元: {source_file}）")

    if unknown:
        append_log(args.repo_root, account["instance"],
                   f"prepare-journals 部門不明: {json.dumps(unknown, ensure_ascii=False)}")
        print("部門対応表に無い（または部門が取得できない）請求書があります。会社を推測せず、"
              "どの会社の分も書き出していません（department_to_company を直して再実行）: "
              + json.dumps(unknown, ensure_ascii=False), file=sys.stderr)
    if unbalanced:
        append_log(args.repo_root, account["instance"],
                   f"prepare-journals 貸借不一致: {json.dumps(unbalanced, ensure_ascii=False)}")
        print("貸借が一致しない仕訳があります。どの会社の分も書き出していません: "
              + json.dumps(unbalanced, ensure_ascii=False), file=sys.stderr)

    print(json.dumps({"account": account["name"], "source_file": source_file,
                      "total_count": len(journals), "companies": companies,
                      "unknown_department": unknown, "unbalanced": unbalanced},
                     ensure_ascii=False))
    return 1 if (unknown or unbalanced) else 0


def cmd_apply_invoice(args: argparse.Namespace) -> int:
    """請求書を申請する（承認フローの手前の工程）。

    invoxのステータスは not_applied（未申請）→ wait_approval（承認待ち）→ wait_export → completed。
    請求書によっては特定の担当者名義で申請する必要がある（部門ごとの運用はインスタンス側で決める）。
    申請者は`staff_code`で指定する
    （担当者コードを取得するAPIは存在しないので、invox画面「設定＞担当者」で調べて
    accounts.jsonの`apply_staff_code`に入れておく）。
    """
    account = load_account_config(args.accounts_json, args.account_key)
    staff_code = getattr(args, "staff_code", None) or account.get("apply_staff_code")
    if not staff_code:
        print("申請者の staff_code が分かりません。引数で渡すか、accounts.jsonの "
              "apply_staff_code に設定してください（invox画面「設定＞担当者」で確認できます）",
              file=sys.stderr)
        return 1
    credentials_path = resolve_path(args.repo_root, account["credentials_path"])
    token = invox_api.ensure_access_token(credentials_path)
    body = {
        "invox_company_code": account["invox_company_code"],
        "invoice_id": args.invoice_id,
        "staff_code": staff_code,
    }
    appr_path_id = getattr(args, "appr_path_id", None)
    if appr_path_id:
        body["appr_path_id"] = int(appr_path_id) if str(appr_path_id).isdigit() else appr_path_id
    result = invox_api.api_call(token, "POST", "invoice_receive/invoice/apply", json_body=body)
    response_json = json.dumps(result, ensure_ascii=False)
    append_log(
        args.repo_root, account["instance"],
        f"apply OK: {args.invoice_id}（{account['name']}） staff_code={staff_code} "
        f"response={response_json}",
    )
    print(f"申請成功: {args.invoice_id}（{account['name']}） staff_code={staff_code} "
          f"response={response_json}")
    return 0


# invoxが「データ化できない」と判定したときのメッセージに現れる語。
# この語を含む注意が付いている書類だけを削除の対象にする。
UNPROCESSABLE_MARKER = "データ化できません"


def cmd_delete_invoice(args: argparse.Namespace) -> int:
    """請求書（アップロードされた書類）を削除する。

    invoxに請求書以外の書類（作業報告書など）が上がると、invoxが「非対応の書類のため
    データ化できません」等の注意を付けたまま未申請に残り続け、申請も承認もできない。
    こうした書類だけを消すための口（アイコンが×の書類は申請も承認もできないため）。

    **削除は取り消せない**ので、削除前に`invoice/get`で中身を確かめ、
    (1)データ化できない旨の注意が付いていて (2)仕入先・金額・タイトルがいずれも空
    の場合だけ実行する。どちらかを満たさなければ消さずに終了コード1で止める
    （`--force`で安全弁を外せるが、オーナーの個別指示があるときだけ使う）。
    消したものは二度と確認できないため、削除前の内容をログに残してから消す。
    """
    account = load_account_config(args.accounts_json, args.account_key)
    credentials_path = resolve_path(args.repo_root, account["credentials_path"])
    token = invox_api.ensure_access_token(credentials_path)
    params = {"invox_company_code": account["invox_company_code"], "invoice_id": args.invoice_id}
    detail = invox_api.api_call(token, "GET", "invoice_receive/invoice/get", params=params)

    messages = detail.get("messages") or []
    unprocessable = [m for m in messages if UNPROCESSABLE_MARKER in (m.get("message") or "")]
    has_data = any(detail.get(k) for k in ("supplier_name", "amt_invoice", "title"))
    if not getattr(args, "force", False):
        if not unprocessable:
            print(f"{args.invoice_id} には「{UNPROCESSABLE_MARKER}」の注意がありません。"
                  f"データ化できない書類ではないため削除しません: "
                  f"{json.dumps([m.get('message') for m in messages], ensure_ascii=False)}",
                  file=sys.stderr)
            return 1
        if has_data:
            print(f"{args.invoice_id} はデータが入っている（仕入先・金額・タイトルのいずれかがある）ため"
                  f"削除しません: supplier={detail.get('supplier_name')} "
                  f"amt={detail.get('amt_invoice')} title={detail.get('title')}", file=sys.stderr)
            return 1

    before = {k: detail.get(k) for k in ("status", "department_name", "supplier_name",
                                         "amt_invoice", "title", "create_datetime", "url")}
    before["messages"] = [m.get("message") for m in messages]
    append_log(args.repo_root, account["instance"],
               f"delete 実行前の内容: {args.invoice_id}（{account['name']}） "
               f"{json.dumps(before, ensure_ascii=False)}")
    result = invox_api.api_call(token, "DELETE", "invoice_receive/invoice/delete", json_body=params)
    response_json = json.dumps(result, ensure_ascii=False)
    append_log(args.repo_root, account["instance"],
               f"delete OK: {args.invoice_id}（{account['name']}） response={response_json}")
    print(f"削除しました: {args.invoice_id}（{account['name']}） "
          f"{json.dumps(before, ensure_ascii=False)} response={response_json}")
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

    p_apply = sub.add_parser("apply-invoice")
    p_apply.add_argument("account_key")
    p_apply.add_argument("invoice_id")
    # 未指定なら accounts.json の apply_staff_code を使う
    p_apply.add_argument("staff_code", nargs="?", default=None)
    p_apply.add_argument("appr_path_id", nargs="?", default=None)
    p_apply.set_defaults(func=cmd_apply_invoice)

    p_delete = sub.add_parser("delete-invoice")
    p_delete.add_argument("account_key")
    p_delete.add_argument("invoice_id")
    p_delete.add_argument("--force", action="store_true",
                          help="安全弁（データ化できない書類か・中身が空か）を外して削除する")
    p_delete.set_defaults(func=cmd_delete_invoice)

    p_update = sub.add_parser("update-invoice")
    p_update.add_argument("account_key")
    p_update.add_argument("invoice_id")
    # 3つとも「指定しなければ触らない」（invoxのupdateは部分更新）。"true"/"false"で明示する。
    for flag in ("exclude-export-invoice", "exclude-export-payment",
                 "exclude-export-expense-journal"):
        p_update.add_argument(f"--{flag}", choices=["true", "false"], default=None)
    p_update.add_argument("--memo-append", default=None, help="メモの末尾に1行追記する（既存のメモは残す）")
    p_update.set_defaults(func=cmd_update_invoice)

    p_export = sub.add_parser("export-journal")
    p_export.add_argument("account_key")
    # expense＝費用計上仕訳（発生）、payment＝支払仕訳。公式ドキュメントのenumどおり
    p_export.add_argument("--journal-type", choices=["expense", "payment"], default="expense")
    p_export.add_argument("--posting-date-from", default=None, help="計上日From（YYYY/MM/DD）")
    p_export.add_argument("--posting-date-to", default=None, help="計上日To（YYYY/MM/DD）")
    p_export.add_argument("--department-code", default=None)
    p_export.add_argument("--out-dir", default=None,
                          help="保存先（既定：<repo-root>/<instance>/work/invox-journal-export）")
    p_export.add_argument("--max-pages", type=int, default=50,
                          help="204が返るまで繰り返す回数の上限（1回＝最大100請求書分）")
    p_export.set_defaults(func=cmd_export_journal)

    p_prepare = sub.add_parser("prepare-journals")
    p_prepare.add_argument("account_key")
    p_prepare.add_argument("files", nargs="+", help="export-journalで保存した弥生形式ファイル")
    p_prepare.add_argument("--out-dir", default=None)
    p_prepare.set_defaults(func=cmd_prepare_journals)

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
