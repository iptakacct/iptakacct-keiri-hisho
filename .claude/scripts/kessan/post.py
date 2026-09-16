"""staging.csv の承認済み行を journal.csv に登録する。

1件でも不正な行があれば何も登録しない（途中まで登録された状態を作らない）。
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from common import (
    IMPORT_LOG_COLUMNS, JOURNAL_COLUMNS, STAGING_COLUMNS,
    KessanError, append_rows, project, read_rows, to_int, write_rows,
)


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


def _validate(groups, accounts, journal):
    errors = []
    registered = {r["取り込み元ID"] for r in journal if r.get("取り込み元ID")}
    id_groups = defaultdict(set)
    for key, lines in groups.items():
        label = _label(key, lines)
        debit = credit = 0
        for r in lines:
            for side in ("借方", "貸方"):
                amount = to_int(r[f"{side}金額"])
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
        if debit != credit:
            errors.append(f"{label}: 借方{debit}≠貸方{credit}")
    for source_id, keys in id_groups.items():
        if len(keys) > 1:
            errors.append(f"取り込み元ID {source_id} が複数の伝票に含まれている")
    return errors


def _update_import_log(year_dir, numbers_by_file):
    path = year_dir / "import-log.csv"
    log = read_rows(path)
    changed = False
    for r in log:
        numbers = numbers_by_file.get(r["ファイル名"])
        if not numbers:
            continue
        low, high = min(numbers), max(numbers)
        if r["登録伝票番号範囲"]:
            old_low, old_high = (int(x) for x in r["登録伝票番号範囲"].split("-"))
            low, high = min(low, old_low), max(high, old_high)
        r["登録伝票番号範囲"] = f"{low}-{high}"
        changed = True
    if changed:
        write_rows(path, IMPORT_LOG_COLUMNS, [project(r, IMPORT_LOG_COLUMNS) for r in log])


def post_approved(year_dir, accounts, now=None):
    year_dir = Path(year_dir)
    staging = read_rows(year_dir / "staging.csv")
    journal = read_rows(year_dir / "journal.csv")
    approved = [r for r in staging if r["承認"].strip() == "済"]
    remaining = [r for r in staging if r["承認"].strip() != "済"]
    if not approved:
        return PostResult(vouchers=[], remaining=len(remaining))

    groups = _group(approved)
    errors = _validate(groups, accounts, journal)
    if errors:
        raise KessanError("承認済みの行を登録できません（何も登録していません）:\n" + "\n".join(errors))

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
                "登録区分": "自動" if r["判定"] == "確立済み" else "確認済",
                "登録日時": stamp,
            })
            new_rows.append(row)
            if r["証憑ファイル"]:
                numbers_by_file[r["証憑ファイル"]].append(int(no))

    append_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, new_rows)
    write_rows(year_dir / "staging.csv", STAGING_COLUMNS, [project(r, STAGING_COLUMNS) for r in remaining])
    _update_import_log(year_dir, numbers_by_file)
    return PostResult(vouchers=vouchers, remaining=len(remaining))
