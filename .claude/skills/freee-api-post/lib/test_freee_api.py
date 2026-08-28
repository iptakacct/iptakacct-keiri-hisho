import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from unittest import mock

from freee_api import (
    load_credentials,
    save_credentials_atomic,
    token_needs_refresh,
    build_authorize_url,
    exchange_code_for_token,
    refresh_token_pair,
    ensure_access_token,
    verify_single_company,
    api_call,
    FreeeApiError,
    REDIRECT_URI,
)


class TestCredentialsFile(unittest.TestCase):
    def test_load_credentials_reads_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "creds.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"client_id": "abc"}, f)
            result = load_credentials(path)
        self.assertEqual(result, {"client_id": "abc"})

    def test_save_credentials_atomic_writes_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "creds.json")
            save_credentials_atomic(path, {"client_id": "abc"})
            with open(path, "r", encoding="utf-8") as f:
                result = json.load(f)
        self.assertEqual(result, {"client_id": "abc"})

    def test_save_credentials_atomic_leaves_no_tmp_file_on_success(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "creds.json")
            save_credentials_atomic(path, {"client_id": "abc"})
            remaining = os.listdir(d)
        self.assertEqual(remaining, ["creds.json"])

    def test_save_credentials_atomic_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "creds.json")
            save_credentials_atomic(path, {"client_id": "old"})
            save_credentials_atomic(path, {"client_id": "new"})
            with open(path, "r", encoding="utf-8") as f:
                result = json.load(f)
        self.assertEqual(result, {"client_id": "new"})


class TestTokenNeedsRefresh(unittest.TestCase):
    def test_returns_true_when_already_expired(self):
        now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
        expires_at = (now - timedelta(minutes=1)).isoformat()
        self.assertTrue(token_needs_refresh(expires_at, now=now))

    def test_returns_true_when_within_buffer(self):
        now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
        expires_at = (now + timedelta(minutes=10)).isoformat()
        self.assertTrue(token_needs_refresh(expires_at, now=now))

    def test_returns_false_when_well_before_expiry(self):
        now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
        expires_at = (now + timedelta(hours=5)).isoformat()
        self.assertFalse(token_needs_refresh(expires_at, now=now))


class TestBuildAuthorizeUrl(unittest.TestCase):
    def test_includes_required_params(self):
        url = build_authorize_url("client-123")
        self.assertIn("client_id=client-123", url)
        self.assertIn("redirect_uri=urn%3Aietf%3Awg%3Aoauth%3A2.0%3Aoob", url)
        self.assertIn("response_type=code", url)
        self.assertIn("prompt=select_company", url)


def _fake_response(payload):
    body = json.dumps(payload).encode("utf-8")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return body

    return FakeResponse()


