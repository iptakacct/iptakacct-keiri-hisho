"""銀行・カードのCSV明細を staging.csv に取り込む。

- 預金側の科目だけを埋め、相手科目は空のまま「要確認」にする（相手科目は段階2でAIが候補を付ける）
- 取り込み元ID（明細の内容から作るハッシュ）で、登録済み・取り込み済みの行を二重に取り込まない
- 明細に残高があれば statement-balances.csv に記録する（検算で帳簿残高と照合する）
"""
import csv
import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from common import (
    IMPORT_LOG_COLUMNS, STAGING_COLUMNS, STATEMENT_BALANCE_COLUMNS,
    KessanError, append_rows, read_rows, to_int,
)


@dataclass(frozen=True)
class ImportResult:
    added: int
    duplicates: int
    zero_amount: int


def load_sources(path):
    path = Path(path)
    if not path.exists():
        raise KessanError(f"口座・明細形式の設定ファイルがありません: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def parse_statement(file_path, fmt):
    file_path = Path(file_path)
    if not file_path.exists():
        raise KessanError(f"明細ファイルがありません: {file_path}")
    with file_path.open(encoding=fmt.get("encoding", "utf-8-sig"), newline="") as f:
        lines = list(csv.reader(f))
    header_index = fmt.get("header_row", 1) - 1
    if header_index >= len(lines):
        raise KessanError(f"{file_path.name}: 列見出しの行（{header_index + 1}行目）がありません")
    header = [h.strip() for h in lines[header_index]]
    idx = {}
    for key, name in fmt["columns"].items():
        if name not in header:
            raise KessanError(f"{file_path.name}: 列が見つかりません「{name}」（見出し: {header}）")
        idx[key] = header.index(name)

    rows = []
    for number, line in enumerate(lines[header_index + 1:], start=header_index + 2):
        if not any(cell.strip() for cell in line):
            continue

        def get(key):
            i = idx.get(key)
            return line[i].strip() if i is not None and i < len(line) else ""

        try:
            date = datetime.strptime(get("日付"), fmt["date_format"]).strftime("%Y-%m-%d")
        except ValueError:
            raise KessanError(f"{file_path.name} {number}行目: 日付「{get('日付')}」を読めません") from None
        balance = get("残高")
        rows.append({
            "日付": date,
            "入金": to_int(get("入金")),
            "出金": to_int(get("出金")),
            "摘要": get("摘要"),
            "残高": to_int(balance) if balance else None,
            "行番号": number,
        })
    return rows


def make_source_id(account_id, row, occurrence):
    balance = "" if row["残高"] is None else str(row["残高"])
    key = "|".join([account_id, row["日付"], str(row["入金"]), str(row["出金"]), row["摘要"], balance, str(occurrence)])
    return "bank:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _staging_row(account, file_name, row, source_id):
    staged = dict.fromkeys(STAGING_COLUMNS, "")
    subject, sub = account["科目"], account.get("補助", "")
    if row["入金"]:
        amount = str(row["入金"])
        staged.update({"借方科目": subject, "借方補助": sub, "借方金額": amount, "貸方金額": amount})
    else:
        amount = str(row["出金"])
        staged.update({"貸方科目": subject, "貸方補助": sub, "貸方金額": amount, "借方金額": amount})
    staged.update({
        "日付": row["日付"], "摘要": row["摘要"], "証憑ファイル": file_name, "取り込み元ID": source_id,
        "読み取り信頼度": "高", "判定": "要確認", "要確認理由": "相手科目未設定",
    })
    return staged


def import_bank(year_dir, sources_path, account_id, file_path, now=None):
    year_dir = Path(year_dir)
    sources = load_sources(sources_path)
    accounts_cfg = {a["id"]: a for a in sources.get("accounts", [])}
    if account_id not in accounts_cfg:
        raise KessanError(f"口座IDが設定ファイルにありません: {account_id}（登録済み: {list(accounts_cfg)}）")
    account = accounts_cfg[account_id]
    fmt = sources.get("formats", {}).get(account.get("format"))
    if fmt is None:
        raise KessanError(f"口座ID {account_id} の明細形式「{account.get('format')}」が設定ファイルにありません")

    rows = parse_statement(file_path, fmt)
    file_name = Path(file_path).name
    existing = {
        r["取り込み元ID"]
        for r in read_rows(year_dir / "journal.csv") + read_rows(year_dir / "staging.csv")
        if r.get("取り込み元ID")
    }

    seen = Counter()
    new_rows, new_balances = [], []
    duplicate = zero = 0
    for row in rows:
        key = (row["日付"], row["入金"], row["出金"], row["摘要"], row["残高"])
        occurrence = seen[key]
        seen[key] += 1
        if row["入金"] == 0 and row["出金"] == 0:
            zero += 1
            continue
        source_id = make_source_id(account_id, row, occurrence)
        if source_id in existing:
            duplicate += 1
            continue
        new_rows.append((row, _staging_row(account, file_name, row, source_id)))
        if row["残高"] is not None:
            new_balances.append({
                "科目": account["科目"], "補助": account.get("補助", ""),
                "日付": row["日付"], "残高": str(row["残高"]), "ファイル名": file_name,
            })

    if new_rows:
        append_rows(year_dir / "staging.csv", STAGING_COLUMNS, [staged for _, staged in new_rows])
        append_rows(year_dir / "statement-balances.csv", STATEMENT_BALANCE_COLUMNS, new_balances)
        dates = sorted(row["日付"] for row, _ in new_rows)
        append_rows(year_dir / "import-log.csv", IMPORT_LOG_COLUMNS, [{
            "取り込み日時": (now or datetime.now()).isoformat(timespec="seconds"),
            "ファイル名": file_name,
            "口座ID": account_id,
            "対象期間": f"{dates[0]}〜{dates[-1]}",
            "件数": str(len(new_rows)),
            "入金合計": str(sum(row["入金"] for row, _ in new_rows)),
            "出金合計": str(sum(row["出金"] for row, _ in new_rows)),
            "登録伝票番号範囲": "",
        }])
    return ImportResult(added=len(new_rows), duplicates=duplicate, zero_amount=zero)
