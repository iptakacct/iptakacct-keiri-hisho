"""取り込みの取り消し（unimport）と、未承認行の破棄（discard）。

- どちらも、全部を検証してから書く（1つでも拒否する理由があれば何も変えない）。書く前に対象のCSVが
  書き込めるか確かめ（ensure_writable）、ファイルごとに一時ファイル経由で置き換える（replace_rows）
- 承認済み（承認=済）の行・帳簿に登録済み（journal.csv にある）の行には触らない
- unimport：読み違えた資料1件分の取り込みを丸ごと取り消す。読み取り結果を直してから取り込み直せる
- discard：オーナーが確認した重複など、不要な未承認行を取り込み元IDで捨てる。discard-log.csv に記録を残す
"""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bank_import import UNSET_COUNTER_ACCOUNT
from common import (
    DISCARD_LOG, DISCARD_LOG_COLUMNS, EVIDENCE_COLUMNS, IMPORT_LOG_COLUMNS, STAGING_COLUMNS, STATEMENT_BALANCE_COLUMNS,
    KessanError, add_reasons, append_rows, ensure_writable, parse_amount, project, read_rows, remove_reason,
    replace_rows,
)
from evidence import EVIDENCE_FILE, STATE_DISCARDED, STATE_MATCHED
from match import MATCHED_RECEIPT

IMPORTED_ID_PREFIXES = ("bank:", "receipt:")  # 取り込みで作った行（manual: 等の手入力の行は対象外）


@dataclass(frozen=True)
class UnimportResult:
    sources: list      # 取り消した資料の記録名（inbox/… または銀行CSVのファイル名）
    staging: int       # staging.csv から消した行数
    balances: int      # statement-balances.csv から消した行数
    evidence: int      # evidence.csv から消した行数
    import_log: int    # import-log.csv から消した行数
    removed_ids: list  # staging.csv から消した行の取り込み元ID
    restored: list     # 証憑の科目候補を外して元に戻した明細行の取り込み元ID


def _normalize_source(source):
    """--source を記録と同じ形にする（年度フォルダからのパスや \\ 区切りは「inbox/…」に直す）。"""
    s = str(source).replace("\\", "/").strip()
    if "/inbox/" in "/" + s:
        s = "inbox/" + ("/" + s).rsplit("/inbox/", 1)[1]
    return s


def _matching_names(source, values):
    """記録に残っている資料名（values）のうち、--source が指すもの（1件）。

    記録名と完全に一致するものだけを指す。フォルダを含まない指定（例：2025-04.csv）は、ファイル名が
    それと同じ記録が1件だけならそれを指し、複数あれば取り違えないよう候補を挙げて止める。
    """
    path = _normalize_source(source)
    recorded = {v.strip() for v in values if v.strip()}
    if "/" not in path:
        found = sorted(v for v in recorded if v.rsplit("/", 1)[-1] == path)
        if len(found) > 1:
            raise KessanError(f"{source} に当てはまる資料が複数あります（{'、'.join(found)}）。"
                              "記録どおりのパス（inbox/…）で指定してください（何も変更していません）")
        return set(found)
    return {path} if path in recorded else set()


def _is_imported(row):
    return row["取り込み元ID"].strip().startswith(IMPORTED_ID_PREFIXES)


def _write_all(year_dir, writes):
    """writes：[(ファイル名, 列, 行)] を順に置き換える。途中で失敗したら、どこまで書いたかを伝える。"""
    done = []
    for name, columns, rows in writes:
        try:
            replace_rows(Path(year_dir) / name, columns, [project(r, columns) for r in rows])
        except OSError:
            written = "・".join(done) + " は更新済み、" if done else ""
            raise KessanError(
                f"{written}{name} の書き込みに失敗しました（Excelで開いていたら閉じてから、同じコマンドを再実行してください）"
            ) from None
        done.append(name)