class TestExchangeCodeForToken(unittest.TestCase):
    @mock.patch("freee_api.urllib.request.urlopen")
    def test_returns_parsed_token_response(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({
            "access_token": "at-1", "refresh_token": "rt-1", "expires_in": 21600
        })
        result = exchange_code_for_token("cid", "csecret", "code-1")
        self.assertEqual(result["access_token"], "at-1")

    @mock.patch("freee_api.urllib.request.urlopen")
    def test_raises_on_http_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="x", code=400, msg="bad", hdrs=None,
            fp=mock.Mock(read=lambda: b'{"error":"invalid_grant"}')
        )
        with self.assertRaises(FreeeApiError):
            exchange_code_for_token("cid", "csecret", "bad-code")

    @mock.patch("freee_api.urllib.request.urlopen")
    def test_sends_correct_form_body(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({
            "access_token": "at-1", "refresh_token": "rt-1", "expires_in": 21600
        })
        exchange_code_for_token("cid", "csecret", "code-1")

        sent_request = mock_urlopen.call_args[0][0]
        body_data = urllib.parse.parse_qsl(sent_request.data.decode("utf-8"))
        body_dict = dict(body_data)

        self.assertEqual(body_dict["grant_type"], "authorization_code")
        self.assertEqual(body_dict["client_id"], "cid")
        self.assertEqual(body_dict["client_secret"], "csecret")
        self.assertEqual(body_dict["code"], "code-1")
        self.assertEqual(body_dict["redirect_uri"], REDIRECT_URI)


class TestRefreshTokenPair(unittest.TestCase):
    @mock.patch("freee_api.urllib.request.urlopen")
    def test_returns_parsed_token_response(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({
            "access_token": "at-new", "refresh_token": "rt-new", "expires_in": 21600
        })
        result = refresh_token_pair("cid", "csecret", "rt-1")
        self.assertEqual(result["access_token"], "at-new")

    @mock.patch("freee_api.urllib.request.urlopen")
    def test_sends_correct_form_body(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({
            "access_token": "at-new", "refresh_token": "rt-new", "expires_in": 21600
        })
        refresh_token_pair("cid", "csecret", "rt-1")

        sent_request = mock_urlopen.call_args[0][0]
        body_data = urllib.parse.parse_qsl(sent_request.data.decode("utf-8"))
        body_dict = dict(body_data)

        self.assertEqual(body_dict["grant_type"], "refresh_token")
        self.assertEqual(body_dict["client_id"], "cid")
        self.assertEqual(body_dict["client_secret"], "csecret")
        self.assertEqual(body_dict["refresh_token"], "rt-1")


class TestEnsureAccessToken(unittest.TestCase):
    @mock.patch("freee_api.refresh_token_pair")
    def test_returns_existing_token_when_not_expired(self, mock_refresh):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "creds.json")
            future = "2999-01-01T00:00:00+00:00"
            save_credentials_atomic(path, {
                "client_id": "cid", "client_secret": "csecret",
                "access_token": "at-old", "refresh_token": "rt-old",
                "expires_at": future,
            })
            token = ensure_access_token(path)
        self.assertEqual(token, "at-old")
        mock_refresh.assert_not_called()

    @mock.patch("freee_api.refresh_token_pair")
    def test_refreshes_and_saves_when_expired(self, mock_refresh):
        mock_refresh.return_value = {
            "access_token": "at-new", "refresh_token": "rt-new", "expires_in": 21600,
        }
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "creds.json")
            past = "2000-01-01T00:00:00+00:00"
            save_credentials_atomic(path, {
                "client_id": "cid", "client_secret": "csecret",
                "access_token": "at-old", "refresh_token": "rt-old",
                "expires_at": past,
            })
            token = ensure_access_token(path)
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
        self.assertEqual(token, "at-new")
        self.assertEqual(saved["refresh_token"], "rt-new")
        mock_refresh.assert_called_once_with("cid", "csecret", "rt-old")


class TestVerifySingleCompany(unittest.TestCase):
    def test_passes_when_only_expected_company_present(self):
        verify_single_company([{"id": 100001, "name": "株式会社サンプルA"}], 100001)

    def test_raises_when_other_company_present(self):
        with self.assertRaises(FreeeApiError):
            verify_single_company([{"id": 100001}, {"id": 999}], 100001)

    def test_raises_when_expected_company_missing(self):
        with self.assertRaises(FreeeApiError):
            verify_single_company([{"id": 999}], 100001)


class TestApiCall(unittest.TestCase):
    @mock.patch("freee_api.urllib.request.urlopen")
    def test_appends_company_id_and_auth_header(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({"companies": [{"id": 100001}]})
        result = api_call("token-1", 100001, "GET", "/api/1/companies")
        self.assertEqual(result["companies"][0]["id"], 100001)
        sent_request = mock_urlopen.call_args[0][0]
        self.assertIn("company_id=100001", sent_request.full_url)
        self.assertEqual(sent_request.get_header("Authorization"), "Bearer token-1")


if __name__ == "__main__":
    unittest.main()
