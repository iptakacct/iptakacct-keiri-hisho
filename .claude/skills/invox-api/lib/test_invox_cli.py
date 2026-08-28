import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import invox_api
from invox_cli import load_account_config, ConfigError, cmd_healthcheck, cmd_list_invoices, cmd_get_invoice, cmd_approve_invoice, main


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
