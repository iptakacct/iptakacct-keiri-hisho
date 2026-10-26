import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
import urllib.error

import mf_api
from mf_api import (
    load_credentials,
    save_credentials_atomic,
    token_needs_refresh,
    build_authorize_url,
    basic_auth_header,
    exchange_code_for_token,
    ensure_access_token,
    verify_single_office,
    api_call,
    merge_token_response,
    MfApiError,
    token_lock_name,
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
        now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
        expires_at = (now - timedelta(minutes=1)).isoformat()
        self.assertTrue(token_needs_refresh(expires_at, now=now))

    def test_returns_true_when_within_buffer(self):
        now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
        expires_at = (now + timedelta(minutes=5)).isoformat()
        self.assertTrue(token_needs_refresh(expires_at, now=now))

    def test_returns_false_when_well_before_expiry(self):
        now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
        expires_at = (now + timedelta(minutes=45)).isoformat()
        self.assertFalse(token_needs_refresh(expires_at, now=now))


class TestBuildAuthorizeUrl(unittest.TestCase):
    def test_includes_required_params(self):
        url = build_authorize_url("client-123")
        self.assertIn("client_id=client-123", url)
        self.assertIn("redirect_uri=http%3A%2F%2Flocalhost%3A8765%2Fcallback", url)
        self.assertIn("response_type=code", url)
        self.assertIn("scope=mfc%2Faccounting%2Foffices.read", url)


class TestBasicAuthHeader(unittest.TestCase):
    def test_encodes_client_id_and_secret(self):
        header = basic_auth_header("myid", "mysecret")
        self.assertTrue(header.startswith("Basic "))
        import base64
        decoded = base64.b64decode(header[len("Basic "):]).decode("utf-8")
        self.assertEqual(decoded, "myid:mysecret")


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
    @mock.patch("mf_api.urllib.request.urlopen")
    def test_returns_parsed_token_response(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({
            "access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600
        })
        result = exchange_code_for_token("cid", "csecret", "code-1")
        self.assertEqual(result["access_token"], "at-1")

    @mock.patch("mf_api.urllib.request.urlopen")
    def test_sends_basic_auth_header(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({
            "access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600
        })
        exchange_code_for_token("cid", "csecret", "code-1")
        sent_request = mock_urlopen.call_args[0][0]
        self.assertTrue(sent_request.get_header("Authorization").startswith("Basic "))

    @mock.patch("mf_api.urllib.request.urlopen")
    def test_タイムアウトを付ける(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({
            "access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600
        })
        exchange_code_for_token("cid", "csecret", "code-1")
        self.assertEqual(mock_urlopen.call_args.kwargs.get("timeout"), 60)

    @mock.patch("mf_api.urllib.request.urlopen")
    def test_raises_on_http_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="x", code=400, msg="bad", hdrs=None,
            fp=mock.Mock(read=lambda: b'{"error":"invalid_grant"}')
        )
        with self.assertRaises(MfApiError):
            exchange_code_for_token("cid", "csecret", "bad-code")


class TestMergeTokenResponse(unittest.TestCase):
    def test_uses_new_refresh_token_when_present(self):
        existing = {"client_id": "cid", "refresh_token": "rt-old"}
        merged = merge_token_response(existing, {
            "access_token": "at-new", "refresh_token": "rt-new", "expires_in": 3600
        })
        self.assertEqual(merged["refresh_token"], "rt-new")

    def test_keeps_existing_refresh_token_when_response_omits_it(self):
        existing = {"client_id": "cid", "refresh_token": "rt-old"}
        merged = merge_token_response(existing, {
            "access_token": "at-new", "expires_in": 3600
        })
        self.assertEqual(merged["refresh_token"], "rt-old")


class TestEnsureAccessToken(unittest.TestCase):
    def setUp(self):
        self._lock_base = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"RUN_LOCK_DIR_BASE": self._lock_base.name})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._lock_base.cleanup()

    @mock.patch("mf_api.refresh_token_pair")
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

    @mock.patch("mf_api.refresh_token_pair")
    def test_refreshes_and_saves_when_expired(self, mock_refresh):
        mock_refresh.return_value = {
            "access_token": "at-new", "refresh_token": "rt-new", "expires_in": 3600,
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

    def _write(self, path, **kw):
        base = {"client_id": "cid", "client_secret": "csecret",
                "access_token": "at-old", "refresh_token": "rt-old",
                "expires_at": "2000-01-01T00:00:00+00:00"}
        base.update(kw)
        save_credentials_atomic(path, base)

    @mock.patch("mf_api.refresh_token_pair")
    def test_取り合いに負けたら勝った側のトークンに乗り換える(self, mock_refresh):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "creds.json")
            self._write(path)

            def lose(cid, secret, rt):
                # 別プロセスが先に更新してファイルを書き換えた状態を作る
                self._write(path, access_token="at-winner", refresh_token="rt-winner",
                            expires_at="2999-01-01T00:00:00+00:00")
                raise MfApiError('HTTP 400: {"error":"invalid_grant"}')

            mock_refresh.side_effect = lose
            token = ensure_access_token(path)
        self.assertEqual(token, "at-winner")
        mock_refresh.assert_called_once()

    @mock.patch("mf_api.refresh_token_pair")
    def test_勝った側も期限が近ければその値で1回だけやり直す(self, mock_refresh):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "creds.json")
            self._write(path)
            calls = []

            def refresh(cid, secret, rt):
                calls.append(rt)
                if rt == "rt-old":
                    self._write(path, access_token="at-winner", refresh_token="rt-winner",
                                expires_at="2000-01-01T00:00:00+00:00")
                    raise MfApiError('HTTP 400: {"error":"invalid_grant"}')
                return {"access_token": "at-new", "refresh_token": "rt-new", "expires_in": 3600}

            mock_refresh.side_effect = refresh
            token = ensure_access_token(path)
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
        self.assertEqual(calls, ["rt-old", "rt-winner"])
        self.assertEqual(token, "at-new")
        self.assertEqual(saved["refresh_token"], "rt-new")

    @mock.patch("mf_api.refresh_token_pair")
    def test_誰も更新していなければ本当の失効として投げる(self, mock_refresh):
        mock_refresh.side_effect = MfApiError('HTTP 400: {"error":"invalid_grant"}')
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "creds.json")
            self._write(path)
            with self.assertRaises(MfApiError):
                ensure_access_token(path)

    @mock.patch("mf_api.refresh_token_pair")
    def test_ロックを待つ間に先客が更新していれば更新しない(self, mock_refresh):
        import mf_lock
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "creds.json")
            self._write(path)
            real_lock = mf_lock.company_lock

            def lock_then_winner_saves(name, **kw):
                # ロックを取るまでの間に、先客が更新を終えてファイルを書き換えた
                self._write(path, access_token="at-winner", refresh_token="rt-winner",
                            expires_at="2999-01-01T00:00:00+00:00")
                return real_lock(name, **kw)

            with mock.patch("mf_api.mf_lock.company_lock", side_effect=lock_then_winner_saves):
                token = ensure_access_token(path)
        self.assertEqual(token, "at-winner")
        mock_refresh.assert_not_called()

    def test_ロック名は認証情報ファイルのパスで決まる(self):
        a = token_lock_name("C:/x/credentials_mf_A.json")
        self.assertTrue(a.startswith("mf-token-"))
        self.assertEqual(a, token_lock_name("C:/x/credentials_mf_A.json"))
        self.assertNotEqual(a, token_lock_name("C:/x/credentials_mf_B.json"))


