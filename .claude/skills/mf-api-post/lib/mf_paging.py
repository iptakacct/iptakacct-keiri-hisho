"""マネーフォワードAPIの一覧系エンドポイントをページの最後まで取る共通ロジック

CLI（mf_cli）と各ライブラリの両方でページングが必要になるため、共通ロジックをここへ1つだけ置き、
`mf_cli.fetch_all` はこちらへ委譲する（lib から CLI を import すると依存の向きが逆転するのを避ける）。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List

# MFの秒間制限があるため、ページをまたぐGETの前に間隔を空ける。
DEFAULT_INTERVAL_SECONDS = 1.5


def fetch_all(get: Callable[[str], Dict[str, Any]], path: str, key: str, *,
              per_page: int = 500, sleep=time.sleep,
              interval_seconds: float = DEFAULT_INTERVAL_SECONDS) -> List[Any]:
    """ページを最後まで取る。

    MFの一覧系APIは `page`・`per_page` をクエリで受け取る。
    実データで確認したところ（journals 789件・transactions 640件）、応答には
    `metadata.total_count`・`metadata.total_pages` が付く。**これがある場合はそれで
    最終ページを判定する**（「返ってきた配列が per_page 未満になったら最終ページ」という
    件数だけの判定だと、総件数が per_page のちょうど倍数のときに存在しない次ページを
    要求してしまい、MFがHTTP 400を返して処理が止まる境界バグがあった）。
    `metadata` が無い応答（未確認の別エンドポイント等）に備え、件数だけで判定する
    従来のfail-safeロジックはフォールバックとして残す（空配列が返るケースもこれに
    含まれる：0 < per_page で必ず止まる）。

    MFの秒間制限を踏まないよう、**2回目以降のGETの前**に `sleep(interval_seconds)` を挟む
    （1ページしか無い呼び出し、および最初のページの前は待たない）。
    """
    results: list = []
    page = 1
    first = True
    while True:
        if not first:
            sleep(interval_seconds)
        first = False
        sep = "&" if "?" in path else "?"
        resp = get(f"{path}{sep}page={page}&per_page={per_page}") or {}
        items = resp.get(key) or []
        results.extend(items)

        metadata = resp.get("metadata") or {}
        total_pages = metadata.get("total_pages")
        if total_pages is None:
            total_count = metadata.get("total_count")
            if total_count is not None and per_page:
                total_pages = -(-total_count // per_page)  # ceil division

        if total_pages is not None:
            if page >= total_pages:
                break
        elif len(items) < per_page:
            break
        page += 1
    return results
