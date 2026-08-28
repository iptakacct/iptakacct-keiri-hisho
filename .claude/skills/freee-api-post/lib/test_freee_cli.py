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


if __name__ == "__main__":
    unittest.main()
