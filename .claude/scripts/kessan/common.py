"""kessan 共通部品：列定義とCSVの読み書き。

CSVはExcelでそのまま開けるよう、BOM付きUTF-8で保存する。
"""
import csv
import os
import re
from datetime import datetime
from pathlib import Path

import yaml


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


def _utf8_error(path):
    return KessanError(f"{Path(path).name}: UTF-8で読めません（Excelでは『CSV UTF-8』で保存）")


def read_rows(path):
    path = Path(path)
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except UnicodeDecodeError:
        raise _utf8_error(path) from None


def write_rows(path, columns, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def append_rows(path, columns, rows):
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        write_rows(path, columns, rows)
        return
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            header = next(csv.reader(f), [])
    except UnicodeDecodeError:
        raise _utf8_error(path) from None
    if header != list(columns):
        raise KessanError(f"{path.name} の列が想定と違います（期待: {'、'.join(columns)}／実際: {'、'.join(header)}）")
    with path.open("rb") as f:
        f.seek(-1, os.SEEK_END)
        ends_with_newline = f.read(1) == b"\n"
    with path.open("a", encoding="utf-8", newline="") as f:
        if not ends_with_newline:
            f.write("\r\n")
        csv.DictWriter(f, fieldnames=columns).writerows(rows)


def replace_rows(path, columns, rows):
    """一時ファイルに書いてから os.replace で置き換える（途中で止まっても元のファイルは壊れない）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(str(tmp_path), str(path))
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def project(row, columns):
    return {c: row.get(c, "") for c in columns}


_AMOUNT_NOISE = str.maketrans("", "", ",，¥￥\\円 　")


def parse_amount(value, where):
    """CSVの金額欄を整数にする。桁区切り・円記号・空白は無視し、空欄は0。読めなければ KessanError。"""
    s = str(value if value is not None else "").translate(_AMOUNT_NOISE)
    if not s:
        return 0
    if not re.fullmatch(r"-?[0-9]+", s):
        raise KessanError(f"{where}: 金額「{value}」を読めません")
    return int(s)


def to_int(value):
    return parse_amount(value, "金額")


PERIOD_FILE = "period.yaml"


def parse_date(value):
    """YYYY-MM-DD の実在する日付なら文字列で返す。そうでなければ None。"""
    s = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return None
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None
    return s


def _validate_period(start, end):
    s, e = parse_date(start), parse_date(end)
    if s is None or e is None:
        raise KessanError(f"事業年度の期首日・期末日は YYYY-MM-DD の実在する日付で指定してください（期首日: {start}／期末日: {end}）")
    if s >= e:
        raise KessanError(f"期首日（{s}）は期末日（{e}）より前にしてください")
    return s, e


def load_period(year_dir):
    path = Path(year_dir) / PERIOD_FILE
    if not path.exists():
        raise KessanError(f"{PERIOD_FILE} がありません（init --start --end で作成）: {Path(year_dir)}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise KessanError(f"{PERIOD_FILE} を読めません: {e}") from None
    if not isinstance(data, dict):
        raise KessanError(f"{PERIOD_FILE} の形式が不正です（期首日・期末日を書く）")
    return _validate_period(data.get("期首日"), data.get("期末日"))


def init_year_dir(year_dir, start, end):
    year_dir = Path(year_dir)
    start, end = _validate_period(start, end)
    if (year_dir / PERIOD_FILE).exists():
        current = load_period(year_dir)
        if current != (start, end):
            raise KessanError(
                f"{PERIOD_FILE} の期間（{current[0]}〜{current[1]}）と指定（{start}〜{end}）が違います"
                "（年度フォルダを取り違えていないか確認）"
            )
    else:
        year_dir.mkdir(parents=True, exist_ok=True)
        (year_dir / PERIOD_FILE).write_text(f'期首日: "{start}"\n期末日: "{end}"\n', encoding="utf-8")
    for sub in ("inbox", "output"):
        (year_dir / sub).mkdir(parents=True, exist_ok=True)
    for name, columns in YEAR_FILES.items():
        if not (year_dir / name).exists():
            write_rows(year_dir / name, columns, [])
