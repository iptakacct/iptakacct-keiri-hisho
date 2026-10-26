"""マネーフォワード クラウド会計API連携の共通ロジック（トークン管理・API呼び出し）。
複数社対応を前提に、認証情報ファイルのパスを都度引数で受け取る。"""
from __future__ import annotations
import base64
import hashlib
import json
import os
import tempfile
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone

import mf_lock

AUTHORIZE_ENDPOINT = "https://api.biz.moneyforward.com/authorize"
TOKEN_ENDPOINT = "https://api.biz.moneyforward.com/token"
API_BASE = "https://api-accounting.moneyforward.com/api/v3"
REDIRECT_URI = "http://localhost:8765/callback"
# 読み取り専用のスコープだけを要求する（最小権限）。仕訳の登録・書き換えを自前APIで行う仕組みを
# 入れるときは、書き込み用スコープ（journal.write / transaction.write 等）を足して再認可する。
SCOPES = [
    "mfc/accounting/offices.read",
    "mfc/accounting/accounts.read",
    "mfc/accounting/taxes.read",
    "mfc/accounting/departments.read",
    "mfc/accounting/master.read",
    "mfc/accounting/report.read",
    "mfc/accounting/journal.read",
    "mfc/accounting/trade_partners.read",
    "mfc/accounting/transaction.read",
    "mfc/accounting/connected_account.read",
    "mfc/accounting/voucher.read",
]
SCOPE = " ".join(SCOPES)
REFRESH_BUFFER_MINUTES = 10
RETRY_STATUSES = (429, 500, 502, 503, 504)
RETRY_WAITS = (1.5, 3.0, 6.0)


class MfApiError(Exception):
    """マネーフォワードAPIとの通信・認証で発生したエラー。"""


def load_credentials(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_credentials_atomic(path: str, data: dict) -> None:
    """認証情報を一時ファイルに書いてからリネームすることで、
    書き込み途中のクラッシュで内容が壊れる（refresh_tokenを失う）ことを防ぐ。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".mf_creds_", suffix=".tmp")
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
    """トークンエンドポイントのクライアント認証（HTTP Basic）用ヘッダー値を作る。
    freeeがclient_id/secretをPOSTボディに含める方式なのと異なり、
    マネーフォワードはBasic認証ヘッダーを要求する。"""
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _post_form(url: str, data: dict, auth_header: str) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Authorization", auth_header)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise MfApiError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')}") from e


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
    refresh_tokenが応答に含まれない場合は既存の値を維持する
    （両対応にする）。"""
    now = datetime.now(timezone.utc)
    expires_in = int(token_response["expires_in"])
    expires_at = now + timedelta(seconds=expires_in)
    merged = dict(existing)
    merged["access_token"] = token_response["access_token"]
    merged["refresh_token"] = token_response.get("refresh_token", existing.get("refresh_token"))
    merged["expires_at"] = expires_at.isoformat()
    return merged


def token_lock_name(credentials_path: str) -> str:
    """トークン更新のロック名。1ファイル＝1事業者なので、パスから決めれば事業者単位になる。
    呼び出し側に名前を渡させないことで、付け方のずれによる排他漏れを防ぐ。"""
    digest = hashlib.sha1(os.path.abspath(credentials_path).encode("utf-8")).hexdigest()[:12]
    return f"mf-token-{digest}"


def ensure_access_token(credentials_path: str) -> str:
    """有効なaccess_tokenを返す。期限が近ければ更新してファイルに保存する。

    マネーフォワードはrefresh_tokenを使い捨てにする（更新のたびに新しい値が発行され、
    古い値はその場で消える）。同じ認証情報を使う処理が重なると後発は必ず
    `[A053305] ... does not exist.`（invalid_grant）になるので、二段で守る
    。
      1. 更新の前後だけ会社ごとのロックを取り、取ったらファイルを読み直す
         （待つ間に先客が更新していれば、それを使う）
      2. それでも失敗したらファイルを読み直し、値が変わっていれば勝った側に乗り換える。
         変わっていなければ本当の失効として投げる
    """
    creds = load_credentials(credentials_path)
    if not token_needs_refresh(creds["expires_at"]):
        return creds["access_token"]

    with mf_lock.company_lock(token_lock_name(credentials_path)):
        creds = load_credentials(credentials_path)
        if not token_needs_refresh(creds["expires_at"]):
            return creds["access_token"]

        used_refresh_token = creds["refresh_token"]
        try:
            resp = refresh_token_pair(creds["client_id"], creds["client_secret"], used_refresh_token)
        except MfApiError:
            latest = load_credentials(credentials_path)
            if latest.get("refresh_token") == used_refresh_token:
                raise  # 誰も更新していない＝本当に失効している
            if not token_needs_refresh(latest["expires_at"]):
                return latest["access_token"]
            # 勝った側の結果も期限が近いなら、その新しいrefresh_tokenで1回だけやり直す
            resp = refresh_token_pair(latest["client_id"], latest["client_secret"],
                                      latest["refresh_token"])
            creds = latest

        creds = merge_token_response(creds, resp)
        save_credentials_atomic(credentials_path, creds)
        return creds["access_token"]


def _request(access_token: str, path: str, *, retries: int = 3, sleep=time.sleep) -> dict:
    """MFのAPIをGETで1回呼ぶ。秒間制限（429）と一時的なサーバーエラー（5xx）だけ、間隔を空けて再試行する。

    MFは秒あたりの呼び出し数に上限があり、まとめて処理すると 429 が返る。
    呼び出し側が件数を気にせず使えるよう、ここで吸収する。恒久的なエラー（400・401・403等）は再試行しない。
    読み取りは何度再試行しても副作用が無いので、5xxも再試行してよい。
    """
    url = f"{API_BASE}{path}"
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {access_token}")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            last = MfApiError(f"HTTP {e.code}: {detail}")
            if e.code not in RETRY_STATUSES or attempt >= retries:
                raise last from e
            sleep(RETRY_WAITS[min(attempt, len(RETRY_WAITS) - 1)])
    raise last  # ここには来ない（ループ内で必ず return か raise する）


def api_call(access_token: str, method: str, path: str, json_body: dict | None = None) -> dict:
    """読み取り専用（GETのみ）。書き込みは受け付けない。"""
    if method.upper() != "GET":
        raise MfApiError(
            f"書き込み（{method.upper()} {path}）は api_call では行えません。"
            "このスキルは読み取り専用です。"
        )
    return _request(access_token, path)


def verify_single_office(office: dict, expected_office_id: str) -> None:
    """GET /offices の応答（接続中の1事業者のオブジェクト。code＝事業者コード）が
    想定の事業者か確かめる。"""
    code = office.get("code") if isinstance(office, dict) else None
    if code != expected_office_id:
        raise MfApiError(
            f"想定外の事業者に接続しています（期待: {expected_office_id}, 実際: {code}）。"
            "事業者選択をやり直してください。"
        )
