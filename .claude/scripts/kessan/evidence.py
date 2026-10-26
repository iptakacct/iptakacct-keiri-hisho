"""evidence.csv（証憑と明細行の対応）の読み書きと証憑ID。"""
import hashlib
from pathlib import Path

from common import EVIDENCE_COLUMNS, append_rows, normalize_key, read_rows

EVIDENCE_FILE = "evidence.csv"
STATE_MATCHED = "明細に対応"
STATE_NEW_ENTRY = "新規仕訳"
STATE_MULTIPLE = "複数候補"
STATE_UNPAID = "未払候補"
STATE_PAYABLE = "未払計上"  # 決算整理で未払に計上した（kessan-close）
STATE_DISCARDED = "破棄"  # 証憑から作った行（receipt:）を discard で破棄した
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


def unique_evidence_id(evidence_rows, base_id):
    """base_id が既存の証憑IDと同じなら、-2・-3 …を付けて重複しないIDにする。

    別の資料ファイルが同じ内容（日付・金額・取引先）の証憑を含む場合に使う
    （同じ資料ファイルの再取り込みは import-log.csv で別途止めるので、ここでは止めない）。
    戻り値: (使う証憑ID, 重複していた元の証憑ID。重複が無ければ None)
    """
    existing = {r["証憑ID"] for r in evidence_rows}
    if base_id not in existing:
        return base_id, None
    n = 2
    candidate = f"{base_id}-{n}"
    while candidate in existing:
        n += 1
        candidate = f"{base_id}-{n}"
    return candidate, base_id


def known_source_ids(year_dir, entries=None):
    """帳簿（journal.csv・adjustments.csv）と staging.csv にある取り込み元ID（空は除く）。

    entries を渡すと、仕訳の全行をもう一度読まずにそれを使う。
    """
    from ledger import entry_rows  # 循環参照を避けるため関数内で読む
    if entries is None:
        entries = entry_rows(year_dir)
    ids = {r["取り込み元ID"].strip() for r in entries}
    ids |= {r["取り込み元ID"].strip() for r in read_rows(Path(year_dir) / "staging.csv")}
    ids.discard("")
    return ids


def evidence_by_source(evidence_rows):
    """明細行（取り込み元ID）ごとに、対応が1つに決まった証憑（明細に対応・新規仕訳）を返す。"""
    found = {}
    for r in evidence_rows:
        source_id = r["取り込み元ID"].strip()
        if r["状態"] in (STATE_MATCHED, STATE_NEW_ENTRY) and source_id and CANDIDATE_SEPARATOR not in source_id:
            found.setdefault(source_id, r)
    return found
