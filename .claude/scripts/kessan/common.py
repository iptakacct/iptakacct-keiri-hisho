"""kessan 共通部品：列定義とCSVの読み書き。

CSVはExcelでそのまま開けるよう、BOM付きUTF-8で保存する。
"""
import csv
import os
import re
import unicodedata
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
EVIDENCE_COLUMNS = [
    "証憑ID", "証憑ファイル", "種類", "日付", "金額", "取引先", "内容", "科目候補", "補助候補",
    "状態", "取り込み元ID", "取り込み日時",
    "支払期日", "支払方法の推定", "自信度",  # 段階3（未払の管理）用。読み取り結果の値をそのまま残す
]
IMPORT_LOG_COLUMNS = ["取り込み日時", "ファイル名", "口座ID", "対象期間", "件数", "入金合計", "出金合計", "登録伝票番号範囲"]
STATEMENT_BALANCE_COLUMNS = ["科目", "補助", "日付", "残高", "ファイル名"]
OPENING_COLUMNS = ["科目", "補助", "残高"]
DISCARD_LOG_COLUMNS = ["日時", "取り込み元ID", "日付", "金額", "摘要", "理由"]

YEAR_FILES = {
    "journal.csv": JOURNAL_COLUMNS,
    "adjustments.csv": JOURNAL_COLUMNS,
    "staging.csv": STAGING_COLUMNS,
    "import-log.csv": IMPORT_LOG_COLUMNS,
    "statement-balances.csv": STATEMENT_BALANCE_COLUMNS,
    "opening-balances.csv": OPENING_COLUMNS,
    "evidence.csv": EVIDENCE_COLUMNS,
    "discard-log.csv": DISCARD_LOG_COLUMNS,
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
    """CSVの金額欄を整数にする。全角数字も読む（NFKC）。桁区切り・円記号・空白は無視し、空欄は0。読めなければ KessanError。"""
    s = unicodedata.normalize("NFKC", str(value if value is not None else "")).translate(_AMOUNT_NOISE)
    if not s:
        return 0
    if not re.fullmatch(r"-?[0-9]+", s):
        raise KessanError(f"{where}: 金額「{value}」を読めません")
    return int(s)


def to_int(value):
    return parse_amount(value, "金額")


def normalize_key(value):
    """照合用の正規化：NFKC（半角カナ→全角、全角英数→半角）にして、空白をすべて除く。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


REASON_SEPARATOR = "／"


def split_reasons(text):
    """要確認理由を「／」で分けた部分の一覧にする（前後の空白を除き、空の部分は捨てる）。"""
    return [part.strip() for part in str(text or "").split(REASON_SEPARATOR) if part.strip()]


def add_reasons(text, *new):
    """要確認理由に new の部分を追記する（既にある部分は足さない。順序は保つ）。

    スクリプトが付けた理由は追記だけで消さない。消してよいのは remove_reason で明示した部分だけ。
    """
    parts = split_reasons(text)
    for reason in new:
        for part in split_reasons(reason):
            if part not in parts:
                parts.append(part)
    return REASON_SEPARATOR.join(parts)


def remove_reason(text, reason):
    """要確認理由から、reason と完全に一致する部分だけを除く（相手科目未設定を外すときに使う）。"""
    return REASON_SEPARATOR.join(part for part in split_reasons(text) if part != reason)


DISCARD_LOG = "discard-log.csv"


def discarded_source_ids(year_dir):
    """discard-log.csv に記録された（オーナーが破棄した）取り込み元ID。取り込みのたびにこれを入れ直さない。"""
    return {r["取り込み元ID"].strip() for r in read_rows(Path(year_dir) / DISCARD_LOG) if r["取り込み元ID"].strip()}


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


def cannot_write(name):
    return KessanError(f"{name} に書き込めません（Excelで開いていたら閉じてから再実行）")


def ensure_writable(year_dir, names):
    """書き込む前に、対象のCSVが全部書き込めるか確かめる（Excelで開いたままだと書けない。途中まで書いた状態を作らない）。"""
    for name in names:
        path = Path(year_dir) / name
        if not path.exists():
            continue
        try:
            with open(path, "a", encoding="utf-8"):
                pass
        except OSError:
            raise cannot_write(name) from None


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
