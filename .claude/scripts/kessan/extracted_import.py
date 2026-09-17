"""読み取り結果ファイル（extracted/*.json）を検証してから staging.csv に取り込む（import-extracted）。

- 形式に誤りがあるファイルは丸ごと取り込まず、ファイル名と理由を返す（他のファイルの取り込みは続ける）
- 同じ回の取り込みでは、明細（通帳・出納帳）を先に登録してから証憑（領収書・請求書）を突き合わせる
  （証憑を先に処理すると、まだ明細が無く突き合わせ候補が見つからないまま新しい仕訳を作ってしまう）
- 通帳・出納帳は銀行CSVと同じ共通部分（bank_import.stage_statement_rows）で取り込む。
  通帳の取り込み元IDは銀行CSVと同じ作り方なので、同じ取引をCSVと通帳の両方で受け取っても二重に入らない
- 領収書・請求書は、口座・カード・現金の明細行と突き合わせ、明細にあれば行に証憑として付け（evidence.csv）、
  無ければ支払方法から新しい仕訳の候補を作る（取り込み元ID=receipt:<証憑ID>）。請求書は支払方法が不明でも
  `receipt_default` を適用せず「未払候補」にする（銀行振込での支払と二重計上になりやすいため）
- 資料ファイル単位の再取り込みは import-log.csv で止める。同じ内容（日付・金額・取引先）の証憑が
  「別の」資料ファイルから来た場合は取り込みを止めず、証憑IDに -2・-3 …を付けて取り込み、重複の疑いを付記する
- 取り込んだ資料は import-log.csv に「資料」のパス（inbox/…）で記録し、同じ資料の再取り込みは件数0にする
- import-log.csv の書き込みだけが失敗した後の再実行は「再開」として扱う：evidence.csv に既にこの資料の
  証憑行があれば、突き合わせをやり直さず（自分自身が「既存の証憑」に見えて -2 が付いてしまうため）
  import-log.csv の記録だけを埋める（件数は必ず0）
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from bank_import import load_sources, normalize_description, stage_statement_rows
from common import (
    IMPORT_LOG_COLUMNS, STAGING_COLUMNS, KessanError, append_rows, cannot_write, ensure_writable, load_period,
    project, read_rows, replace_rows,
)
from evidence import (
    CANDIDATE_SEPARATOR, EVIDENCE_FILE, STATE_MATCHED, STATE_MULTIPLE, STATE_NEW_ENTRY, STATE_UNPAID,
    append_evidence, attached_source_ids, make_evidence_id, read_evidence, receipt_source_id, unique_evidence_id,
)
from extracted import check_passbook_pages, extracted_files, load_document, validate_document
from match import (
    MATCHED_RECEIPT, SUSPECTED_DUPLICATE_RECEIPT, credit_for_new_entry, find_candidates, payment_accounts,
    receipt_default,
)

PAGE_NOT_CHAINED = "ページの残高が連続しない"
STATEMENT_KINDS = ("通帳", "出納帳")  # この回の取り込みでは、証憑（領収書・請求書）より先に処理する


@dataclass
class ExtractedImportResult:
    imported: list = field(default_factory=list)          # 取り込んだ読み取り結果ファイル名
    already_imported: list = field(default_factory=list)  # 資料が取り込み済みのため件数0だったファイル名
    rejected: dict = field(default_factory=dict)          # 形式エラーで取り込まなかったファイル名 → 理由の一覧
    unreadable: list = field(default_factory=list)        # 種類=読めない の資料（inbox/…）
    added: int = 0
    duplicates: int = 0
    zero_amount: int = 0
    low_confidence_pages: list = field(default_factory=list)  # 「inbox/… ページN」
    overlapping_imports: list = field(default_factory=list)
    evidence: Counter = field(default_factory=Counter)        # 証憑の状態（明細に対応／新規仕訳／複数候補／未払候補）→ 件数
    receipt_duplicates: list = field(default_factory=list)    # (資料, 重複していた既存の証憑ID) の一覧。取り込みは止めない
    resumed: list = field(default_factory=list)               # import-log.csv の書き込み失敗後の再実行で、記録だけ埋めた資料

    def add(self, r):
        self.added += r.added
        self.duplicates += r.duplicates
        self.zero_amount += r.zero_amount
        self.overlapping_imports += r.overlapping_imports


def _statement_row(r):
    return {"日付": r["日付"], "入金": r["入金"], "出金": r["出金"],
            "摘要": normalize_description(r["摘要"]), "残高": r.get("残高")}


def _import_passbook(year_dir, data, source_accounts, result, now):
    broken = set(check_passbook_pages(data["ページ"]))
    rows = []
    for page in data["ページ"]:
        for r in page["行"]:
            row = _statement_row(r)
            if page["ページ番号"] in broken:
                row.update({"読み取り信頼度": "低", "要確認理由": PAGE_NOT_CHAINED})
            rows.append(row)
    result.low_confidence_pages += [f"{data['資料']} ページ{n}" for n in sorted(broken)]
    account = source_accounts[data["口座ID"]]
    result.add(stage_statement_rows(
        year_dir, data["口座ID"], account["科目"], account.get("補助", ""), data["資料"], rows,
        log_id=data["口座ID"], now=now, always_log=True,
    ))


def _import_cash_book(year_dir, data, result, now):
    subject, sub = data["科目"], data.get("補助", "")
    log_id = f"出納帳:{subject}" + (f"（{sub}）" if sub else "")
    result.add(stage_statement_rows(
        year_dir, f"{subject}|{sub}", subject, sub, data["資料"], [_statement_row(r) for r in data["行"]],
        log_id=log_id, now=now, always_log=True,
    ))


def _receipt_log_row(data, added, stamp):
    return {
        "取り込み日時": stamp, "ファイル名": data["資料"], "口座ID": data["種類"],
        "対象期間": f"{data['日付']}〜{data['日付']}", "件数": str(added),
        "入金合計": "0", "出金合計": str(data["金額"] if added else 0), "登録伝票番号範囲": "",
    }


def _evidence_row(data, evidence_id, state, source_id, stamp):
    return {
        "証憑ID": evidence_id, "証憑ファイル": data["資料"], "種類": data["種類"], "日付": data["日付"],
        "金額": str(data["金額"]), "取引先": data["取引先"], "内容": data["内容"],
        "科目候補": data["科目候補"], "補助候補": data.get("補助候補", ""),
        "状態": state, "取り込み元ID": source_id, "取り込み日時": stamp,
    }


def _write_failure(done, failed):
    return KessanError(
        f"{done} は更新済み、{failed} の書き込みに失敗しました"
        "（Excelで開いていたら閉じてから再実行してください。再実行しても二重には登録されません）"
    )


def _import_receipt(year_dir, data, sources, result, now):
    """証憑1件を取り込む。書き込みの順序：staging.csv → evidence.csv → import-log.csv。

    途中で止まって再実行しても、receipt:<証憑ID> の行が staging.csv・journal.csv にあれば
    新しい行を作らず、evidence.csv に無ければ改めて記録するだけなので、二重にならない。
    同じ資料ファイルの再取り込みは呼び出し元（import_extracted）が import-log.csv で止める。
    ここで止めるのは「同じ資料の再取り込み」だけで、日付・金額・取引先が同じ証憑が「別の」資料
    から来た場合は取り込みを止めず、証憑IDに -2・-3 …を付けて取り込み、要確認理由に重複の疑いを付記する。

    ただし、evidence.csv にこの資料（data["資料"]）の証憑行が既にある場合は「再開」として扱い、
    突き合わせをやり直さない：staging.csv・evidence.csv は前回の実行で書き終わっていて
    import-log.csv だけが書けなかったケースであり、ここで改めて突き合わせると、自分自身の証憑IDが
    「別の資料からの同一内容」に見えてしまい、-2 が付いた証憑・仕訳をもう一つ作ってしまう（二重取り込み）。
    """
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    evidence_rows = read_evidence(year_dir)
    names = ("staging.csv", EVIDENCE_FILE, "import-log.csv")

    if any(r["証憑ファイル"] == data["資料"] for r in evidence_rows):
        ensure_writable(year_dir, names)
        try:
            append_rows(year_dir / "import-log.csv", IMPORT_LOG_COLUMNS, [_receipt_log_row(data, 0, stamp)])
        except OSError:
            raise _write_failure("staging.csv・evidence.csv", "import-log.csv") from None
        result.resumed.append(data["資料"])
        return

    raw_evidence_id = make_evidence_id(data["日付"], data["金額"], data["取引先"])
    evidence_id, duplicate_of = unique_evidence_id(evidence_rows, raw_evidence_id)
    duplicate_reason = f"{SUSPECTED_DUPLICATE_RECEIPT}（証憑ID {duplicate_of}）" if duplicate_of else None

    staging = read_rows(year_dir / "staging.csv")
    lines = ([{**r, "_file": "journal.csv"} for r in read_rows(year_dir / "journal.csv")]
             + [{**r, "_file": "staging.csv"} for r in staging])
    accounts_for_payment = payment_accounts(sources)
    candidates = find_candidates(lines, data, accounts_for_payment, attached_source_ids(evidence_rows))
    suspected = [r["証憑ID"] for r in evidence_rows
                 if r["日付"] == data["日付"] and r["金額"] == str(data["金額"]) and r["証憑ID"] != duplicate_of]

    staging_changed = False
    new_row = None
    if len(candidates) == 1:
        state, source_id = STATE_MATCHED, candidates[0]
        for r in staging:
            if r["取り込み元ID"].strip() != source_id or r["承認"].strip() == "済" or r["借方科目"].strip():
                continue
            r["借方科目"] = data["科目候補"]
            r["借方補助"] = data.get("補助候補", "") if data["科目候補"] else ""
            r["取引先"] = r["取引先"] or data["取引先"]
            reason = MATCHED_RECEIPT
            if duplicate_reason:
                reason = f"{reason}／{duplicate_reason}"
            r["要確認理由"] = reason
            if data["自信度"] == "低":
                r["読み取り信頼度"] = "低"
            staging_changed = True
    elif len(candidates) > 1:
        state, source_id = STATE_MULTIPLE, CANDIDATE_SEPARATOR.join(candidates)
    else:
        credit = credit_for_new_entry(data["種類"], data["支払方法の推定"], receipt_default(sources), accounts_for_payment)
        if credit is None:
            state, source_id = STATE_UNPAID, ""
        else:
            state, source_id = STATE_NEW_ENTRY, receipt_source_id(evidence_id)
            if source_id not in {r["取り込み元ID"].strip() for r in lines}:
                subject, sub, reason = credit
                if suspected:
                    reason = f"{reason}／{SUSPECTED_DUPLICATE_RECEIPT}（証憑ID {'・'.join(suspected)} と日付・金額が一致）"
                if duplicate_reason:
                    reason = f"{reason}／{duplicate_reason}"
                amount = str(data["金額"])
                new_row = dict.fromkeys(STAGING_COLUMNS, "")
                new_row.update({
                    "日付": data["日付"],
                    "借方科目": data["科目候補"], "借方補助": data.get("補助候補", "") if data["科目候補"] else "",
                    "借方金額": amount, "貸方科目": subject, "貸方補助": sub, "貸方金額": amount,
                    "摘要": data["内容"], "取引先": data["取引先"], "証憑ファイル": data["資料"], "取り込み元ID": source_id,
                    "読み取り信頼度": data["自信度"], "判定": "要確認", "要確認理由": reason,
                })

    ensure_writable(year_dir, names)
    try:
        if staging_changed:
            replace_rows(year_dir / "staging.csv", STAGING_COLUMNS, [project(r, STAGING_COLUMNS) for r in staging])
        if new_row:
            append_rows(year_dir / "staging.csv", STAGING_COLUMNS, [new_row])
    except OSError:
        raise cannot_write("staging.csv") from None

    try:
        append_evidence(year_dir, [_evidence_row(data, evidence_id, state, source_id, stamp)])
    except OSError:
        raise _write_failure("staging.csv", "evidence.csv") from None

    try:
        append_rows(year_dir / "import-log.csv", IMPORT_LOG_COLUMNS, [_receipt_log_row(data, 1 if new_row else 0, stamp)])
    except OSError:
        raise _write_failure("staging.csv・evidence.csv", "import-log.csv") from None

    result.evidence[state] += 1
    if new_row:
        result.added += 1
    if duplicate_of:
        result.receipt_duplicates.append((data["資料"], duplicate_of))


def import_extracted(year_dir, sources_path, accounts, files, now=None):
    year_dir = Path(year_dir)
    sources = load_sources(sources_path)
    receipt_default(sources)  # 設定の誤りは、どのファイルも取り込む前に止める
    source_accounts = {a["id"]: a for a in sources.get("accounts") or []}
    period = load_period(year_dir)
    result = ExtractedImportResult()

    entries = []
    for path in files:
        path = Path(path)
        data, errors = load_document(path)
        if not errors:
            errors = validate_document(data, accounts, set(source_accounts), period, year_dir)
        if errors:
            result.rejected[path.name] = errors
            continue
        if data["種類"] == "読めない":
            result.unreadable.append(data["資料"])
            continue
        entries.append((path, data))

    # 明細（通帳・出納帳）を先に、証憑（領収書・請求書）を後に処理する（各グループ内は元の順序のまま）。
    # 逆だと、証憑が明細より先に処理されて突き合わせ候補が見つからず、新しい仕訳を余分に作ってしまう。
    ordered = ([e for e in entries if e[1]["種類"] in STATEMENT_KINDS]
               + [e for e in entries if e[1]["種類"] not in STATEMENT_KINDS])

    for path, data in ordered:
        if data["資料"] in {r["ファイル名"] for r in read_rows(year_dir / "import-log.csv")}:
            result.already_imported.append(path.name)
            continue
        if data["種類"] == "通帳":
            _import_passbook(year_dir, data, source_accounts, result, now)
        elif data["種類"] == "出納帳":
            _import_cash_book(year_dir, data, result, now)
        else:
            _import_receipt(year_dir, data, sources, result, now)
        result.imported.append(path.name)
    return result


def inbox_status(year_dir):
    """inbox/ の資料ごとに (inbox/…, 状態) を返す。状態：取り込み済み／読めない／読み取り済み・未取り込み／未処理。"""
    year_dir = Path(year_dir)
    logged = {r["ファイル名"] for r in read_rows(year_dir / "import-log.csv")}
    kinds = {}
    for path in extracted_files(year_dir):
        data, errors = load_document(path)
        if not errors and isinstance(data, dict) and isinstance(data.get("資料"), str):
            kinds[data["資料"]] = data.get("種類")
    inbox = year_dir / "inbox"
    statuses = []
    for path in sorted(p for p in inbox.rglob("*") if p.is_file() and not p.name.startswith(".")):
        rel = "inbox/" + path.relative_to(inbox).as_posix()
        if rel in logged or path.name in logged:  # 銀行CSV（import-bank）はファイル名だけで記録されている
            status = "取り込み済み"
        elif kinds.get(rel) == "読めない":
            status = "読めない"
        elif rel in kinds:
            status = "読み取り済み・未取り込み"
        else:
            status = "未処理"
        statuses.append((rel, status))
    return statuses
