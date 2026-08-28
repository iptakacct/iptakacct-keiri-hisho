"""invox（受取請求書）API連携の共通ロジック（トークン管理・API呼び出し）。
invoxは1アカウントでグループ各社を部門で区別するため、companies.jsonではなく
accounts.json（invoxアカウント単位）を前提にする。"""
from __future__ import annotations
import base64
import json
import os
import tempfile
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone

AUTHORIZE_ENDPOINT = "https://api.invox.jp/oauth2/authorize/"
TOKEN_ENDPOINT = "https://api.invox.jp/oauth2/token/"
API_BASE = "https://api.invox.jp/api/public/"
# invoxはlocalhostのような受け皿を使わず、ログイン画面自体をリダイレクト先にする
# （2026-08-28発行済みクライアントの実際の設定値）。
REDIRECT_URI = "https://web.invox.jp"
# 2026-08-28時点で確認できた実際のスコープ例。公式ドキュメントの一般説明では
# "read write" とも記載されており、実機で確定させる（Global Constraints参照）。
SCOPE = "read write"  # 2026-08-28: receive_invoice_createでは一覧取得が403だったため公式ドキュメント記載の値に変更
REFRESH_BUFFER_MINUTES = 10
# ネットワーク不調（DNS障害・接続拒否等）でurlopenが無期限にハングしないようにする上限。
REQUEST_TIMEOUT_SECONDS = 30


class InvoxApiError(Exception):
    """invox APIとの通信・認証で発生したエラー。"""


def load_credentials(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_credentials_atomic(path: str, data: dict) -> None:
    """認証情報を一時ファイルに書いてからリネームすることで、
    書き込み途中のクラッシュで内容が壊れる（refresh_tokenを失う）ことを防ぐ。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".invox_creds_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def token_needs_refresh(expires_at_iso: str, now: datetime | None = None) -> bool:
    """access_tokenの残り有効時間がREFRESH_BUFFER_MINUTES未満ならTrue。"""
    now = now or datetime.now(timezone.utc)
    expires_at = datetime.fromisoformat(expires_at_iso)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return (expires_at - now) < timedelta(minutes=REFRESH_BUFFER_MINUTES)


def build_authorize_url(client_id: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
    }
    return f"{AUTHORIZE_ENDPOINT}?{urllib.parse.urlencode(params)}"


def basic_auth_header(client_id: str, client_secret: str) -> str:
    """トークンエンドポイントのクライアント認証（HTTP Basic、第一候補）用ヘッダー値を作る。
    freee・マネーフォワードともBasic認証だった前例を踏襲するが、invoxでの実際の方式は
    Task 3の実機テストで確認する（Global Constraints参照）。"""
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _open_json(req: urllib.request.Request) -> dict:
    """リクエストを送信し、JSONレスポンスをdictとして返す共通処理。
    HTTPError・URLError（DNS障害・接続拒否・タイムアウト等）・不正なJSON応答は
    すべてInvoxApiErrorにラップして呼び出し元に伝える。"""
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        raise InvoxApiError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise InvoxApiError(f"{req.full_url} への接続に失敗しました: {e}") from e
    except json.JSONDecodeError as e:
        raise InvoxApiError(f"{req.full_url} の応答をJSONとして解釈できませんでした: {e}") from e


def _post_form(url: str, data: dict, auth_header: str) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Authorization", auth_header)
    return _open_json(req)


def exchange_code_for_token(client_id: str, client_secret: str, code: str) -> dict:
    return _post_form(
        TOKEN_ENDPOINT,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        basic_auth_header(client_id, client_secret),
    )


def refresh_token_pair(client_id: str, client_secret: str, refresh_token: str) -> dict:
    return _post_form(
        TOKEN_ENDPOINT,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        basic_auth_header(client_id, client_secret),
    )


def merge_token_response(existing: dict, token_response: dict) -> dict:
    """トークンエンドポイントの応答を、既存の認証情報（client_id等）に上書きマージする。
    refresh_tokenが応答に含まれない場合は既存の値を維持する。"""
    now = datetime.now(timezone.utc)
    expires_in = int(token_response["expires_in"])
    expires_at = now + timedelta(seconds=expires_in)
    merged = dict(existing)
    merged["access_token"] = token_response["access_token"]
    merged["refresh_token"] = token_response.get("refresh_token", existing.get("refresh_token"))
    merged["expires_at"] = expires_at.isoformat()
    return merged


def ensure_access_token(credentials_path: str) -> str:
    """必要ならrefresh_tokenでトークンを更新し、有効なaccess_tokenを返す。
    更新した場合はファイルにアトミックに保存する。"""
    creds = load_credentials(credentials_path)
    if token_needs_refresh(creds["expires_at"]):
        token_response = refresh_token_pair(
            creds["client_id"], creds["client_secret"], creds["refresh_token"]
        )
        creds = merge_token_response(creds, token_response)
        save_credentials_atomic(credentials_path, creds)
    return creds["access_token"]


def api_call(access_token: str, method: str, path: str,
             params: dict | None = None, json_body: dict | None = None) -> dict:
    """API_BASE配下のパスを呼ぶ汎用ラッパー。GETはparamsをクエリ文字列に、
    POSTはjson_bodyをJSONボディにする。"""
    url = f"{API_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {access_token}")
    if data is not None:
        req.add_header("Content-type", "application/json")
    return _open_json(req)
