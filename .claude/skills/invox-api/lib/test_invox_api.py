import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from invox_api import (
    load_credentials,
    save_credentials_atomic,
    token_needs_refresh,
    build_authorize_url,
    basic_auth_header,
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
        now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        expires_at = (now - timedelta(minutes=1)).isoformat()
        self.assertTrue(token_needs_refresh(expires_at, now=now))

    def test_returns_true_when_within_buffer(self):
        now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        expires_at = (now + timedelta(minutes=5)).isoformat()
        self.assertTrue(token_needs_refresh(expires_at, now=now))

    def test_returns_false_when_well_before_expiry(self):
        # アクセストークン有効期限は10時間（36000秒）なので、9時間後でも余裕あり
        now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        expires_at = (now + timedelta(hours=9)).isoformat()
        self.assertFalse(token_needs_refresh(expires_at, now=now))


class TestBuildAuthorizeUrl(unittest.TestCase):
    def test_includes_required_params(self):
        url = build_authorize_url("client-123")
        self.assertIn("client_id=client-123", url)
        self.assertIn("redirect_uri=https%3A%2F%2Fweb.invox.jp", url)
        self.assertIn("response_type=code", url)


class TestBasicAuthHeader(unittest.TestCase):
    def test_encodes_client_id_and_secret(self):
        header = basic_auth_header("myid", "mysecret")
        self.assertTrue(header.startswith("Basic "))
        import base64
        decoded = base64.b64decode(header[len("Basic "):]).decode("utf-8")
        self.assertEqual(decoded, "myid:mysecret")


from unittest import mock
import urllib.error
from invox_api import (
    exchange_code_for_token,
    ensure_access_token,
    api_call,
    merge_token_response,
    InvoxApiError,
    REQUEST_TIMEOUT_SECONDS,
)


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
    @mock.patch("invox_api.urllib.request.urlopen")
    def test_returns_parsed_token_response(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({
            "access_token": "at-1", "refresh_token": "rt-1", "expires_in": 36000
        })
        result = exchange_code_for_token("cid", "csecret", "code-1")
        self.assertEqual(result["access_token"], "at-1")

    @mock.patch("invox_api.urllib.request.urlopen")
    def test_sends_basic_auth_header(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({
            "access_token": "at-1", "refresh_token": "rt-1", "expires_in": 36000
        })
        exchange_code_for_token("cid", "csecret", "code-1")
        sent_request = mock_urlopen.call_args[0][0]
        self.assertTrue(sent_request.get_header("Authorization").startswith("Basic "))

    @mock.patch("invox_api.urllib.request.urlopen")
    def test_raises_on_http_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="x", code=400, msg="bad", hdrs=None,
            fp=mock.Mock(read=lambda: b'{"error":"invalid_grant"}')
        )
        with self.assertRaises(InvoxApiError):
            exchange_code_for_token("cid", "csecret", "bad-code")

    @mock.patch("invox_api.urllib.request.urlopen")
    def test_raises_invoxapierror_on_url_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        with self.assertRaises(InvoxApiError):
            exchange_code_for_token("cid", "csecret", "code-1")

    @mock.patch("invox_api.urllib.request.urlopen")
    def test_sends_request_timeout(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({
            "access_token": "at-1", "refresh_token": "rt-1", "expires_in": 36000
        })
        exchange_code_for_token("cid", "csecret", "code-1")
        self.assertEqual(mock_urlopen.call_args.kwargs.get("timeout"), REQUEST_TIMEOUT_SECONDS)


class TestMergeTokenResponse(unittest.TestCase):
    def test_uses_new_refresh_token_when_present(self):
        existing = {"client_id": "cid", "refresh_token": "rt-old"}
        merged = merge_token_response(existing, {
            "access_token": "at-new", "refresh_token": "rt-new", "expires_in": 36000
        })
        self.assertEqual(merged["refresh_token"], "rt-new")

    def test_keeps_existing_refresh_token_when_response_omits_it(self):
        existing = {"client_id": "cid", "refresh_token": "rt-old"}
        merged = merge_token_response(existing, {
            "access_token": "at-new", "expires_in": 36000
        })
        self.assertEqual(merged["refresh_token"], "rt-old")


class TestEnsureAccessToken(unittest.TestCase):
    @mock.patch("invox_api.refresh_token_pair")
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

    @mock.patch("invox_api.refresh_token_pair")
    def test_refreshes_and_saves_when_expired(self, mock_refresh):
        mock_refresh.return_value = {
            "access_token": "at-new", "refresh_token": "rt-new", "expires_in": 36000,
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


class TestApiCall(unittest.TestCase):
    @mock.patch("invox_api.urllib.request.urlopen")
    def test_sends_bearer_auth_header(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({"count": 1, "results": [{"invoice_id": "inv-1"}]})
        result = api_call("token-1", "GET", "invoice_receive/invoice/list", params={"invox_company_code": "cc-1"})
        self.assertEqual(result["results"][0]["invoice_id"], "inv-1")
        sent_request = mock_urlopen.call_args[0][0]
        self.assertEqual(sent_request.get_header("Authorization"), "Bearer token-1")

    @mock.patch("invox_api.urllib.request.urlopen")
    def test_get_with_params_builds_query_string(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({"results": []})
        api_call("token-1", "GET", "invoice_receive/invoice/list",
                  params={"invox_company_code": "cc-1", "department_code": "PMO"})
        sent_request = mock_urlopen.call_args[0][0]
        self.assertIn("invox_company_code=cc-1", sent_request.full_url)
        self.assertIn("department_code=PMO", sent_request.full_url)

    @mock.patch("invox_api.urllib.request.urlopen")
    def test_post_sends_json_body(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({})
        api_call("token-1", "POST", "invoice_receive/invoice/approve",
                  json_body={"invox_company_code": "cc-1", "invoice_id": "inv-1"})
        sent_request = mock_urlopen.call_args[0][0]
        self.assertEqual(sent_request.get_header("Content-type"), "application/json")
        sent_body = json.loads(sent_request.data.decode("utf-8"))
        self.assertEqual(sent_body["invoice_id"], "inv-1")

    @mock.patch("invox_api.urllib.request.urlopen")
    def test_raises_invoxapierror_on_url_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        with self.assertRaises(InvoxApiError):
            api_call("token-1", "GET", "invoice_receive/invoice/list",
                      params={"invox_company_code": "cc-1"})

    @mock.patch("invox_api.urllib.request.urlopen")
    def test_sends_request_timeout(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({"results": []})
        api_call("token-1", "GET", "invoice_receive/invoice/list",
                  params={"invox_company_code": "cc-1"})
        self.assertEqual(mock_urlopen.call_args.kwargs.get("timeout"), REQUEST_TIMEOUT_SECONDS)


if __name__ == "__main__":
    unittest.main()
