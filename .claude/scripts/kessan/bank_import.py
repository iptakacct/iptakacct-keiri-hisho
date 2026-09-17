"""銀行・カードのCSV明細を staging.csv に取り込む。通帳・出納帳の読み取り結果（extracted_import.py）もここの共通部分を使う。

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
from datetime import date, datetime
from pathlib import Path

import yaml

from common import (
    IMPORT_LOG_COLUMNS, STAGING_COLUMNS, STATEMENT_BALANCE_COLUMNS,
    KessanError, add_reasons, append_rows, cannot_write, discarded_source_ids, ensure_writable, load_period, parse_amount, parse_date,
    project, read_rows, remove_reason, replace_rows,
)
from evidence import STATE_NEW_ENTRY, STATE_UNPAID, read_evidence
from match import MATCH_WINDOW_DAYS

SUSPECTED_DOUBLE_IMPORT = "取り込み済みの明細と日付・金額が一致（二重取り込みの疑い）"
UNSET_COUNTER_ACCOUNT = "相手科目未設定"
SUSPECTED_DOUBLE_BOOKED_RECEIPT = "証憑から計上済みの仕訳と金額・日付が近い（二重計上の疑い）"
MATCHES_UNPAID_RECEIPT = "未払候補の証憑と金額が一致（支払の可能性）"
DOUBLE_BOOKED_ON_RECEIPT_ROW = "明細にも同額の支払がある（二重計上の疑い）"  # 証憑から作った行（receipt:）の側に付ける
WRITE_TARGETS = ("staging.csv", "statement-balances.csv", "import-log.csv")


@dataclass(frozen=True)
class ImportResult:
    added: int
    duplicates: int
    zero_amount: int
    out_of_period: int = 0
    overlapping_imports: list = field(default_factory=list)
    discarded: int = 0  # discard-log.csv にある（破棄済みの）ため入れなかった行


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


def normalize_description(text):
    """摘要の正規化。半角カナ・全角英数などの表記ゆれで同じ明細が別物と判定されないようにする。

    取り込み元IDの材料なので、CSV・通帳・出納帳のどれから取り込むときも必ずこれを通す。
    """
    return unicodedata.normalize("NFKC", str(text or "")).strip()


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
            "摘要": normalize_description(get("摘要")),
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


def _staging_row(subject, sub, file_name, row, source_id):
    staged = dict.fromkeys(STAGING_COLUMNS, "")
    if row["入金"]:
        amount = str(row["入金"])
        staged.update({"借方科目": subject, "借方補助": sub, "借方金額": amount, "貸方金額": amount})
    else:
        amount = str(row["出金"])
        staged.update({"貸方科目": subject, "貸方補助": sub, "貸方金額": amount, "借方金額": amount})
    staged.update({
        "日付": row["日付"], "摘要": row["摘要"], "証憑ファイル": file_name, "取り込み元ID": source_id,
        "読み取り信頼度": row.get("読み取り信頼度") or "高", "判定": "要確認",
        "要確認理由": row.get("要確認理由") or UNSET_COUNTER_ACCOUNT,
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


def _near_evidence(evidence_rows, target_date_str, amount):
    """evidence.csv のうち、金額が同じで日付が target_date_str の前後 MATCH_WINDOW_DAYS 日以内の行。"""
    target = date.fromisoformat(target_date_str)
    found = []
    for r in evidence_rows:
        if parse_amount(r["金額"], "evidence.csv") != amount:
            continue
        ev_date = parse_date(r["日付"])
        if ev_date is None:
            continue
        if abs((date.fromisoformat(ev_date) - target).days) <= MATCH_WINDOW_DAYS:
            found.append(r)
    return found


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


def stage_statement_rows(year_dir, source_key, subject, sub, file_name, rows, log_id, now=None, always_log=False):
    """古い順に並んだ明細の行を staging.csv に入れる（CSV・通帳・出納帳で共通）。

    rows: {日付, 入金, 出金, 摘要（normalize_description 済み）, 残高（無ければ None）} のリスト。
          行ごとに 読み取り信頼度・要確認理由 を持たせると、それを staging に書く（通帳のページ検算NGなど）
    source_key: 取り込み元IDの材料（口座ID。出納帳は「科目|補助」）
    log_id: import-log.csv の口座ID欄に書く値
    always_log: 追加が0件でも import-log.csv に記録する（読み取り結果ファイルを取り込み済みにする印）
    """
    year_dir = Path(year_dir)
    start, end = load_period(year_dir)
    staging_rows = read_rows(year_dir / "staging.csv")
    existing_lines = ([{**r, "_file": "journal.csv"} for r in read_rows(year_dir / "journal.csv")]
                      + [{**r, "_file": "staging.csv"} for r in staging_rows])
    existing = {r["取り込み元ID"] for r in existing_lines if r.get("取り込み元ID")}
    from_this_file = {r["取り込み元ID"] for r in existing_lines if r.get("取り込み元ID") and r["証憑ファイル"] == file_name}
    bank_lines = _existing_bank_lines(existing_lines)
    log = read_rows(year_dir / "import-log.csv")
    logged = file_name in {r["ファイル名"] for r in log}
    overlapping = _overlapping_imports(log, log_id, rows)
    recorded_balances = {(r["科目"], r["補助"], r["日付"], r["残高"], r["ファイル名"])
                         for r in read_rows(year_dir / "statement-balances.csv")}
    evidence_rows = read_evidence(year_dir)
    discarded_ids = discarded_source_ids(year_dir)

    seen = Counter()
    new_rows, new_balances = [], []
    resumed_rows = []          # 前回この資料から staging.csv に入れた行（書き込みが途中で止まった後の再実行）
    receipts_to_flag = set()   # 明細にも同額の支払があった、証憑から作った行の取り込み元ID
    duplicate = zero = out_of_period = discarded = 0

    def add_balance(row, only_if_missing=False):
        # 検算は日付ごとに最後の残高を使うので、新しい行の残高は重複に見えても順序どおりに全部書く。
        # 再実行で埋める残高（only_if_missing）だけは、既に記録済みのものを書かない
        key = (subject, sub, row["日付"], str(row["残高"]), file_name)
        if row["残高"] is not None and not (only_if_missing and key in recorded_balances):
            new_balances.append(dict(zip(STATEMENT_BALANCE_COLUMNS, key)))

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
        source_id = make_source_id(source_key, row, occurrence)
        if source_id in discarded_ids:  # オーナーが破棄した行は入れ直さない（他の行の要確認理由にも使わない）
            discarded += 1
            add_balance(row, only_if_missing=True)
            continue
        if source_id in existing:
            duplicate += 1
            if source_id in from_this_file:  # 残高の記録が書けずに止まった後の再実行なら、残高を埋める
                resumed_rows.append(row)
                add_balance(row, only_if_missing=True)
            continue
        staged = _staging_row(subject, sub, file_name, row, source_id)
        side = "借方" if row["入金"] else "貸方"
        match = bank_lines.get((side, subject, sub, row["日付"], row["入金"] or row["出金"]), set())
        if match - {source_id}:  # より強い理由を付けるときは「相手科目未設定」の部分だけ外す（他の理由は残す）
            staged["要確認理由"] = add_reasons(remove_reason(staged["要確認理由"], UNSET_COUNTER_ACCOUNT),
                                             SUSPECTED_DOUBLE_IMPORT)
        if row["出金"]:  # 支払側（出金）の新しい行は、対応しそうな証憑が無いか確認する
            near = _near_evidence(evidence_rows, row["日付"], row["出金"])
            extra_reasons = []
            new_entries = [r for r in near if r["状態"] == STATE_NEW_ENTRY]
            if new_entries:
                extra_reasons.append(SUSPECTED_DOUBLE_BOOKED_RECEIPT)
                receipts_to_flag.update(r["取り込み元ID"].strip() for r in new_entries)
            if any(r["状態"] == STATE_UNPAID for r in near):
                extra_reasons.append(MATCHES_UNPAID_RECEIPT)
            staged["要確認理由"] = add_reasons(staged["要確認理由"], *extra_reasons)
        new_rows.append((row, staged))
        add_balance(row)

    receipt_rows = [r for r in staging_rows
                    if r["取り込み元ID"].strip() in receipts_to_flag and r["取り込み元ID"].strip().startswith("receipt:")
                    and r["承認"].strip() != "済"]
    for r in receipt_rows:
        r["要確認理由"] = add_reasons(r["要確認理由"], DOUBLE_BOOKED_ON_RECEIPT_ROW)

    log_rows = [row for row, _ in new_rows]
    write_log = bool(new_rows) or always_log
    if not new_rows and resumed_rows and not logged:  # 取り込み記録が書けずに止まった後の再実行
        log_rows, write_log = resumed_rows, True
    if not (new_rows or new_balances or write_log):
        return ImportResult(added=0, duplicates=duplicate, zero_amount=zero, out_of_period=out_of_period,
                            overlapping_imports=overlapping, discarded=discarded)

    ensure_writable(year_dir, WRITE_TARGETS)
    done = []
    try:
        if new_rows and receipt_rows:
            replace_rows(year_dir / "staging.csv", STAGING_COLUMNS,
                         [project(r, STAGING_COLUMNS) for r in staging_rows] + [staged for _, staged in new_rows])
        elif new_rows:
            append_rows(year_dir / "staging.csv", STAGING_COLUMNS, [staged for _, staged in new_rows])
    except OSError:
        raise cannot_write("staging.csv") from None
    if new_rows:
        done.append("staging.csv")
    later_writes = []
    if new_balances:
        later_writes.append(("statement-balances.csv", STATEMENT_BALANCE_COLUMNS, new_balances))
    if write_log:
        later_writes.append(("import-log.csv", IMPORT_LOG_COLUMNS, [_log_row(log_rows, rows, file_name, log_id, now)]))
    for name, columns, lines in later_writes:
        try:
            append_rows(year_dir / name, columns, lines)
        except OSError:
            if not done:
                raise cannot_write(name) from None
            raise KessanError(
                f"{'・'.join(done)} は更新済み、{name} の書き込みに失敗しました（Excelで開いていたら閉じてから再実行してください。"
                "再実行しても二重には取り込まず、残高の記録・取り込み記録を埋めます）"
            ) from None
        done.append(name)
    return ImportResult(added=len(new_rows), duplicates=duplicate, zero_amount=zero, out_of_period=out_of_period,
                        overlapping_imports=overlapping, discarded=discarded)


def _log_row(log_rows, all_rows, file_name, log_id, now):
    dates = sorted(row["日付"] for row in log_rows) or sorted(r["日付"] for r in all_rows if r["入金"] or r["出金"])
    return {
        "取り込み日時": (now or datetime.now()).isoformat(timespec="seconds"),
        "ファイル名": file_name,
        "口座ID": log_id,
        "対象期間": f"{dates[0]}〜{dates[-1]}" if dates else "",
        "件数": str(len(log_rows)),
        "入金合計": str(sum(row["入金"] for row in log_rows)),
        "出金合計": str(sum(row["出金"] for row in log_rows)),
        "登録伝票番号範囲": "",
    }


def recorded_file_name(year_dir, file_path):
    """取り込んだ資料を記録する名前。年度フォルダの inbox/ の中なら「inbox/…」（/ 区切り）、外ならファイル名だけ。

    import-extracted の「資料」と同じ形にそろえ、unimport で同じファイル名の別の資料と取り違えないようにする。
    """
    path = Path(file_path)
    try:
        relative = path.resolve().relative_to((Path(year_dir) / "inbox").resolve())
    except (OSError, ValueError):
        return path.name
    return "inbox/" + relative.as_posix()


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

    load_period(year_dir)  # 年度フォルダの期間設定が無ければ、明細を読む前に止める
    file_name = recorded_file_name(year_dir, file_path)
    rows = order_oldest_first(parse_statement(file_path, fmt), Path(file_path).name)
    return stage_statement_rows(year_dir, account_id, account["科目"], account.get("補助", ""), file_name, rows,
                                log_id=account_id, now=now)
