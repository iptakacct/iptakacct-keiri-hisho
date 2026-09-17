"""evidence.csv（証憑と明細行の対応）の読み書きと証憑ID。"""
import hashlib
from pathlib import Path

from common import EVIDENCE_COLUMNS, append_rows, normalize_key, read_rows

EVIDENCE_FILE = "evidence.csv"
STATE_MATCHED = "明細に対応"
STATE_NEW_ENTRY = "新規仕訳"
STATE_MULTIPLE = "複数候補"
STATE_UNPAID = "未払候補"
CANDIDATE_SEPARATOR = ";"  # 複数候補のとき、取り込み元ID欄に候補のIDをこの記号でつないで書く


def make_evidence_id(date, amount, partner):
    """証憑ID：日付・金額・取引先（NFKC正規化し空白を除いたもの）のハッシュ。同じ領収書を2回撮影しても同じIDになる。"""
    key = "|".join([date, str(amount), normalize_key(partner)])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def receipt_source_id(evidence_id):
    return f"receipt:{evidence_id}"


def read_evidence(year_dir):
    return read_rows(Path(year_dir) / EVIDENCE_FILE)


def append_evidence(year_dir, rows):
    append_rows(Path(year_dir) / EVIDENCE_FILE, EVIDENCE_COLUMNS, rows)


def attached_source_ids(evidence_rows):
    """証憑が付いている明細行の取り込み元ID。"""
    return {r["取り込み元ID"] for r in evidence_rows if r["状態"] == STATE_MATCHED}


def candidate_ids(evidence_row):
    return [x for x in evidence_row["取り込み元ID"].split(CANDIDATE_SEPARATOR) if x]
