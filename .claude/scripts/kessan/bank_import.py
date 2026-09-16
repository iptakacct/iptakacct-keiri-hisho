"""銀行・カードのCSV明細を staging.csv に取り込む。

- 預金側の科目だけを埋め、相手科目は空のまま「要確認」にする（相手科目は段階2でAIが候補を付ける）
- 取り込み元ID（明細の内容から作るハッシュ）で、登録済み・取り込み済みの行を二重に取り込まない
- 明細に残高があれば statement-balances.csv に記録する（検算で帳簿残高と照合する）
- 新しい順に並んだ明細は、残高の連続から判定して古い順に並べ替えてから取り込む
"""
import csv
import hashlib
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from common import (
    IMPORT_LOG_COLUMNS, STAGING_COLUMNS, STATEMENT_BALANCE_COLUMNS,
    KessanError, append_rows, load_period, parse_amount, read_rows,
)

SUSPECTED_DOUBLE_IMPORT = "取り込み済みの明細と日付・金額が一致（二重取り込みの疑い）"


@dataclass(frozen=True)
class ImportResult:
    added: int
    duplicates: int
    zero_amount: int
    out_of_period: int = 0
    overlapping_imports: list = field(default_factory=list)


def load_sources(path):
    path = Path(path)
    if not path.exists():
        raise KessanError(f"口座・明細形式の設定ファイルがありません: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise KessanError(f"口座・明細形式の設定ファイルを読めません（{path.name}）: {e}") from None
    if not isinstance(data, dict):
        raise KessanError(f"口座・明細形式の設定ファイルの形式が不正です（{path.name}）")
    return data


def _check_format(name, fmt):
    if not isinstance(fmt, dict):
        raise KessanError(f"明細形式「{name}」の書き方が不正です")
    for key in ("columns", "date_format"):
        if key not in fmt:
            raise KessanError(f"明細形式「{name}」に {key} がありません（kessan-sources.yaml を確認）")
    columns = fmt["columns"]
    if not isinstance(columns, dict) or "日付" not in columns:
        raise KessanError(f"明細形式「{name}」の columns に 日付 がありません")
    if "入金" not in columns and "出金" not in columns:
        raise KessanError(f"明細形式「{name}」の columns に 入金・出金 のどちらもありません")


def parse_statement(file_path, fmt):
    file_path = Path(file_path)
    if not file_path.exists():
        raise KessanError(f"明細ファイルがありません: {file_path}")
    encoding = fmt.get("encoding", "utf-8-sig")
    try:
        with file_path.open(encoding=encoding, newline="") as f:
            lines = list(csv.reader(f))
    except UnicodeDecodeError:
        raise KessanError(f"{file_path.name}: 文字コード {encoding} として読めません（kessan-sources.yaml の encoding を確認）") from None
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

        where = f"{file_path.name} {number}行目"
        deposit = parse_amount(get("入金"), where)
        withdrawal = parse_amount(get("出金"), where)
        # 入金・出金の両方の欄に金額がある行は読めない（符号の入れ替え前に判定する）
        if deposit and withdrawal:
            raise KessanError(f"{where}: 入金と出金の両方に金額があります（入金{deposit} 出金{withdrawal}）")
        # マイナスの入金は出金、マイナスの出金は入金として扱う（取消・返金の表記）
        deposit, withdrawal = max(deposit, 0) + max(-withdrawal, 0), max(withdrawal, 0) + max(-deposit, 0)

        balance_str = get("残高")
        balance = parse_amount(balance_str, where) if balance_str else None

        rows.append({
            "日付": date,
            "入金": deposit,
            "出金": withdrawal,
            # 半角カナ・全角英数などの表記ゆれで同じ明細が別物と判定されないよう正規化する
            "摘要": unicodedata.normalize("NFKC", get("摘要")).strip(),
            "残高": balance,
            "行番号": number,
        })
    return rows


def _chains(seq):
    return all(
        seq[i]["残高"] == seq[i - 1]["残高"] + seq[i]["入金"] - seq[i]["出金"]
        for i in range(1, len(seq))
    )


def order_oldest_first(rows, file_name):
    """全ての金額行に残高があれば、残高の連続から並び順を判定し、古い順にして返す。"""
    moving = [r for r in rows if r["入金"] or r["出金"]]
    if len(moving) < 2 or any(r["残高"] is None for r in moving):
        return rows
    if _chains(moving):
        return rows
    if _chains(moving[::-1]):
        return rows[::-1]
    number = None
    for prev, cur in zip(moving, moving[1:]):
        ascending = cur["残高"] == prev["残高"] + cur["入金"] - cur["出金"]
        descending = prev["残高"] == cur["残高"] + prev["入金"] - prev["出金"]
        if not ascending and not descending:
            number = cur["行番号"]
            break
    if number is None:  # 隣同士はどちらかの順で合うが、全体としてはどちらの順でも合わない（順序が混在）
        number = next(cur["行番号"] for prev, cur in zip(moving, moving[1:]) if not _chains([prev, cur]))
    raise KessanError(f"{file_name} {number}行目: 残高の連続が合いません（行の欠落・並び順を確認）")


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


def _existing_bank_lines(lines):
    """登録済み・取り込み済みの行を (側, 科目, 補助, 日付, 金額) → 取り込み元IDの集合 にする。"""
    index = defaultdict(set)
    for r in lines:
        for side in ("借方", "貸方"):
            name = r[f"{side}科目"].strip()
            if name:
                key = (side, name, r[f"{side}補助"].strip(), r["日付"].strip(),
                       parse_amount(r[f"{side}金額"], f"{r['_file']} {r['伝票番号'] or r['日付']}"))
                index[key].add(r["取り込み元ID"].strip())
    return index


def _overlapping_imports(log, account_id, rows):
    dates = [r["日付"] for r in rows if r["入金"] or r["出金"]]
    if not dates:
        return []
    low, high = min(dates), max(dates)
    found = []
    for r in log:
        if r["口座ID"] != account_id:
            continue
        period = r["対象期間"].split("〜")
        if len(period) != 2:
            continue
        if period[0] <= high and low <= period[1]:
            found.append(f"{r['ファイル名']} {r['対象期間']}")
    return found


def import_bank(year_dir, sources_path, account_id, file_path, now=None):
    year_dir = Path(year_dir)
    sources = load_sources(sources_path)
    accounts_cfg = {a["id"]: a for a in sources.get("accounts") or []}
    if account_id not in accounts_cfg:
        raise KessanError(f"口座IDが設定ファイルにありません: {account_id}（登録済み: {list(accounts_cfg)}）")
    account = accounts_cfg[account_id]
    fmt = (sources.get("formats") or {}).get(account.get("format"))
    if fmt is None:
        raise KessanError(f"口座ID {account_id} の明細形式「{account.get('format')}」が設定ファイルにありません")
    _check_format(account.get("format"), fmt)

    start, end = load_period(year_dir)
    file_name = Path(file_path).name
    rows = order_oldest_first(parse_statement(file_path, fmt), file_name)
    existing_lines = [{**r, "_file": name} for name in ("journal.csv", "staging.csv") for r in read_rows(year_dir / name)]
    existing = {r["取り込み元ID"] for r in existing_lines if r.get("取り込み元ID")}
    bank_lines = _existing_bank_lines(existing_lines)
    overlapping = _overlapping_imports(read_rows(year_dir / "import-log.csv"), account_id, rows)
    subject, sub = account["科目"], account.get("補助", "")

    seen = Counter()
    new_rows, new_balances = [], []
    duplicate = zero = out_of_period = 0
    for row in rows:
        key = (row["日付"], row["入金"], row["出金"], row["摘要"], row["残高"])
        occurrence = seen[key]
        seen[key] += 1
        if row["入金"] == 0 and row["出金"] == 0:
            zero += 1
            continue
        if not start <= row["日付"] <= end:
            out_of_period += 1
            continue
        source_id = make_source_id(account_id, row, occurrence)
        if source_id in existing:
            duplicate += 1
            continue
        staged = _staging_row(account, file_name, row, source_id)
        side = "借方" if row["入金"] else "貸方"
        match = bank_lines.get((side, subject, sub, row["日付"], row["入金"] or row["出金"]), set())
        if match - {source_id}:
            staged["要確認理由"] = SUSPECTED_DOUBLE_IMPORT
        new_rows.append((row, staged))
        if row["残高"] is not None:
            new_balances.append({
                "科目": subject, "補助": sub,
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
    return ImportResult(added=len(new_rows), duplicates=duplicate, zero_amount=zero,
                        out_of_period=out_of_period, overlapping_imports=overlapping)
