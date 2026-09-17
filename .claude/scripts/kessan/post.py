"""staging.csv の承認済み行を journal.csv に登録する。

1件でも不正な行があれば何も登録しない（途中まで登録された状態を作らない）。

書き込みの順序（途中で止まっても二重登録にならないようにする）:
1. 3つのCSVが書き込めるか先に確かめる（Excelで開いたままだと書けない）
2. 取り込み元IDの無い承認済み行に manual:<ランダム値> を付け、staging.csv に保存する
3. journal.csv を置き換える（ここで登録が確定する）
4. staging.csv から登録済みの行を消し、import-log.csv を更新する
3のあとで4が失敗しても、staging.csv に残った行は取り込み元IDが journal.csv にあるため、再実行では拒否される。
"""
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from common import (
    IMPORT_LOG_COLUMNS, JOURNAL_COLUMNS, STAGING_COLUMNS,
    KessanError, cannot_write, ensure_writable, load_period, parse_amount, parse_date, project, read_rows, replace_rows,
)

WRITE_TARGETS = ("journal.csv", "staging.csv", "import-log.csv")


@dataclass(frozen=True)
class PostResult:
    vouchers: list
    remaining: int


def _group(approved):
    groups = {}
    for i, r in enumerate(approved):
        key = r["伝票番号"].strip() or f"__line{i}"
        groups.setdefault(key, []).append(r)
    return groups


def _label(key, lines):
    if key.startswith("__line"):
        return f"{lines[0]['日付']} {lines[0]['摘要']}".strip()
    return f"伝票{key}"


def _validate(groups, accounts, journal, period):
    start, end = period
    errors = []
    registered = {r["取り込み元ID"] for r in journal if r.get("取り込み元ID")}
    id_groups = defaultdict(set)
    for key, lines in groups.items():
        label = _label(key, lines)
        debit = credit = 0
        dates = set()
        for r in lines:
            raw_date = r["日付"].strip()
            dates.add(raw_date)
            date = parse_date(raw_date)
            if date is None:
                errors.append(f"{label}: 日付「{raw_date}」はYYYY-MM-DD形式で入力（Excelで保存すると2025/4/1になることがある）")
            elif not start <= date <= end:
                errors.append(f"{label}: 期間外の日付「{date}」（{start}〜{end}）")
            for side in ("借方", "貸方"):
                try:
                    amount = parse_amount(r[f"{side}金額"], f"staging.csv {label}")
                except KessanError as e:
                    errors.append(str(e))
                    amount = 0
                if amount < 0:
                    errors.append(f"{label}: マイナスの金額（{side}{amount}）")
                name = r[f"{side}科目"].strip()
                if amount and not name:
                    errors.append(f"{label}: {side}科目が未設定")
                if name and name not in accounts:
                    errors.append(f"{label}: 科目マスタに無い{side}科目「{name}」")
                if side == "借方":
                    debit += amount
                else:
                    credit += amount
            source_id = r["取り込み元ID"].strip()
            if source_id:
                id_groups[source_id].add(key)
                if source_id in registered:
                    errors.append(f"{label}: 取り込み元ID {source_id} は登録済み")
        if len(dates) > 1:
            errors.append(f"{label}: 伝票内で日付が違う（{'・'.join(sorted(dates))}）")
        if debit != credit:
            errors.append(f"{label}: 借方{debit}≠貸方{credit}")
        if debit == 0:
            errors.append(f"{label}: 金額が0の伝票")
    for source_id, keys in id_groups.items():
        if len(keys) > 1:
            errors.append(f"取り込み元ID {source_id} が複数の伝票に含まれている")
    return errors


def _assign_manual_ids(groups):
    """取り込み元IDの無い行に、伝票ごとに1つの manual:<ランダム値> を付ける。"""
    for lines in groups.values():
        manual_id = None
        for r in lines:
            if not r["取り込み元ID"].strip():
                manual_id = manual_id or f"manual:{uuid.uuid4().hex}"
                r["取り込み元ID"] = manual_id


