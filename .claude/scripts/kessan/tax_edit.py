"""税の列を直すコマンド（段階3a）：登録済みの伝票の税の列の修正（set-tax）と、税率が混在する明細の分割（split-tax）。

- AIは journal.csv・adjustments.csv・staging.csv を直接編集せず、必ずこのコマンドを通す
- 全件を検証してから一括で反映する（1件でも不正なら何も書かない）
"""
import json
from collections import defaultdict
from pathlib import Path

from accounts import load_accounts
from common import (
    JOURNAL_COLUMNS, STAGING_COLUMNS, TAX_COLUMNS, KessanError, cannot_write, ensure_writable, parse_amount, project, read_rows,
    remove_reasons, replace_rows,
)
from tax import ALLOCATION_VALUES, INVOICE_VALUES, TAX_CATEGORIES, load_tax_settings, with_rate
from tax_fill import MIXED_RATE_REASON

SPLIT_RATES = (10, 8)


def tax_value_error(column, value):
    """税の列に書けない値なら理由を返す（書ける値と空は None）。"""
    if value == "":
        return None
    allowed = {"税区分": TAX_CATEGORIES, "インボイス": INVOICE_VALUES, "個別対応": ALLOCATION_VALUES}
    for suffix, values in allowed.items():
        if column.endswith(suffix):
            if value not in values:
                return f"{column}「{value}」は使えない値（使える値: {'・'.join(values)}）"
            return None
    return f"{column} は税の列ではありません"


def load_tax_updates(path):
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise KessanError(f"税の修正のファイルがありません: {path}") from None
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise KessanError(f"{path.name}: JSONとして読めません（{e}）") from None


# ---- set-tax ----

TAX_TARGET_FILES = ("journal.csv", "adjustments.csv")


def _check_tax_update(u):
    """1件の更新の形を検証する。(エラー一覧, 伝票番号, 行) を返す。"""
    if not isinstance(u, dict):
        return ["更新は {伝票番号, 行, 列名: 値} の形で書く"], None, None
    no, row_no = u.get("伝票番号"), u.get("行")
    if not isinstance(no, str) or not no.strip():
        return ["伝票番号（文字列）がありません"], None, None
    no = no.strip()
    where = f"伝票{no}"
    errors = []
    if isinstance(row_no, bool) or not isinstance(row_no, int) or row_no < 1:
        errors.append(f"{where}: 行は1以上の整数（伝票内の行番号）で書く（値: {row_no}）")
    values = {k: v for k, v in u.items() if k not in ("伝票番号", "行")}
    unknown = [k for k in values if k not in TAX_COLUMNS]
    if unknown:
        errors.append(f"{where}: 書き込めない列 {'・'.join(unknown)}（書ける列: {'・'.join(TAX_COLUMNS)}）")
    if not values:
        errors.append(f"{where}: 直す列がありません")
    for k, v in values.items():
        if k not in TAX_COLUMNS:
            continue
        if not isinstance(v, str):
            errors.append(f"{where}: {k} は文字列で書く")
        else:
            message = tax_value_error(k, v.strip())
            if message:
                errors.append(f"{where}: {message}")
    return errors, no, row_no


def set_journal_tax(year_dir, updates):
    """updates：[{"伝票番号": "12", "行": 1, "借方税区分": "…", ...}]（行は伝票内の1からの行番号）。

    登録済みの journal.csv・adjustments.csv の税の列だけを直す（金額・科目などは変えない）。
    全件を検証してから一括で反映し、直した行数を返す。
    """
    year_dir = Path(year_dir)
    if not isinstance(updates, list) or not updates:
        raise KessanError("税の修正は [{伝票番号, 行, 列名: 値}] の形で1件以上書く")
    files = {name: read_rows(year_dir / name) for name in TAX_TARGET_FILES}
    vouchers = {}  # 伝票番号 -> (ファイル名, [行])
    ambiguous = set()  # journal.csv と adjustments.csv の両方にある伝票番号（本来は無い。どちらか決められない）
    for name, rows in files.items():
        for r in rows:
            no = r["伝票番号"].strip()
            if no in vouchers and vouchers[no][0] != name:
                ambiguous.add(no)
            vouchers.setdefault(no, (name, []))[1].append(r)
    errors, seen, targets = [], set(), []
    for u in updates:
        found, no, row_no = _check_tax_update(u)
        errors += found
        if no is None or found:
            continue
        if no not in vouchers:
            errors.append(f"伝票{no}: journal.csv・adjustments.csv にありません")
            continue
        if no in ambiguous:
            errors.append(f"伝票{no}: journal.csv と adjustments.csv の両方にあり、どちらか決められません（帳簿を確認）")
            continue
        lines = vouchers[no][1]
        if row_no > len(lines):
            errors.append(f"伝票{no}: 行{row_no} は範囲外（この伝票は{len(lines)}行）")
            continue
        if (no, row_no) in seen:
            errors.append(f"伝票{no} 行{row_no} が重複しています")
            continue
        seen.add((no, row_no))
        targets.append((vouchers[no][0], lines[row_no - 1], {k: v.strip() for k, v in u.items() if k in TAX_COLUMNS}))
    if errors:
        raise KessanError("税の修正を反映できません（何も変更していません）:\n" + "\n".join(errors))
    changed_files = defaultdict(int)
    for name, row, values in targets:
        row.update(values)
        changed_files[name] += 1
    ensure_writable(year_dir, list(changed_files))
    for name in changed_files:
        try:
            replace_rows(year_dir / name, JOURNAL_COLUMNS, [project(r, JOURNAL_COLUMNS) for r in files[name]])
        except OSError:
            raise cannot_write(name) from None
    return len(targets)


