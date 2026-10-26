import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import freee_api
from freee_cli import (
    load_company_config,
    ConfigError,
    cmd_healthcheck,
    cmd_authorize_start,
    cmd_authorize_finish,
    cmd_interest,
    monitor_options,
)


def _write_companies_json(d, credentials_relpath="creds.json"):
    path = os.path.join(d, "companies.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "companies": [
                {
                    "name": "株式会社サンプルA",
                    "company_id": 100001,
                    "instance": "サンプルAグループ",
                    "credentials_path": credentials_relpath,
                    "slack_channel": "CH-SAMPLE-D",
                }
            ]
        }, f, ensure_ascii=False)
    return path


class TestMonitorOptions(unittest.TestCase):
    def test_default_is_everything_on(self):
        self.assertEqual(monitor_options({"name": "A"}), {"enabled": True, "receipts": True})

    def test_enabled_false_turns_off_the_whole_company(self):
        self.assertEqual(monitor_options({"monitor": {"enabled": False}}),
                         {"enabled": False, "receipts": True})

    def test_receipts_false_turns_off_only_the_receipt_check(self):
        self.assertEqual(monitor_options({"monitor": {"receipts": False}}),
                         {"enabled": True, "receipts": False})



class TestLoadCompanyConfig(unittest.TestCase):
    def test_finds_company_by_name(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_companies_json(d)
            company = load_company_config(path, "株式会社サンプルA")
        self.assertEqual(company["company_id"], 100001)

    def test_finds_company_by_company_id(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_companies_json(d)
            company = load_company_config(path, "100001")
        self.assertEqual(company["name"], "株式会社サンプルA")

    def test_raises_when_not_found(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_companies_json(d)
            with self.assertRaises(ConfigError):
                load_company_config(path, "存在しない会社")


def _write_credentials(repo_root, relpath, data):
    path = os.path.join(repo_root, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


class TestAuthorizeStart(unittest.TestCase):
    """認可URLの表示だけを行うコマンド。ネットワークには触れないので実物のまま動かす。"""

    def test_prints_authorize_url_and_company_name(self):
        with tempfile.TemporaryDirectory() as repo_root:
            companies_json = _write_companies_json(
                repo_root, credentials_relpath="creds/creds.json")
            _write_credentials(repo_root, "creds/creds.json",
                               {"client_id": "test-client-id"})
            args = argparse.Namespace(
                companies_json=companies_json,
                repo_root=repo_root,
                company_key="株式会社サンプルA",
            )
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                exit_code = cmd_authorize_start(args)
            output = buf.getvalue()

        self.assertEqual(exit_code, 0)
        self.assertIn("株式会社サンプルA", output)
        self.assertIn("https://accounts.secure.freee.co.jp/public_api/authorize", output)
        self.assertIn("client_id=test-client-id", output)


class TestAuthorizeFinish(unittest.TestCase):
    """Task 8で本番のfreeeに対して一度きり実行される経路。
    exchange_code_for_token → merge_token_response → save_credentials_atomic →
    api_call → verify_single_company の連結が壊れていないことを、
    ネットワークに触れる2関数だけモックして確かめる。"""

    @mock.patch("freee_api.api_call")
    @mock.patch("freee_api.exchange_code_for_token")
    def test_success_saves_tokens_and_logs(self, mock_exchange, mock_api_call):
        mock_exchange.return_value = {
            "access_token": "at-new", "refresh_token": "rt-new", "expires_in": 21600,
        }
        mock_api_call.return_value = {"companies": [{"id": 100001}]}
        with tempfile.TemporaryDirectory() as repo_root:
            companies_json = _write_companies_json(
                repo_root, credentials_relpath="creds/creds.json")
            credentials_path = _write_credentials(
                repo_root, "creds/creds.json",
                {"client_id": "cid", "client_secret": "csecret"})
            args = argparse.Namespace(
                companies_json=companies_json,
                repo_root=repo_root,
                company_key="株式会社サンプルA",
                code="auth-code-1",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = cmd_authorize_finish(args)

            with open(credentials_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            log_path = os.path.join(
                repo_root, "サンプルAグループ", "work", "freee-api-post.log")
            with open(log_path, "r", encoding="utf-8") as f:
                log_content = f.read()

        self.assertEqual(exit_code, 0)
        mock_exchange.assert_called_once_with("cid", "csecret", "auth-code-1")
        self.assertEqual(saved["access_token"], "at-new")
        self.assertEqual(saved["refresh_token"], "rt-new")
        self.assertIn("expires_at", saved)
        # client_id/client_secretがマージで消えていないこと
        self.assertEqual(saved["client_id"], "cid")
        self.assertIn("authorize-finish 成功", log_content)

    @mock.patch("freee_api.api_call")
    @mock.patch("freee_api.exchange_code_for_token")
    def test_raises_when_wrong_company_authorized(self, mock_exchange, mock_api_call):
        """事業所選択を間違えた場合の既存の振る舞い（保存→検証→送出）を固定する。
        トークン自体は保存済みになるが、verify_single_companyが弾いて例外になる。"""
        mock_exchange.return_value = {
            "access_token": "at-new", "refresh_token": "rt-new", "expires_in": 21600,
        }
        mock_api_call.return_value = {"companies": [{"id": 99999999}]}
        with tempfile.TemporaryDirectory() as repo_root:
            companies_json = _write_companies_json(
                repo_root, credentials_relpath="creds/creds.json")
            _write_credentials(repo_root, "creds/creds.json",
                               {"client_id": "cid", "client_secret": "csecret"})
            args = argparse.Namespace(
                companies_json=companies_json,
                repo_root=repo_root,
                company_key="株式会社サンプルA",
                code="auth-code-1",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(freee_api.FreeeApiError):
                    cmd_authorize_finish(args)


class TestHealthcheck(unittest.TestCase):
    @mock.patch("freee_api.api_call")
    @mock.patch("freee_api.ensure_access_token")
    def test_success_logs_ok_and_returns_zero(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = {"companies": [{"id": 100001}]}
        with tempfile.TemporaryDirectory() as repo_root:
            companies_json = _write_companies_json(repo_root)
            args = argparse.Namespace(companies_json=companies_json, repo_root=repo_root)
            exit_code = cmd_healthcheck(args)
            log_path = os.path.join(repo_root, "サンプルAグループ", "work", "freee-api-post.log")
            with open(log_path, "r", encoding="utf-8") as f:
                log_content = f.read()
        self.assertEqual(exit_code, 0)
        self.assertIn("healthcheck OK", log_content)

    @mock.patch("freee_api.ensure_access_token")
    def test_failure_logs_ng_and_returns_one(self, mock_ensure):
        mock_ensure.side_effect = freee_api.FreeeApiError("invalid_grant")
        with tempfile.TemporaryDirectory() as repo_root:
            companies_json = _write_companies_json(repo_root)
            args = argparse.Namespace(companies_json=companies_json, repo_root=repo_root)
            exit_code = cmd_healthcheck(args)
            log_path = os.path.join(repo_root, "サンプルAグループ", "work", "freee-api-post.log")
            with open(log_path, "r", encoding="utf-8") as f:
                log_content = f.read()
        self.assertEqual(exit_code, 1)
        self.assertIn("healthcheck NG", log_content)


INTEREST_ACCOUNT = 900001
TAX_ACCOUNT = 900002


def _interest_api_router(deals, journals=(), txns=()):
    """cmd_interest が呼ぶ GET を順不同で捌く簡易ルーター。POST は呼び出しを記録する。"""
    posted = []

    def _call(token, company_id, method, path, json_body=None):
        if method == "POST":
            posted.append((path, json_body))
            return {"manual_journal": {"id": 555}}
        if path.startswith("/api/1/account_items"):
            return {"account_items": [{"id": INTEREST_ACCOUNT, "name": "受取利息"},
                                      {"id": TAX_ACCOUNT, "name": "法人税・住民税及び事業税"}]}
        if path.startswith("/api/1/taxes/codes"):
            return {"taxes": [{"code": 2, "name_ja": "対象外"}, {"code": 23, "name_ja": "非課売上"}]}
        if path.startswith("/api/1/deals"):
            return {"deals": list(deals)}
        if path.startswith("/api/1/manual_journals"):
            return {"manual_journals": list(journals)}
        if path.startswith("/api/1/wallet_txns"):
            return {"wallet_txns": list(txns)}
        if path.startswith("/api/1/items"):
            return {"items": [{"id": 500001, "name": "利息源泉税"}]}
        raise AssertionError("想定外のパス: " + path)

    return _call, posted


def _net_interest_deal():
    """受取利息1行だけの取引（自動登録ルール act=1 が作る形）。"""
    return {"id": 100, "issue_date": "2026-09-01", "type": "income", "partner_id": 7,
            "details": [{"account_item_id": INTEREST_ACCOUNT, "entry_side": "credit",
                         "amount": 39, "tax_code": 23, "partner_id": 7, "description": ""}],
            "payments": [{"from_walletable_type": "bank_account", "from_walletable_id": 4000001,
                          "amount": 39}]}


class TestInterest(unittest.TestCase):
    @mock.patch("freee_api.api_call")
    @mock.patch("freee_api.ensure_access_token")
    def test_registers_withholding_journal(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        call, posted = _interest_api_router([_net_interest_deal()])
        mock_api_call.side_effect = call
        with tempfile.TemporaryDirectory() as repo_root:
            companies_json = _write_companies_json(repo_root)
            args = argparse.Namespace(companies_json=companies_json, repo_root=repo_root,
                                      dry_run=False, days=60)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                exit_code = cmd_interest(args)
            log_path = os.path.join(repo_root, "サンプルAグループ", "work", "freee-api-post.log")
            with open(log_path, "r", encoding="utf-8") as f:
                log_content = f.read()
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(posted), 1)
        path, body = posted[0]
        self.assertEqual(path, "/api/1/manual_journals")
        self.assertEqual(body["issue_date"], "2026-09-01")
        debit, credit = body["details"]
        self.assertEqual((debit["account_item_id"], debit["amount"], debit["tax_code"]),
                         (TAX_ACCOUNT, 7, 2))
        self.assertEqual((credit["account_item_id"], credit["amount"], credit["tax_code"]),
                         (INTEREST_ACCOUNT, 7, 23))
        self.assertIn("総額46円", debit["description"])
        self.assertIn("interest 起票", log_content)
        report = json.loads(out.getvalue().strip())
        self.assertIn("伝票id 555", report["text"])

    @mock.patch("freee_api.api_call")
    @mock.patch("freee_api.ensure_access_token")
    def test_dry_run_does_not_post(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        call, posted = _interest_api_router([_net_interest_deal()])
        mock_api_call.side_effect = call
        with tempfile.TemporaryDirectory() as repo_root:
            companies_json = _write_companies_json(repo_root)
            args = argparse.Namespace(companies_json=companies_json, repo_root=repo_root,
                                      dry_run=True, days=60)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                exit_code = cmd_interest(args)
            self.assertFalse(os.path.exists(os.path.join(repo_root, "サンプルAグループ", "work",
                                                         "freee-api-post.log")))
        self.assertEqual(exit_code, 0)
        self.assertEqual(posted, [])

    @mock.patch("freee_api.api_call")
    @mock.patch("freee_api.ensure_access_token")
    def test_skips_gross_type_account(self, mock_ensure, mock_api_call):
        """同日・同口座に「国税」の出金明細がある口座（総額入金型）には起票しない。"""
        mock_ensure.return_value = "token-1"
        txns = [{"id": 1, "date": "2026-09-01", "walletable_id": 4000001, "amount": 7,
                 "description": "国税", "entry_side": "expense"}]
        call, posted = _interest_api_router([_net_interest_deal()], txns=txns)
        mock_api_call.side_effect = call
        with tempfile.TemporaryDirectory() as repo_root:
            companies_json = _write_companies_json(repo_root)
            args = argparse.Namespace(companies_json=companies_json, repo_root=repo_root,
                                      dry_run=False, days=60)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                exit_code = cmd_interest(args)
        self.assertEqual(exit_code, 0)
        self.assertEqual(posted, [])
        self.assertEqual(out.getvalue().strip(), "")

    @mock.patch("freee_api.api_call")
    @mock.patch("freee_api.ensure_access_token")
    def test_does_not_repost_when_journal_exists(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        note = "普通預金利息 源泉所得税（総額46円・入金39円・対象取引100）"
        journals = [{"id": 900, "issue_date": "2026-09-01", "details": [
            {"account_item_id": TAX_ACCOUNT, "entry_side": "debit", "amount": 7, "description": note},
            {"account_item_id": INTEREST_ACCOUNT, "entry_side": "credit", "amount": 7,
             "description": note}]}]
        call, posted = _interest_api_router([_net_interest_deal()], journals=journals)
        mock_api_call.side_effect = call
        with tempfile.TemporaryDirectory() as repo_root:
            companies_json = _write_companies_json(repo_root)
            args = argparse.Namespace(companies_json=companies_json, repo_root=repo_root,
                                      dry_run=False, days=60)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                exit_code = cmd_interest(args)
        self.assertEqual(exit_code, 0)
        self.assertEqual(posted, [])

    @mock.patch("freee_api.api_call")
    @mock.patch("freee_api.ensure_access_token")
    def test_skips_company_with_auto_post_false(self, mock_ensure, mock_api_call):
        """auto_post: false の会社には一切書き込まない（APIも呼ばない）。"""
        mock_ensure.return_value = "token-1"
        call, posted = _interest_api_router([_net_interest_deal()])
        mock_api_call.side_effect = call
        with tempfile.TemporaryDirectory() as repo_root:
            companies_json = os.path.join(repo_root, "companies.json")
            with open(companies_json, "w", encoding="utf-8") as f:
                json.dump({"companies": [{"name": "株式会社サンプルA", "company_id": 100001,
                                          "instance": "サンプルAグループ", "credentials_path": "creds.json",
                                          "slack_channel": "CH-SAMPLE-D", "auto_post": False}]},
                          f, ensure_ascii=False)
            args = argparse.Namespace(companies_json=companies_json, repo_root=repo_root,
                                      dry_run=False, days=60)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                exit_code = cmd_interest(args)
        self.assertEqual(exit_code, 0)
        self.assertEqual(posted, [])
        self.assertEqual(mock_api_call.call_count, 0)
        self.assertEqual(out.getvalue().strip(), "")

    @mock.patch("freee_api.ensure_access_token")
    def test_failure_returns_one_and_logs(self, mock_ensure):
        mock_ensure.side_effect = freee_api.FreeeApiError("invalid_grant")
        with tempfile.TemporaryDirectory() as repo_root:
            companies_json = _write_companies_json(repo_root)
            args = argparse.Namespace(companies_json=companies_json, repo_root=repo_root,
                                      dry_run=False, days=60)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                exit_code = cmd_interest(args)
            log_path = os.path.join(repo_root, "サンプルAグループ", "work", "freee-api-post.log")
            with open(log_path, "r", encoding="utf-8") as f:
                log_content = f.read()
        self.assertEqual(exit_code, 1)
        self.assertIn("interest NG", log_content)


if __name__ == "__main__":
    unittest.main()