def unimport(year_dir, source):
    """資料1件分の取り込みを取り消す。書き込みの順序：staging.csv → evidence.csv → statement-balances.csv → import-log.csv。

    import-log.csv を最後にするので、途中で止まっても資料は「取り込み済み」のまま残り、再実行で続きを消せる。
    """
    year_dir = Path(year_dir)
    staging = read_rows(year_dir / "staging.csv")
    journal = read_rows(year_dir / "journal.csv")
    balances = read_rows(year_dir / "statement-balances.csv")
    evidence = read_rows(year_dir / EVIDENCE_FILE)
    log = read_rows(year_dir / "import-log.csv")

    values = ([r["証憑ファイル"] for r in staging if _is_imported(r)] + [r["ファイル名"] for r in balances]
              + [r["証憑ファイル"] for r in evidence] + [r["ファイル名"] for r in log]
              + [r["証憑ファイル"] for r in journal if _is_imported(r)])
    names = _matching_names(source, values)
    if not names:
        raise KessanError(f"{source} は取り込まれていません（staging.csv・evidence.csv・import-log.csv に記録がありません）")

    removed = [r for r in staging if _is_imported(r) and r["証憑ファイル"].strip() in names]
    removed_keys = {id(r) for r in removed}
    removed_ids = list(dict.fromkeys(r["取り込み元ID"].strip() for r in removed))
    own_evidence = [r for r in evidence if r["証憑ファイル"].strip() in names]
    matched = [r for r in own_evidence if r["状態"] == STATE_MATCHED]
    matched_ids = {r["取り込み元ID"].strip() for r in matched}
    new_entry_ids = {r["取り込み元ID"].strip() for r in own_evidence if r["取り込み元ID"].strip().startswith("receipt:")}

    errors = []
    approved = sorted({r["取り込み元ID"].strip() for r in staging
                       if r["承認"].strip() == "済" and (id(r) in removed_keys or r["取り込み元ID"].strip() in matched_ids)})
    if approved:
        errors.append(f"承認済みの行があります: {'、'.join(approved)}"
                      "（取り消すには、確認用Excelの修正メモ欄に書いて apply-review で承認を外してから）")
    journal_ids = {r["取り込み元ID"].strip() for r in journal}
    posted = sorted(({r["取り込み元ID"].strip() for r in journal if _is_imported(r) and r["証憑ファイル"].strip() in names}
                     | ((set(removed_ids) | matched_ids | new_entry_ids) & journal_ids)) - {""})
    if posted:
        errors.append(f"帳簿（journal.csv）に登録済みの行があります: {'、'.join(posted)}（登録済みの行は取り消せません）")
    attached = sorted({r["証憑ファイル"] for r in evidence
                       if r["状態"] == STATE_MATCHED and r["証憑ファイル"].strip() not in names
                       and r["取り込み元ID"].strip() in removed_ids})
    if attached:
        errors.append(f"この資料の明細行に別の証憑が付いています: {'、'.join(attached)}"
                      "（先にその証憑を unimport してから、この資料を取り消してください）")
    if errors:
        raise KessanError(f"{source} の取り込みを取り消せません（何も変更していません）:\n" + "\n".join(errors))

    restored = []
    for ev in matched:
        for r in staging:
            if r["取り込み元ID"].strip() != ev["取り込み元ID"].strip() or id(r) in removed_keys:
                continue
            reasons = remove_reason(r["要確認理由"], MATCHED_RECEIPT)
            current = (r["借方科目"].strip(), r["借方補助"].strip(), r["取引先"].strip())
            if current == (ev["科目候補"].strip(), ev["補助候補"].strip(), ev["取引先"].strip()):
                r.update({"借方科目": "", "借方補助": "", "取引先": ""})
                reasons = add_reasons(reasons, UNSET_COUNTER_ACCOUNT)
                restored.append(r["取り込み元ID"].strip())
            r["要確認理由"] = reasons

    own_keys = {id(r) for r in own_evidence}
    kept_staging = [r for r in staging if id(r) not in removed_keys]
    kept_evidence = [r for r in evidence if id(r) not in own_keys]
    kept_balances = [r for r in balances if r["ファイル名"].strip() not in names]
    kept_log = [r for r in log if r["ファイル名"].strip() not in names]
    targets = ("staging.csv", EVIDENCE_FILE, "statement-balances.csv", "import-log.csv")
    ensure_writable(year_dir, targets)
    _write_all(year_dir, [
        ("staging.csv", STAGING_COLUMNS, kept_staging),
        (EVIDENCE_FILE, EVIDENCE_COLUMNS, kept_evidence),
        ("statement-balances.csv", STATEMENT_BALANCE_COLUMNS, kept_balances),
        ("import-log.csv", IMPORT_LOG_COLUMNS, kept_log),
    ])
    return UnimportResult(
        sources=sorted(names), staging=len(removed), balances=len(balances) - len(kept_balances),
        evidence=len(own_evidence), import_log=len(log) - len(kept_log), removed_ids=removed_ids, restored=restored,
    )