# ---- split-tax ----

def _free_voucher_number(staging):
    used = {r["伝票番号"].strip() for r in staging}
    n = 1
    while f"S{n}" in used:
        n += 1
    return f"S{n}"


def _blank_side(row, side):
    for suffix in ("科目", "補助", "金額", "税区分", "インボイス", "個別対応"):
        row[f"{side}{suffix}"] = ""


def _check_parts(parts):
    errors = []
    if not isinstance(parts, (list, tuple)) or len(parts) < 2:
        return ["分ける区分は2つ以上必要です（税率ごとに --part 10:金額 --part 8:金額）"]
    for part in parts:
        try:
            rate, amount = part
        except (TypeError, ValueError):
            errors.append(f"分ける区分は (税率, 金額) の形で書く（値: {part}）")
            continue
        if isinstance(rate, bool) or rate not in SPLIT_RATES:
            errors.append(f"税率は10か8（値: {rate}）")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
            errors.append(f"金額は1以上の整数（値: {amount}）")
    return errors


def split_tax(year_dir, source_id, parts, payment_accounts, accounts=None):
    """staging.csv の未承認の1行を、税率ごとに分けた複合仕訳にする。付けた伝票番号（S1, S2…）を返す。

    parts：[(税率, 税込金額), ...]（税率は10か8。合計が相手側の金額と一致すること）。
    明細側（payment_accounts に入っている側）は1行に残し、相手側を parts の数の行に分ける。
    課税の年度だけ、相手側の税区分（空なら科目の既定）を税率に合わせて替える。免税の年度は税の列を触らない。
    """
    year_dir = Path(year_dir)
    accounts = accounts if accounts is not None else load_accounts()
    where = f"取り込み元ID {source_id}"
    errors = _check_parts(parts)
    staging = read_rows(year_dir / "staging.csv")
    matches = [i for i, r in enumerate(staging) if r["取り込み元ID"].strip() == source_id]
    if not matches:
        errors.append(f"{where}: staging.csv にありません")
    elif len(matches) > 1:
        errors.append(f"{where}: staging.csv に{len(matches)}行あります（複合仕訳は分割できない）")
    if errors:
        raise KessanError("税率で分けられません（何も変更していません）:\n" + "\n".join(errors))
    index = matches[0]
    row = staging[index]
    if row["承認"].strip() == "済":
        errors.append(f"{where}: 承認済みの行は分けられません")
    if row["伝票番号"].strip():
        errors.append(f"{where}: 既に伝票番号（{row['伝票番号'].strip()}）が付いています")
    keys = {side: (row[f"{side}科目"].strip(), row[f"{side}補助"].strip()) for side in ("借方", "貸方")}
    paying = [side for side in ("借方", "貸方") if keys[side] in payment_accounts]
    if len(paying) != 1:
        errors.append(f"{where}: 明細側（口座・カード・現金）がちょうど1つの行だけ分けられます"
                      f"（借方 {' '.join(keys['借方'])}／貸方 {' '.join(keys['貸方'])}）")
    if errors:
        raise KessanError("税率で分けられません（何も変更していません）:\n" + "\n".join(errors))
    pay_side = paying[0]
    other = "貸方" if pay_side == "借方" else "借方"
    other_amount = parse_amount(row[f"{other}金額"], f"staging.csv {where}")
    total = sum(amount for _, amount in parts)
    if total != other_amount:
        raise KessanError(f"{where}: 合計{total}が相手側の金額{other_amount}と一致しません")

    other_account = accounts.get(row[f"{other}科目"].strip())
    base_tax = row[f"{other}税区分"].strip() or (other_account.default_tax if other_account else "")
    taxable = load_tax_settings(year_dir).taxable
    if taxable:
        category = TAX_CATEGORIES.get(base_tax)
        if category is None or category.rate is None:
            raise KessanError(f"{where}: {other}の税区分「{base_tax}」は税率を持たないので分けられません"
                              "（先に set-accounts で税区分を決める）")

    number = _free_voucher_number(staging)
    reasons = remove_reasons(row["要確認理由"], MIXED_RATE_REASON)
    new_rows = []
    for rate, amount in parts:
        part = dict(row)
        _blank_side(part, pay_side)
        part[f"{other}金額"] = str(amount)
        if taxable:
            part[f"{other}税区分"] = with_rate(base_tax, rate)
        new_rows.append(part)
    pay_line = dict(row)
    _blank_side(pay_line, other)
    new_rows.append(pay_line)
    for r in new_rows:
        r["伝票番号"] = number
        r["要確認理由"] = reasons
    staging[index:index + 1] = new_rows
    ensure_writable(year_dir, ["staging.csv"])
    replace_rows(year_dir / "staging.csv", STAGING_COLUMNS, [project(r, STAGING_COLUMNS) for r in staging])
    return number
