"""freee API連携の共通ロジック（トークン管理・API呼び出し）。
複数社対応を前提に、認証情報ファイルのパスを都度引数で受け取る。"""
from __future__ import annotations
import json
import os
import tempfile
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone

TOKEN_ENDPOINT = "https://accounts.secure.freee.co.jp/public_api/token"
AUTHORIZE_ENDPOINT = "https://accounts.secure.freee.co.jp/public_api/authorize"
API_BASE = "https://api.freee.co.jp"
REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"
REFRESH_BUFFER_MINUTES = 30


class FreeeApiError(Exception):
    """freee APIとの通信・認証で発生したエラー。"""


def load_credentials(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_credentials_atomic(path: str, data: dict) -> None:
    """認証情報を一時ファイルに書いてからリネームすることで、
    書き込み途中のクラッシュで内容が壊れる（refresh_tokenを失う）ことを防ぐ。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".freee_creds_", suffix=".tmp")
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
        "prompt": "select_company",
    }
    return f"{AUTHORIZE_ENDPOINT}?{urllib.parse.urlencode(params)}"


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise FreeeApiError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')}") from e


def exchange_code_for_token(client_id: str, client_secret: str, code: str) -> dict:
    return _post_form(TOKEN_ENDPOINT, {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": REDIRECT_URI,
    })


def refresh_token_pair(client_id: str, client_secret: str, refresh_token: str) -> dict:
    return _post_form(TOKEN_ENDPOINT, {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    })


def merge_token_response(existing: dict, token_response: dict) -> dict:
    """トークンエンドポイントの応答を、既存の認証情報（client_id等）に上書きマージする。"""
    now = datetime.now(timezone.utc)
    expires_in = int(token_response["expires_in"])
    expires_at = now + timedelta(seconds=expires_in)
    merged = dict(existing)
    merged["access_token"] = token_response["access_token"]
    merged["refresh_token"] = token_response["refresh_token"]
    merged["expires_at"] = expires_at.isoformat()
    return merged


def ensure_access_token(credentials_path: str) -> str:
    """必要ならrefresh_tokenでトークンを更新し、有効なaccess_tokenを返す。
    更新した場合はファイルにアトミックに保存する（refresh_tokenは1回使い切りのため、
    保存を忘れると次回の更新に失敗する）。"""
    creds = load_credentials(credentials_path)
    if token_needs_refresh(creds["expires_at"]):
        token_response = refresh_token_pair(
            creds["client_id"], creds["client_secret"], creds["refresh_token"]
        )
        creds = merge_token_response(creds, token_response)
        save_credentials_atomic(credentials_path, creds)
    return creds["access_token"]


def api_call(access_token: str, company_id: int, method: str, path: str, json_body: dict | None = None) -> dict:
    url = f"{API_BASE}{path}"
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query)
    query.append(("company_id", str(company_id)))
    url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))

    data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {access_token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise FreeeApiError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')}") from e


def verify_single_company(companies_response: list, expected_company_id: int,
                          ignored_company_ids=None) -> None:
    """トークンで見える事業所が対象1社だけであることを確かめる。

    ignored_company_ids には、対象社と同じfreeeアカウントに紐づいていて自分では
    削除できない事業所（例：freeeの「開発用テスト事業所」。アプリ0件で3ヶ月放置後に
    freee側が自動削除するまで残る）を列挙できる。列挙した事業所は「想定外」と
    みなさない。それ以外の事業所が混ざっていれば従来通りエラーにする。
    """
    ignored = set(ignored_company_ids or [])
    ids = [c["id"] for c in companies_response]
    remaining = [i for i in ids if i not in ignored]
    if remaining != [expected_company_id]:
        raise FreeeApiError(
            f"想定外の事業所が含まれています（期待: {expected_company_id}, 実際: {ids}）。"
            "事業所選択をやり直してください。"
        )