def discard(year_dir, source_ids, reason, now=None):
    """未承認の staging.csv の行を取り込み元IDで破棄し、discard-log.csv に記録する。破棄した行数を返す。

    書き込みの順序：discard-log.csv → evidence.csv → staging.csv（記録を先に残す。途中で止まっても、
    行が staging.csv に残っていれば再実行で破棄できる）。
    """
    year_dir = Path(year_dir)
    ids = list(dict.fromkeys(str(s).strip() for s in source_ids if str(s).strip()))
    if not ids:
        raise KessanError("破棄する取り込み元IDがありません")
    reason = str(reason or "").strip()
    if not reason:
        raise KessanError("破棄の理由を書いてください（--reason。例：オーナー確認済みの二重取り込み）")
    staging = read_rows(year_dir / "staging.csv")
    journal_ids = {r["取り込み元ID"].strip() for r in read_rows(year_dir / "journal.csv")}
    evidence = read_rows(year_dir / EVIDENCE_FILE)

    errors = []
    for source_id in ids:
        where = f"取り込み元ID {source_id}"
        rows = [r for r in staging if r["取り込み元ID"].strip() == source_id]
        if source_id in journal_ids:
            errors.append(f"{where}: 帳簿（journal.csv）に登録済みのため破棄できません")
        elif not rows:
            errors.append(f"{where}: staging.csv にありません")
        elif any(r["承認"].strip() == "済" for r in rows):
            errors.append(f"{where}: 承認済みの行は破棄できません（確認用Excelの修正メモで承認を外してから）")
        attached = sorted({r["証憑ファイル"] for r in evidence
                           if r["状態"] == STATE_MATCHED and r["取り込み元ID"].strip() == source_id})
        if attached:
            errors.append(f"{where}: 証憑（{'、'.join(attached)}）が付いた明細行は破棄できません（先に証憑を unimport してから）")
    if errors:
        raise KessanError("破棄できません（何も変更していません）:\n" + "\n".join(errors))

    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    wanted = set(ids)
    removed = [r for r in staging if r["取り込み元ID"].strip() in wanted]
    records = []
    for r in removed:
        where = f"staging.csv 取り込み元ID {r['取り込み元ID']}"
        amount = max(parse_amount(r["借方金額"], where), parse_amount(r["貸方金額"], where))
        records.append({"日時": stamp, "取り込み元ID": r["取り込み元ID"].strip(), "日付": r["日付"].strip(),
                        "金額": str(amount), "摘要": r["摘要"], "理由": reason})
    evidence_changed = False
    for ev in evidence:
        source_id = ev["取り込み元ID"].strip()
        if source_id.startswith("receipt:") and source_id in wanted and ev["状態"] != STATE_DISCARDED:
            ev["状態"] = STATE_DISCARDED
            evidence_changed = True

    ensure_writable(year_dir, (DISCARD_LOG, EVIDENCE_FILE, "staging.csv"))
    try:
        append_rows(year_dir / DISCARD_LOG, DISCARD_LOG_COLUMNS, records)
    except OSError:
        raise KessanError(f"{DISCARD_LOG} の書き込みに失敗しました（何も破棄していません。閉じてから再実行）") from None
    writes = [(EVIDENCE_FILE, EVIDENCE_COLUMNS, evidence)] if evidence_changed else []
    writes.append(("staging.csv", STAGING_COLUMNS, [r for r in staging if r["取り込み元ID"].strip() not in wanted]))
    try:
        _write_all(year_dir, writes)
    except KessanError as e:
        raise KessanError(f"{DISCARD_LOG} は記録済み、{e}") from None
    return len(removed)
