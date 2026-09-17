"""staging.csv の更新：科目候補の書き込み（set-accounts）と承認（approve・apply-review）。

- AIは staging.csv を直接編集せず、必ずこのコマンドを通す
- 全件を検証してから一括で反映する（1件でも不正なら何も書かない）
- 取り込み元IDで行を特定する（取り込み元IDの無い手入力の行は対象外。人が staging.csv で確認する）
"""
import json
from collections import defaultdict
from pathlib import Path

from common import STAGING_COLUMNS, KessanError, ensure_writable, parse_amount, project, read_rows, replace_rows

SET_FIELDS = ("借方科目", "借方補助", "貸方科目", "貸方補助", "取引先", "判定", "要確認理由")
AI_JUDGEMENT = "要確認"
ESTABLISHED = "確立済み"


def load_updates(path):
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise KessanError(f"科目候補のファイルがありません: {path}") from None
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise KessanError(f"{path.name}: JSONとして読めません（{e}）") from None


def _rows_by_id(staging):
    index = defaultdict(list)
    for r in staging:
        source_id = r["取り込み元ID"].strip()
        if source_id:
            index[source_id].append(r)
    return index


def _write_staging(year_dir, staging):
    ensure_writable(year_dir, ["staging.csv"])
    replace_rows(Path(year_dir) / "staging.csv", STAGING_COLUMNS, [project(r, STAGING_COLUMNS) for r in staging])


def _check_update(source_id, rows, values, accounts, payment_accounts):
    where = f"取り込み元ID {source_id}"
    if not rows:
        return [f"{where}: staging.csv にありません"]
    if len(rows) > 1:
        return [f"{where}: staging.csv に{len(rows)}行あります（複合仕訳は人が確認する）"]
    if not isinstance(values, dict):
        return [f"{where}: 値は {{列名: 値}} の形で書く"]
    row = rows[0]
    errors = []
    unknown = [k for k in values if k not in SET_FIELDS]
    if unknown:
        errors.append(f"{where}: 書き込めない列 {'・'.join(unknown)}（書ける列: {'・'.join(SET_FIELDS)}）")
    non_text = [k for k, v in values.items() if not isinstance(v, str)]
    if non_text:
        errors.append(f"{where}: {'・'.join(non_text)} は文字列で書く")
        return errors
    if row["承認"].strip() == "済":
        errors.append(f"{where}: 承認済みの行は上書きできません")
    judgement = values.get("判定")
    if judgement == ESTABLISHED:
        errors.append(f"{where}: 判定=確立済み はAIからは指定できません（確立済みパターンとの照合でスクリプトが付ける）")
    elif judgement is not None and judgement != AI_JUDGEMENT:
        errors.append(f"{where}: 判定に指定できるのは「{AI_JUDGEMENT}」だけです（値: {judgement}）")
    for side in ("借方", "貸方"):
        name = values.get(f"{side}科目", row[f"{side}科目"]).strip()
        if name and name not in accounts:
            errors.append(f"{where}: 科目マスタに無い{side}科目「{name}」")
        current = (row[f"{side}科目"].strip(), row[f"{side}補助"].strip())
        new = (name, values.get(f"{side}補助", row[f"{side}補助"]).strip())
        if current in payment_accounts and new != current:
            errors.append(f"{where}: 明細側の{side}（{current[0]} {current[1]}）は変更できません")
    return errors


def set_accounts(year_dir, accounts, updates, payment_accounts):
    """updates：{取り込み元ID: {借方科目, 借方補助, 貸方科目, 貸方補助, 取引先, 判定, 要確認理由}}（書く列だけでよい）。

    payment_accounts：kessan-sources.yaml の口座・カード・現金の (科目, 補助)。明細側の科目は書き換えさせない。
    反映した行数を返す。
    """
    year_dir = Path(year_dir)
    if not isinstance(updates, dict) or not updates:
        raise KessanError("科目候補は {取り込み元ID: {列名: 値}} の形で1件以上書く")
    staging = read_rows(year_dir / "staging.csv")
    index = _rows_by_id(staging)
    errors = []
    for source_id, values in updates.items():
        errors += _check_update(source_id, index.get(source_id, []), values, accounts, payment_accounts)
    if errors:
        raise KessanError("科目候補を反映できません（何も変更していません）:\n" + "\n".join(errors))
    for source_id, values in updates.items():
        (row,) = index[source_id]
        row.update({k: v.strip() for k, v in values.items()})
        row["判定"] = AI_JUDGEMENT
    _write_staging(year_dir, staging)
    return len(updates)


def _check_approvable(source_id, rows, accounts):
    where = f"取り込み元ID {source_id}"
    if not rows:
        return [f"{where}: staging.csv にありません"]
    errors = []
    for row in rows:
        for side in ("借方", "貸方"):
            name = row[f"{side}科目"].strip()
            try:
                amount = parse_amount(row[f"{side}金額"], f"staging.csv {where}")
            except KessanError as e:
                errors.append(str(e))
                continue
            if amount and not name:
                errors.append(f"{where}: {side}科目が未設定")
            if name and name not in accounts:
                errors.append(f"{where}: 科目マスタに無い{side}科目「{name}」")
    return errors


def _approve_rows(staging, accounts, source_ids):
    index = _rows_by_id(staging)
    errors = []
    for source_id in source_ids:
        errors += _check_approvable(source_id, index.get(source_id, []), accounts)
    if errors:
        raise KessanError("承認できません（何も変更していません）:\n" + "\n".join(errors))
    changed = 0
    for source_id in source_ids:
        for row in index[source_id]:
            if row["承認"].strip() != "済":
                row["承認"] = "済"
                changed += 1
    return changed


def approve(year_dir, accounts, source_ids):
    """取り込み元IDの行を 承認=済 にする。借方・貸方の科目が埋まっていない行が1つでもあれば何も変えない。変更した行数を返す。"""
    year_dir = Path(year_dir)
    ids = list(dict.fromkeys(s.strip() for s in source_ids if s.strip()))
    if not ids:
        raise KessanError("承認する取り込み元IDがありません")
    staging = read_rows(year_dir / "staging.csv")
    changed = _approve_rows(staging, accounts, ids)
    _write_staging(year_dir, staging)
    return changed
