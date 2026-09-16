"""kessan 共通部品：列定義とCSVの読み書き。

CSVはExcelでそのまま開けるよう、BOM付きUTF-8で保存する。
"""
import csv
from pathlib import Path


class KessanError(Exception):
    """利用者に内容を伝えて処理を止めるべきエラー。"""


JOURNAL_COLUMNS = [
    "伝票番号", "日付", "借方科目", "借方補助", "借方金額", "貸方科目", "貸方補助", "貸方金額",
    "摘要", "取引先", "証憑ファイル", "取り込み元ID", "登録区分", "登録日時",
]
STAGING_COLUMNS = JOURNAL_COLUMNS + ["読み取り信頼度", "判定", "要確認理由", "承認"]
IMPORT_LOG_COLUMNS = ["取り込み日時", "ファイル名", "口座ID", "対象期間", "件数", "入金合計", "出金合計", "登録伝票番号範囲"]
STATEMENT_BALANCE_COLUMNS = ["科目", "補助", "日付", "残高", "ファイル名"]
OPENING_COLUMNS = ["科目", "補助", "残高"]

YEAR_FILES = {
    "journal.csv": JOURNAL_COLUMNS,
    "adjustments.csv": JOURNAL_COLUMNS,
    "staging.csv": STAGING_COLUMNS,
    "import-log.csv": IMPORT_LOG_COLUMNS,
    "statement-balances.csv": STATEMENT_BALANCE_COLUMNS,
    "opening-balances.csv": OPENING_COLUMNS,
}


def read_rows(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_rows(path, columns, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def append_rows(path, columns, rows):
    path = Path(path)
    if not path.exists():
        write_rows(path, columns, rows)
        return
    with path.open("a", encoding="utf-8", newline="") as f:
        csv.DictWriter(f, fieldnames=columns).writerows(rows)


def project(row, columns):
    return {c: row.get(c, "") for c in columns}


def to_int(value):
    s = str(value or "").replace(",", "").replace("¥", "").replace("\\", "").strip()
    return int(s) if s else 0


def init_year_dir(year_dir):
    year_dir = Path(year_dir)
    for sub in ("inbox", "output"):
        (year_dir / sub).mkdir(parents=True, exist_ok=True)
    for name, columns in YEAR_FILES.items():
        if not (year_dir / name).exists():
            write_rows(year_dir / name, columns, [])