def _update_import_log(year_dir, log, numbers_by_file):
    """log は登録の前に読んでおいた import-log.csv の行（登録後に文字コードの問題で止まらないようにする）。"""
    path = year_dir / "import-log.csv"
    changed = False
    for r in log:
        numbers = numbers_by_file.get(r["ファイル名"])
        if not numbers:
            continue
        low, high = min(numbers), max(numbers)
        if r["登録伝票番号範囲"]:
            try:
                old_low, old_high = (int(x) for x in r["登録伝票番号範囲"].split("-"))
            except ValueError:
                raise KessanError(f"import-log.csv の登録伝票番号範囲が不正です: {r['登録伝票番号範囲']}（帳簿への登録は完了済み）") from None
            low, high = min(low, old_low), max(high, old_high)
        r["登録伝票番号範囲"] = f"{low}-{high}"
        changed = True
    if changed:
        replace_rows(path, IMPORT_LOG_COLUMNS, [project(r, IMPORT_LOG_COLUMNS) for r in log])


def post_approved(year_dir, accounts, now=None):
    year_dir = Path(year_dir)
    staging = read_rows(year_dir / "staging.csv")
    journal = read_rows(year_dir / "journal.csv")
    approved = [r for r in staging if r["承認"].strip() == "済"]
    remaining = [r for r in staging if r["承認"].strip() != "済"]
    if not approved:
        return PostResult(vouchers=[], remaining=len(remaining))

    groups = _group(approved)
    errors = _validate(groups, accounts, journal, load_period(year_dir))
    if errors:
        raise KessanError("承認済みの行を登録できません（何も登録していません）:\n" + "\n".join(errors))
    log = read_rows(year_dir / "import-log.csv")  # 読めなければここで止まる（まだ何も書いていない）
    ensure_writable(year_dir, WRITE_TARGETS)

    _assign_manual_ids(groups)
    try:
        replace_rows(year_dir / "staging.csv", STAGING_COLUMNS, [project(r, STAGING_COLUMNS) for r in staging])
    except OSError:
        raise cannot_write("staging.csv") from None

    next_no = max((int(r["伝票番号"]) for r in journal if r["伝票番号"].isdigit()), default=0) + 1
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    new_rows, vouchers = [], []
    numbers_by_file = defaultdict(list)
    for lines in groups.values():
        no = str(next_no)
        next_no += 1
        vouchers.append(no)
        for r in lines:
            row = project(r, JOURNAL_COLUMNS)
            row.update({
                "伝票番号": no,
                "日付": r["日付"].strip(),
                "登録区分": "自動" if r["判定"] == "確立済み" else "確認済",
                "登録日時": stamp,
            })
            new_rows.append(row)
            if r["証憑ファイル"]:
                numbers_by_file[r["証憑ファイル"]].append(int(no))

    full_journal = [project(r, JOURNAL_COLUMNS) for r in journal] + new_rows
    try:
        replace_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, full_journal)
    except OSError:
        raise cannot_write("journal.csv") from None

    # ここから先は登録が確定済み。失敗しても再実行で二重登録にはならない（取り込み元IDで拒否される）
    current = "staging.csv"
    try:
        replace_rows(year_dir / "staging.csv", STAGING_COLUMNS, [project(r, STAGING_COLUMNS) for r in remaining])
        current = "import-log.csv"
        _update_import_log(year_dir, log, numbers_by_file)
    except OSError:
        raise KessanError(
            f"帳簿への登録は完了済み（伝票 {', '.join(vouchers)}）ですが、{current} の更新に失敗しました。"
            f"{current} を閉じてから、staging.csv の登録済み行（取り込み元IDが journal.csv にある行）を削除してください"
        ) from None
    return PostResult(vouchers=vouchers, remaining=len(remaining))
