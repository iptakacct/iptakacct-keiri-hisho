"""MF会計APIの読み取り（GETだけ）。MCPの get 系ツール（getJournals・getAccounts等）の代わり。

本文の形はMCPの戻り値と同じ。大きな取得はファイルに保存し、画面には件数と保存先だけを出す
（大きな取得はファイルに落として Python で解析する）。
"""
import os
import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode

import mf_paging

# 読み取りを許す口を明示的に絞る（書き込み口・明細登録口〔/transactions/journalize〕は対象外）。
_ALLOWED = [r"/offices", r"/accounts", r"/sub_accounts", r"/taxes", r"/departments",
            r"/trade_partners", r"/term_settings", r"/connected_accounts", r"/journals",
            r"/journals/[^/?]+", r"/transactions", r"/reports/(trial_balance|transition)_(bs|pl)"]
# ページ送りする一覧の配列名。ここに無いものは --all-pages でもクエリそのままの1回のGETにする。
# 取引先・部門・補助科目は page/per_page を付けると実APIが HTTP 400（unsupported_query_parameter）を
# 返すので入れない。
_PAGED = {"/journals": "journals", "/transactions": "transactions"}
# 件数を数えるための配列名（ページ送りしない一覧を含む）。1件取得・レポート系は持たない。
_COUNT_KEYS = dict(_PAGED, **{"/trade_partners": "trade_partners", "/sub_accounts": "sub_accounts",
                              "/departments": "departments", "/accounts": "accounts", "/taxes": "taxes"})


def check_path(path: str) -> str:
    """許可リストに無いパスは読ませない（誤って書き込み系・対象外のエンドポイントを叩かないため）。"""
    bare = (path or "").split("?")[0].rstrip("/")
    if not any(re.fullmatch(p, bare) for p in _ALLOWED) or bare.endswith("/journalize"):
        raise ValueError(f"読み取れるのはMF会計APIの一覧・1件取得・レポートだけです（{path}）")
    return path


def _bare(path: str) -> str:
    return path.split("?")[0].rstrip("/")


def list_key(path: str):
    """一覧なら件数を数える配列名を返す（ページ送りしない一覧も含む）。1件取得・レポートは None。"""
    return _COUNT_KEYS.get(_bare(path))


def paged_key(path: str):
    """ページ送りする一覧（仕訳・明細）なら配列名を返す。それ以外は None。"""
    return _PAGED.get(_bare(path))


def read(get, path: str, all_pages: bool, sleep) -> dict:
    """許可リストを確かめたうえで読む。`all_pages` かつページ送りする一覧（仕訳・明細）なら最後まで取る。
    それ以外は `all_pages` でもクエリそのままの1回のGET。"""
    path = check_path(path)
    key = paged_key(path)
    if not (all_pages and key):
        return get(path)
    # 呼び出し元が渡した page/per_page は捨てて、mf_paging.fetch_all に付け直させる
    # （二重に page= が付かないように。R1）。
    bare, _, q = path.partition("?")
    params = [(k, v) for k, v in parse_qsl(q, keep_blank_values=True) if k not in ("page", "per_page")]
    base = bare + ("?" + urlencode(params, safe="[]%") if params else "")
    return {key: mf_paging.fetch_all(get, base, key, sleep=sleep)}


def default_out(repo_root: str, company: dict, now=None) -> str:
    """保存先の既定パス：<インスタンス>/work/mf-read/<事業者コード>_<日時>.json。"""
    now = now or datetime.now()
    return os.path.join(repo_root, company["instance"], "work", "mf-read",
                        f"{company['office_id']}_{now.strftime('%Y%m%d-%H%M%S')}.json")