class TestVerifySingleOffice(unittest.TestCase):
    # 実機確認：GET /offices は接続中の1事業者をオブジェクトで返す（code＝事業者コード）
    def test_passes_when_code_matches(self):
        verify_single_office({"code": "1000-0001", "name": "株式会社サンプルA"}, "1000-0001")

    def test_raises_when_code_differs(self):
        with self.assertRaises(MfApiError):
            verify_single_office({"code": "9999-9999", "name": "別の会社"}, "1000-0001")

    def test_raises_when_code_missing(self):
        with self.assertRaises(MfApiError):
            verify_single_office({"offices": [{"id": "1000-0001"}]}, "1000-0001")


class TestApiCall(unittest.TestCase):
    @mock.patch("mf_api.urllib.request.urlopen")
    def test_sends_bearer_auth_header(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({"offices": [{"id": "1000-0001"}]})
        result = api_call("token-1", "GET", "/offices")
        self.assertEqual(result["offices"][0]["id"], "1000-0001")
        sent_request = mock_urlopen.call_args[0][0]
        self.assertEqual(sent_request.get_header("Authorization"), "Bearer token-1")

    @mock.patch("mf_api.urllib.request.urlopen")
    def test_タイムアウトを付ける(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({"code": "1000-0001"})
        api_call("token-1", "GET", "/offices")
        self.assertEqual(mock_urlopen.call_args.kwargs.get("timeout"), 60)

    def test_GET以外は受け付けない(self):
        for method in ("POST", "PUT", "DELETE"):
            with self.assertRaises(MfApiError):
                api_call("token-1", method, "/journals", {"journal": {}})


    @mock.patch("mf_api.urllib.request.urlopen")
    def test_429は間隔を空けて再試行する(self, mock_urlopen):
        err = urllib.error.HTTPError("u", 429, "Too Many Requests", None, io.BytesIO(b"limit"))
        mock_urlopen.side_effect = [err, _fake_response({"ok": True})]
        slept = []
        result = mf_api._request("t", "/journals", sleep=slept.append)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(slept, [1.5])

    @mock.patch("mf_api.urllib.request.urlopen")
    def test_5xxも再試行する(self, mock_urlopen):
        err = urllib.error.HTTPError("u", 503, "Service Unavailable", None, io.BytesIO(b"busy"))
        mock_urlopen.side_effect = [err, err, _fake_response({"ok": True})]
        slept = []
        result = mf_api._request("t", "/journals", sleep=slept.append)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(slept, [1.5, 3.0])

    @mock.patch("mf_api.urllib.request.urlopen")
    def test_再試行を使い切ったらMfApiError(self, mock_urlopen):
        err = urllib.error.HTTPError("u", 429, "Too Many Requests", None, io.BytesIO(b"limit"))
        mock_urlopen.side_effect = [err, err, err, err]
        with self.assertRaises(MfApiError) as cm:
            mf_api._request("t", "/journals", sleep=lambda s: None)
        self.assertIn("429", str(cm.exception))

    @mock.patch("mf_api.urllib.request.urlopen")
    def test_400は再試行しない(self, mock_urlopen):
        err = urllib.error.HTTPError("u", 400, "Bad Request", None, io.BytesIO(b"ng"))
        mock_urlopen.side_effect = [err, _fake_response({"ok": True})]
        with self.assertRaises(MfApiError):
            mf_api._request("t", "/journals", sleep=lambda s: None)
        self.assertEqual(mock_urlopen.call_count, 1)



if __name__ == "__main__":
    unittest.main()
