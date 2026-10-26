import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import invox_api
from invox_cli import load_account_config, ConfigError, cmd_healthcheck, cmd_list_invoices, cmd_get_invoice, cmd_approve_invoice, cmd_export_journal, cmd_prepare_journals, cmd_apply_invoice, cmd_delete_invoice, build_parser, main


def _write_accounts_json(d, credentials_relpath="creds.json"):
    path = os.path.join(d, "accounts.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "accounts": [
                {
                    "name": "SAMPLE（サンプルグループ）",
                    "invox_company_code": "cc-1",
                    "instance": "samplegroup",
                    "credentials_path": credentials_relpath,
                    "slack_channel": "CH-SAMPLE-E",
                    "department_to_company": {"A部門": "A社", "B部門": "B社"},
                }
            ]
        }, f, ensure_ascii=False)
    return path


class TestLoadAccountConfig(unittest.TestCase):
    def test_finds_account_by_name(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_accounts_json(d)
            account = load_account_config(path, "SAMPLE（サンプルグループ）")
        self.assertEqual(account["invox_company_code"], "cc-1")

    def test_raises_when_not_found(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_accounts_json(d)
            with self.assertRaises(ConfigError):
                load_account_config(path, "存在しないアカウント")


class TestHealthcheck(unittest.TestCase):
    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_success_logs_ok_and_returns_zero(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = {"count": 0, "results": []}
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            args = argparse.Namespace(accounts_json=accounts_json, repo_root=repo_root)
            exit_code = cmd_healthcheck(args)
            log_path = os.path.join(repo_root, "samplegroup", "work", "invox-api.log")
            with open(log_path, "r", encoding="utf-8") as f:
                log_content = f.read()
        self.assertEqual(exit_code, 0)
        self.assertIn("healthcheck OK", log_content)

    @mock.patch("invox_api.ensure_access_token")
    def test_failure_logs_ng_and_returns_one(self, mock_ensure):
        mock_ensure.side_effect = invox_api.InvoxApiError("invalid_grant")
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            args = argparse.Namespace(accounts_json=accounts_json, repo_root=repo_root)
            exit_code = cmd_healthcheck(args)
            log_path = os.path.join(repo_root, "samplegroup", "work", "invox-api.log")
            with open(log_path, "r", encoding="utf-8") as f:
                log_content = f.read()
        self.assertEqual(exit_code, 1)
        self.assertIn("healthcheck NG", log_content)


class TestListInvoices(unittest.TestCase):
    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_prints_results_as_json(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = {"count": 1, "results": [{"invoice_id": "inv-1"}]}
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            args = argparse.Namespace(
                accounts_json=accounts_json, repo_root=repo_root,
                account_key="SAMPLE（サンプルグループ）", department_code="PMO",
            )
            with mock.patch("builtins.print") as mock_print:
                exit_code = cmd_list_invoices(args)
        self.assertEqual(exit_code, 0)
        printed = mock_print.call_args[0][0]
        parsed = json.loads(printed)
        self.assertEqual(parsed["results"][0]["invoice_id"], "inv-1")
        call_kwargs = mock_api_call.call_args.kwargs
        self.assertEqual(call_kwargs["params"]["invox_company_code"], "cc-1")
        self.assertEqual(call_kwargs["params"]["department_code"], "PMO")
        # ページ指定なしの場合は page/limit をparamsに含めない
        self.assertNotIn("page", call_kwargs["params"])
        self.assertNotIn("limit", call_kwargs["params"])

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_forwards_date_filters_when_given(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = {"count": 0, "results": []}
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            args = argparse.Namespace(
                accounts_json=accounts_json, repo_root=repo_root,
                account_key="SAMPLE（サンプルグループ）",
                create_date_from="2026/08/01", create_date_to="2026/08/31",
                fixed_only="false",
            )
            with mock.patch("builtins.print"):
                cmd_list_invoices(args)
        params = mock_api_call.call_args.kwargs["params"]
        self.assertEqual(params["create_date_from"], "2026/08/01")
        self.assertEqual(params["create_date_to"], "2026/08/31")
        self.assertEqual(params["fixed_only"], "false")

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_forwards_page_and_limit_when_given(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = {"count": 1, "results": []}
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            args = argparse.Namespace(
                accounts_json=accounts_json, repo_root=repo_root,
                account_key="SAMPLE（サンプルグループ）", department_code=None,
                page="2", limit="50",
            )
            exit_code = cmd_list_invoices(args)
        self.assertEqual(exit_code, 0)
        call_kwargs = mock_api_call.call_args.kwargs
        self.assertEqual(call_kwargs["params"]["page"], "2")
        self.assertEqual(call_kwargs["params"]["limit"], "50")


class TestGetInvoice(unittest.TestCase):
    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_fetches_detail_with_journal_info_and_prints_json(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = {"invoice_id": "inv-1", "status": "wait_export"}
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            args = argparse.Namespace(
                accounts_json=accounts_json, repo_root=repo_root,
                account_key="SAMPLE（サンプルグループ）", invoice_id="inv-1",
                include_journal_info=True,
            )
            with mock.patch("builtins.print") as mock_print:
                exit_code = cmd_get_invoice(args)
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(mock_print.call_args[0][0])["status"], "wait_export")
        call = mock_api_call.call_args
        self.assertEqual(call.args[1:3], ("GET", "invoice_receive/invoice/get"))
        self.assertEqual(call.kwargs["params"], {
            "invox_company_code": "cc-1", "invoice_id": "inv-1", "include_journal_info": "true",
        })

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_omits_journal_info_flag_when_not_requested(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = {"invoice_id": "inv-1"}
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            args = argparse.Namespace(
                accounts_json=accounts_json, repo_root=repo_root,
                account_key="SAMPLE（サンプルグループ）", invoice_id="inv-1",
                include_journal_info=False,
            )
            with mock.patch("builtins.print"):
                cmd_get_invoice(args)
        self.assertNotIn("include_journal_info", mock_api_call.call_args.kwargs["params"])


class TestConfirmMessage(unittest.TestCase):
    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_posts_confirm_and_logs(self, mock_ensure, mock_api_call):
        from invox_cli import cmd_confirm_message
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = {"result": "ok"}
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            args = argparse.Namespace(
                accounts_json=accounts_json, repo_root=repo_root,
                account_key="SAMPLE（サンプルグループ）", invoice_id="inv-1", message_id="36595723",
            )
            with mock.patch("builtins.print") as mock_print:
                exit_code = cmd_confirm_message(args)
            log_path = os.path.join(repo_root, "samplegroup", "work", "invox-api.log")
            with open(log_path, encoding="utf-8") as f:
                log_text = f.read()
        self.assertEqual(exit_code, 0)
        call = mock_api_call.call_args
        self.assertEqual(call.args[1:3], ("POST", "invoice_receive/invoice/message/confirm"))
        self.assertEqual(call.kwargs["json_body"], {
            "invox_company_code": "cc-1", "invoice_id": "inv-1", "message_id": 36595723,
        })
        self.assertIn("message_id=36595723", log_text)
        self.assertIn("36595723", mock_print.call_args[0][0])


class TestApproveInvoice(unittest.TestCase):
    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_calls_approve_endpoint_and_logs_response_content(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = {"result": "approved", "invoice_id": "inv-1"}
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            args = argparse.Namespace(
                accounts_json=accounts_json, repo_root=repo_root,
                account_key="SAMPLE（サンプルグループ）", invoice_id="inv-1",
                approve_task_name="二次承認", appr_path_id="1",
            )
            with mock.patch("builtins.print") as mock_print:
                exit_code = cmd_approve_invoice(args)
            log_path = os.path.join(repo_root, "samplegroup", "work", "invox-api.log")
            with open(log_path, "r", encoding="utf-8") as f:
                log_content = f.read()
        self.assertEqual(exit_code, 0)
        self.assertIn("approve OK: inv-1", log_content)
        # レスポンス内容とアカウント名の両方が記録・出力されること（Finding 4）
        self.assertIn("SAMPLE（サンプルグループ）", log_content)
        self.assertIn("approved", log_content)
        printed = " ".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertIn("SAMPLE（サンプルグループ）", printed)
        self.assertIn("approved", printed)
        call_kwargs = mock_api_call.call_args.kwargs
        self.assertEqual(call_kwargs["json_body"]["invoice_id"], "inv-1")

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_appr_path_id_omitted_from_body_when_not_given(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = {"result": "ok"}
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            args = argparse.Namespace(
                accounts_json=accounts_json, repo_root=repo_root,
                account_key="SAMPLE（サンプルグループ）", invoice_id="inv-1",
                approve_task_name="2次承認", appr_path_id=None,
            )
            with mock.patch("builtins.print"):
                cmd_approve_invoice(args)
        body = mock_api_call.call_args.kwargs["json_body"]
        self.assertNotIn("appr_path_id", body)
        self.assertEqual(body["approve_task_name"], "2次承認")

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_appr_path_id_passed_through_as_string(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = {}
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            args = argparse.Namespace(
                accounts_json=accounts_json, repo_root=repo_root,
                account_key="SAMPLE（サンプルグループ）", invoice_id="inv-1",
                approve_task_name="二次承認", appr_path_id="1",
            )
            exit_code = cmd_approve_invoice(args)
        self.assertEqual(exit_code, 0)
        call_kwargs = mock_api_call.call_args.kwargs
        # 数字だけの値はAPI仕様（number型）に合わせて数値で送る（2026-08-28実機：承認待ち分は8254等の数値）
        self.assertEqual(call_kwargs["json_body"]["appr_path_id"], 1)

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_non_numeric_appr_path_id_does_not_traceback(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = {}
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            args = argparse.Namespace(
                accounts_json=accounts_json, repo_root=repo_root,
                account_key="SAMPLE（サンプルグループ）", invoice_id="inv-1",
                approve_task_name="二次承認", appr_path_id="path-ABC",
            )
            exit_code = cmd_approve_invoice(args)
        self.assertEqual(exit_code, 0)
        call_kwargs = mock_api_call.call_args.kwargs
        self.assertEqual(call_kwargs["json_body"]["appr_path_id"], "path-ABC")


class TestMainErrorHandling(unittest.TestCase):
    def test_unknown_account_key_prints_one_line_message_and_exits_1(self):
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = main([
                    "--accounts-json", accounts_json, "--repo-root", repo_root,
                    "list-invoices", "存在しないアカウント",
                ])
        self.assertEqual(exit_code, 1)
        output = stderr.getvalue()
        self.assertIn("存在しないアカウント", output)
        self.assertNotIn("Traceback", output)

    def test_missing_accounts_json_prints_one_line_message_and_exits_1(self):
        with tempfile.TemporaryDirectory() as repo_root:
            missing_accounts_json = os.path.join(repo_root, "does-not-exist.json")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = main([
                    "--accounts-json", missing_accounts_json, "--repo-root", repo_root,
                    "list-invoices", "SAMPLE（サンプルグループ）",
                ])
        self.assertEqual(exit_code, 1)
        output = stderr.getvalue()
        # Windows環境ではFileNotFoundErrorのメッセージ中でパス区切り文字が
        # 二重エスケープされることがあるため、ファイル名部分のみで照合する。
        self.assertIn(os.path.basename(missing_accounts_json), output)
        self.assertNotIn("Traceback", output)

    @mock.patch("invox_api.ensure_access_token")
    def test_invox_api_error_prints_one_line_message_and_exits_1(self, mock_ensure):
        mock_ensure.side_effect = invox_api.InvoxApiError("invalid_grant")
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = main([
                    "--accounts-json", accounts_json, "--repo-root", repo_root,
                    "list-invoices", "SAMPLE（サンプルグループ）",
                ])
        self.assertEqual(exit_code, 1)
        output = stderr.getvalue()
        self.assertIn("invalid_grant", output)
        self.assertNotIn("Traceback", output)

    def test_malformed_accounts_json_prints_one_line_message_and_exits_1(self):
        # accounts.json自体が壊れているケース（JSONDecodeError）。
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = os.path.join(repo_root, "accounts.json")
            with open(accounts_json, "w", encoding="utf-8") as f:
                f.write("{これはJSONとして壊れている")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = main([
                    "--accounts-json", accounts_json, "--repo-root", repo_root,
                    "list-invoices", "SAMPLE（サンプルグループ）",
                ])
        self.assertEqual(exit_code, 1)
        output = stderr.getvalue()
        self.assertNotIn("Traceback", output)

    def test_credentials_missing_expires_at_prints_one_line_message_and_exits_1(self):
        # 認可コード交換前（client_id/client_secretのみ保存された状態）のトークンファイルを
        # 想定したケース（KeyError）。
        with tempfile.TemporaryDirectory() as repo_root:
            creds_path = os.path.join(repo_root, "creds.json")
            with open(creds_path, "w", encoding="utf-8") as f:
                json.dump({"client_id": "cid", "client_secret": "secret"}, f)
            accounts_json = _write_accounts_json(repo_root, credentials_relpath="creds.json")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = main([
                    "--accounts-json", accounts_json, "--repo-root", repo_root,
                    "list-invoices", "SAMPLE（サンプルグループ）",
                ])
        self.assertEqual(exit_code, 1)
        output = stderr.getvalue()
        self.assertNotIn("Traceback", output)



if __name__ == "__main__":
    unittest.main()


class TestUpdateInvoice(unittest.TestCase):
    def _run(self, **flags):
        from invox_cli import cmd_update_invoice
        defaults = {"exclude_export_invoice": None, "exclude_export_payment": None,
                    "exclude_export_expense_journal": None}
        defaults.update(flags)
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            args = argparse.Namespace(
                accounts_json=accounts_json, repo_root=repo_root,
                account_key="SAMPLE（サンプルグループ）", invoice_id="inv-1", **defaults,
            )
            with mock.patch("builtins.print"):
                exit_code = cmd_update_invoice(args)
            log_path = os.path.join(repo_root, "samplegroup", "work", "invox-api.log")
            log_text = ""
            if os.path.exists(log_path):
                with open(log_path, encoding="utf-8") as f:
                    log_text = f.read()
        return exit_code, log_text

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_sends_only_specified_flags(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = {}
        exit_code, log_text = self._run(exclude_export_expense_journal="true")
        self.assertEqual(exit_code, 0)
        call = mock_api_call.call_args
        self.assertEqual(call.args[1:3], ("PUT", "invoice_receive/invoice/update"))
        # 指定しなかった項目は送らない（invoxのupdateは部分更新なので、送らなければ元のまま）
        self.assertEqual(call.kwargs["json_body"], {
            "invox_company_code": "cc-1", "invoice_id": "inv-1",
            "invoice": {"exclude_export_expense_journal": True},
        })
        self.assertIn("update OK: inv-1", log_text)

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_false_is_sent_as_boolean_false(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = {}
        self._run(exclude_export_payment="false")
        self.assertEqual(mock_api_call.call_args.kwargs["json_body"]["invoice"],
                         {"exclude_export_payment": False})

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_errors_when_no_flag_given(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        with contextlib.redirect_stderr(io.StringIO()):
            exit_code, _ = self._run()
        self.assertEqual(exit_code, 1)
        mock_api_call.assert_not_called()


class TestExportJournal(unittest.TestCase):
    """journal/exportは呼んだ分が二度と取り出せない。
    受け取ったバイト列を「加工せず・次の呼び出しの前に」ファイルへ残すことが最重要。"""

    def _args(self, repo_root, accounts_json, **overrides):
        base = dict(accounts_json=accounts_json, repo_root=repo_root,
                    account_key="SAMPLE（サンプルグループ）", journal_type="expense",
                    posting_date_from=None, posting_date_to=None,
                    department_code=None, out_dir=None, max_pages=50)
        base.update(overrides)
        return argparse.Namespace(**base)

    @mock.patch("invox_api.api_call_raw")
    @mock.patch("invox_api.ensure_access_token")
    def test_saves_each_chunk_raw_and_stops_at_204(self, mock_ensure, mock_raw):
        mock_ensure.return_value = "token-1"
        chunk1 = "2111,,,2026/07/31,支払報酬料".encode("cp932")
        chunk2 = b"2100,,,2026/08/31,x"
        mock_raw.side_effect = [
            (200, "text/csv; charset=Shift_JIS", chunk1),
            (200, "text/csv; charset=Shift_JIS", chunk2),
            (204, "", b""),
        ]
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                exit_code = cmd_export_journal(self._args(repo_root, accounts_json))
            out_dir = os.path.join(repo_root, "samplegroup", "work", "invox-journal-export")
            data_files = sorted(f for f in os.listdir(out_dir) if not f.endswith(".meta.json"))
            meta_files = sorted(f for f in os.listdir(out_dir) if f.endswith(".meta.json"))
            with open(os.path.join(out_dir, data_files[0]), "rb") as f:
                saved1 = f.read()
            with open(os.path.join(out_dir, data_files[1]), "rb") as f:
                saved2 = f.read()
            with open(os.path.join(out_dir, meta_files[0]), "r", encoding="utf-8") as f:
                meta1 = json.load(f)
            with open(os.path.join(repo_root, "samplegroup", "work", "invox-api.log"), encoding="utf-8") as f:
                log_content = f.read()
        self.assertEqual(exit_code, 0)
        self.assertEqual(mock_raw.call_count, 3)
        self.assertEqual(len(data_files), 2)
        self.assertEqual(saved1, chunk1)  # バイト列そのまま（cp932のまま、改行も触らない）
        self.assertEqual(saved2, chunk2)
        self.assertEqual(meta1["http_status"], 200)
        self.assertEqual(meta1["content_type"], "text/csv; charset=Shift_JIS")
        self.assertEqual(meta1["request"]["journal_type"], "expense")
        self.assertEqual(meta1["bytes"], len(chunk1))
        summary = json.loads(out.getvalue())
        self.assertEqual(summary["chunks"], 2)
        self.assertEqual([c["bytes"] for c in summary["files"]], [len(chunk1), len(chunk2)])
        self.assertIn("journal export saved", log_content)

    @mock.patch("invox_api.api_call_raw")
    @mock.patch("invox_api.ensure_access_token")
    def test_returns_0_and_no_files_when_nothing_to_export(self, mock_ensure, mock_raw):
        mock_ensure.return_value = "token-1"
        mock_raw.return_value = (204, "", b"")
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                exit_code = cmd_export_journal(self._args(repo_root, accounts_json))
            out_dir = os.path.join(repo_root, "samplegroup", "work", "invox-journal-export")
            files = os.listdir(out_dir) if os.path.isdir(out_dir) else []
        self.assertEqual(exit_code, 0)
        self.assertEqual(files, [])
        self.assertEqual(json.loads(out.getvalue())["chunks"], 0)

    @mock.patch("invox_cli.save_export_chunk")
    @mock.patch("invox_api.api_call_raw")
    @mock.patch("invox_api.ensure_access_token")
    def test_stops_immediately_when_save_fails(self, mock_ensure, mock_raw, mock_save):
        mock_ensure.return_value = "token-1"
        mock_raw.side_effect = [(200, "text/csv", b"a"), (200, "text/csv", b"b"), (204, "", b"")]
        mock_save.side_effect = OSError("disk full")
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                exit_code = cmd_export_journal(self._args(repo_root, accounts_json))
            with open(os.path.join(repo_root, "samplegroup", "work", "invox-api.log"), encoding="utf-8") as f:
                log_content = f.read()
        self.assertEqual(exit_code, 1)
        self.assertEqual(mock_raw.call_count, 1)  # 保存に失敗したら次のチャンクを取りに行かない
        self.assertIn("SAVE FAILED", log_content)
        self.assertIn("保存に失敗", err.getvalue())

    @mock.patch("invox_api.api_call_raw")
    @mock.patch("invox_api.ensure_access_token")
    def test_sends_company_code_journal_type_and_filters(self, mock_ensure, mock_raw):
        mock_ensure.return_value = "token-1"
        mock_raw.return_value = (204, "", b"")
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            with contextlib.redirect_stdout(io.StringIO()):
                cmd_export_journal(self._args(
                    repo_root, accounts_json, journal_type="payment",
                    posting_date_from="2026/08/01", posting_date_to="2026/08/31"))
        body = mock_raw.call_args.kwargs["json_body"]
        self.assertEqual(body["invox_company_code"], "cc-1")
        self.assertEqual(body["journal_type"], "payment")
        self.assertEqual(body["posting_date_from"], "2026/08/01")
        self.assertEqual(body["posting_date_to"], "2026/08/31")
        self.assertNotIn("department_code", body)  # 未指定のものは送らない

    def test_parser_rejects_unknown_journal_type(self):
        parser = build_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["--accounts-json", "a", "--repo-root", ".",
                                   "export-journal", "SAMPLE（サンプルグループ）", "--journal-type", "bogus"])


class TestPrepareJournals(unittest.TestCase):
    """export-journalで保存した弥生データを、会社別のprepared-*.jsonへ変換するCLI。
    会社の振り分けには invoice/get の department_name が必要なので、請求書IDごとに1回だけ引く。"""

    ROW = ("2111,,,2026/08/31,支払報酬料,鈴木ゆきえ,,課仕 10%,4680,425,"
           "未払金,,,対象外,4680,0,,,2026/09/28,3,,invox:IR0000000001,0,0,0")
    ROW2 = ROW.replace("IR0000000001", "IR0000000002")

    def _write_export(self, d, *rows, name="export-01.txt"):
        path = os.path.join(d, name)
        with open(path, "wb") as f:
            f.write(("\r\n".join(rows) + "\r\n").encode("cp932"))
        return path

    def _args(self, repo_root, accounts_json, files, **overrides):
        base = dict(accounts_json=accounts_json, repo_root=repo_root,
                    account_key="SAMPLE（サンプルグループ）", files=files, out_dir=None)
        base.update(overrides)
        return argparse.Namespace(**base)

    @staticmethod
    def _department_response(department_by_invoice):
        def fake(token, method, path, params=None, json_body=None):
            return {"invoice_id": params["invoice_id"],
                    "department_name": department_by_invoice[params["invoice_id"]]}
        return fake

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_writes_one_prepared_file_per_company(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.side_effect = self._department_response(
            {"IR0000000001": "B部門", "IR0000000002": "A部門"})
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            export = self._write_export(repo_root, self.ROW, self.ROW2)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                exit_code = cmd_prepare_journals(
                    self._args(repo_root, accounts_json, [export], out_dir=repo_root))
            names = sorted(f for f in os.listdir(repo_root) if f.startswith("prepared-"))
            b_name = next(f for f in names if f.startswith("prepared-B社-"))
            with open(os.path.join(repo_root, b_name), encoding="utf-8") as f:
                b_co = json.load(f)
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(names), 2)
        # 実行ごとに別ファイルにする（過去の取り込み結果を上書きしないため）
        self.assertRegex(b_name, r"^prepared-B社-\d{4}-\d{2}-\d{2}-\d{6}\.json$")
        self.assertTrue(any(f.startswith("prepared-A社-") for f in names))
        self.assertEqual(b_co["count"], 1)
        self.assertEqual(b_co["total_taxin"], 4680)
        self.assertEqual(b_co["journals"][0]["invoice_id"], "IR0000000001")
        summary = json.loads(out.getvalue())
        self.assertEqual(summary["companies"]["B社"]["count"], 1)
        self.assertEqual(summary["unknown_department"], [])
        self.assertEqual(summary["unbalanced"], [])

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_fetches_each_invoice_once_even_across_multiple_rows(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.side_effect = self._department_response({"IR0000000001": "B部門"})
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            export = self._write_export(repo_root, self.ROW, self.ROW)
            with contextlib.redirect_stdout(io.StringIO()):
                cmd_prepare_journals(self._args(repo_root, accounts_json, [export], out_dir=repo_root))
        self.assertEqual(mock_api_call.call_count, 1)

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_unknown_department_is_reported_and_exit_code_is_1(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.side_effect = self._department_response(
            {"IR0000000001": "B部門", "IR0000000002": "新設部門"})
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            export = self._write_export(repo_root, self.ROW, self.ROW2)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                exit_code = cmd_prepare_journals(
                    self._args(repo_root, accounts_json, [export], out_dir=repo_root))
            names = sorted(f for f in os.listdir(repo_root) if f.startswith("prepared-"))
        self.assertEqual(exit_code, 1)
        self.assertEqual(names, [])  # 一部だけ書き出すと欠けたまま登録されうるので、何も出さない
        summary = json.loads(out.getvalue())
        self.assertEqual(summary["unknown_department"],
                         [{"invoice_id": "IR0000000002", "department_name": "新設部門"}])

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_unbalanced_journal_is_reported_and_exit_code_is_1(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.side_effect = self._department_response({"IR0000000001": "B部門"})
        broken = self.ROW.replace(",未払金,,,対象外,4680,0", ",未払金,,,対象外,4000,0")
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            export = self._write_export(repo_root, broken)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                exit_code = cmd_prepare_journals(
                    self._args(repo_root, accounts_json, [export], out_dir=repo_root))
            summary = json.loads(out.getvalue())
            names = [f for f in os.listdir(repo_root) if f.startswith("prepared-")]
        self.assertEqual(exit_code, 1)
        self.assertEqual(names, [])
        self.assertEqual(summary["unbalanced"],
                         [{"invoice_id": "IR0000000001", "debit_total": 4680, "credit_total": 4000}])

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_combines_multiple_export_files(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.side_effect = self._department_response(
            {"IR0000000001": "B部門", "IR0000000002": "B部門"})
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            first = self._write_export(repo_root, self.ROW, name="export-01.txt")
            second = self._write_export(repo_root, self.ROW2, name="export-02.txt")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                exit_code = cmd_prepare_journals(
                    self._args(repo_root, accounts_json, [first, second], out_dir=repo_root))
            name = next(f for f in os.listdir(repo_root) if f.startswith("prepared-B社-"))
            with open(os.path.join(repo_root, name), encoding="utf-8") as f:
                b_co = json.load(f)
        self.assertEqual(exit_code, 0)
        self.assertEqual(b_co["count"], 2)
        self.assertEqual(b_co["source_file"], "export-01.txt, export-02.txt")


class TestApplyInvoice(unittest.TestCase):
    """申請（承認フローの手前）。申請者は staff_code で指定する。"""

    def _args(self, repo_root, accounts_json, **overrides):
        base = dict(accounts_json=accounts_json, repo_root=repo_root,
                    account_key="SAMPLE（サンプルグループ）", invoice_id="IR0000000001",
                    staff_code=None, appr_path_id=None)
        base.update(overrides)
        return argparse.Namespace(**base)

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_sends_company_invoice_and_staff_code(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = {}
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = cmd_apply_invoice(
                    self._args(repo_root, accounts_json, staff_code="staff-admin"))
            with open(os.path.join(repo_root, "samplegroup", "work", "invox-api.log"), encoding="utf-8") as f:
                log_content = f.read()
        self.assertEqual(exit_code, 0)
        self.assertEqual(mock_api_call.call_args[0][2], "invoice_receive/invoice/apply")
        body = mock_api_call.call_args.kwargs["json_body"]
        self.assertEqual(body, {"invox_company_code": "cc-1", "invoice_id": "IR0000000001",
                                "staff_code": "staff-admin"})
        self.assertIn("apply OK", log_content)

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_sends_numeric_appr_path_id_as_number(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = {}
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            with contextlib.redirect_stdout(io.StringIO()):
                cmd_apply_invoice(self._args(repo_root, accounts_json,
                                             staff_code="staff-admin", appr_path_id="8254"))
        self.assertEqual(mock_api_call.call_args.kwargs["json_body"]["appr_path_id"], 8254)

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_falls_back_to_apply_staff_code_in_accounts_json(self, mock_ensure, mock_api_call):
        """毎回コードを打たなくてよいように、accounts.jsonの apply_staff_code を既定にする。"""
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = {}
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            with open(accounts_json, encoding="utf-8") as f:
                config = json.load(f)
            config["accounts"][0]["apply_staff_code"] = "S-KEIRI"
            with open(accounts_json, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False)
            with contextlib.redirect_stdout(io.StringIO()):
                cmd_apply_invoice(self._args(repo_root, accounts_json))
        self.assertEqual(mock_api_call.call_args.kwargs["json_body"]["staff_code"], "S-KEIRI")

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_errors_without_staff_code_and_does_not_call_api(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                exit_code = cmd_apply_invoice(self._args(repo_root, accounts_json))
        self.assertEqual(exit_code, 1)
        self.assertEqual(mock_api_call.call_count, 0)
        self.assertIn("staff_code", err.getvalue())


class TestDeleteInvoice(unittest.TestCase):
    """請求書の削除。取り消せないので、対象は「データ化できない書類」に限る
    （安全弁として、削除前に invoice/get で
    データが入っていないことを確かめ、入っていたら消さずに止める。"""

    UNPROCESSABLE = {"invoice_id": "IR0000000001", "status": "not_applied",
                     "supplier_name": None, "amt_invoice": None, "title": None,
                     "messages": [{"code": "E10010010", "confirmed": False,
                                   "message": "非対応の書類のためデータ化できません。"}]}

    def _args(self, repo_root, accounts_json, **overrides):
        base = dict(accounts_json=accounts_json, repo_root=repo_root,
                    account_key="SAMPLE（サンプルグループ）", invoice_id="IR0000000001", force=False)
        base.update(overrides)
        return argparse.Namespace(**base)

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_deletes_unprocessable_document_and_logs_what_was_deleted(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.side_effect = [self.UNPROCESSABLE, {}]
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = cmd_delete_invoice(self._args(repo_root, accounts_json))
            with open(os.path.join(repo_root, "samplegroup", "work", "invox-api.log"), encoding="utf-8") as f:
                log_content = f.read()
        self.assertEqual(exit_code, 0)
        self.assertEqual(mock_api_call.call_args[0][1], "DELETE")
        self.assertEqual(mock_api_call.call_args[0][2], "invoice_receive/invoice/delete")
        self.assertEqual(mock_api_call.call_args.kwargs["json_body"],
                         {"invox_company_code": "cc-1", "invoice_id": "IR0000000001"})
        self.assertIn("delete OK", log_content)
        # 消したものは二度と見られないので、削除前の内容をログに残す
        self.assertIn("非対応の書類", log_content)

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_refuses_when_invoice_has_data(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = dict(self.UNPROCESSABLE, supplier_name="株式会社A",
                                          amt_invoice=1000.0)
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                exit_code = cmd_delete_invoice(self._args(repo_root, accounts_json))
        self.assertEqual(exit_code, 1)
        self.assertEqual(mock_api_call.call_count, 1)  # getだけ。deleteは呼ばない
        self.assertIn("データが入っている", err.getvalue())

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_refuses_when_document_was_digitized_successfully(self, mock_ensure, mock_api_call):
        """データ化できない旨のメッセージが無いものは、読めている書類なので消さない。"""
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = dict(self.UNPROCESSABLE, messages=[])
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                exit_code = cmd_delete_invoice(self._args(repo_root, accounts_json))
        self.assertEqual(exit_code, 1)
        self.assertEqual(mock_api_call.call_count, 1)
        self.assertIn("データ化できない", err.getvalue())

    @mock.patch("invox_api.api_call")
    @mock.patch("invox_api.ensure_access_token")
    def test_force_deletes_despite_safety_check(self, mock_ensure, mock_api_call):
        """安全弁を外して消したい場合の明示指定。オーナーの個別指示があるときだけ使う。"""
        mock_ensure.return_value = "token-1"
        mock_api_call.side_effect = [dict(self.UNPROCESSABLE, supplier_name="株式会社A",
                                          amt_invoice=1000.0, messages=[]), {}]
        with tempfile.TemporaryDirectory() as repo_root:
            accounts_json = _write_accounts_json(repo_root)
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = cmd_delete_invoice(self._args(repo_root, accounts_json, force=True))
        self.assertEqual(exit_code, 0)
        self.assertEqual(mock_api_call.call_args[0][1], "DELETE")
