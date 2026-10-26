"""証憑の突き合わせの解決（resolve-evidence）。複数候補・未払候補・明細に該当なしの証憑を、1件ずつ決める。

- match：複数候補の証憑を、候補のうちの1つの明細行に決める（明細行に証憑を付ける処理は import-extracted と同じ）
- as_payable：決算整理で未払として扱う（未払候補にする）
- as_new：証憑から新しい仕訳の候補を staging に作る（支払方法が決まらないものは作れない）
書き込みの順序は import-extracted と同じ（staging.csv → evidence.csv）。
"""
from dataclasses import dataclass
from pathlib import Path

from common import (
    EVIDENCE_COLUMNS, STAGING_COLUMNS, KessanError, append_rows, cannot_write, discarded_source_ids, ensure_writable,
    project, read_rows, replace_rows,
)
from evidence import (
    EVIDENCE_FILE, STATE_MATCHED, STATE_MULTIPLE, STATE_NEW_ENTRY, STATE_UNPAID, candidate_ids,
    known_source_ids, read_evidence, receipt_source_id,
)
from extracted_import import attach_receipt_to_row, can_attach_receipt, new_entry_row
from match import credit_for_new_entry, load_abbreviations, payment_accounts, receipt_default


@dataclass(frozen=True)
class ResolveResult:
    state: str      # 新しい状態
    attached: bool  # --match で明細行に科目・取引先を付けた（付けなかったときは False。--match 以外は常に False）

    @property
    def message(self):
        if self.state == STATE_MATCHED:
            return (f"{self.state}（科目を付与）" if self.attached
                    else f"{self.state}（付与なし：承認済み・科目入力済み・登録済み）")
        return self.state


def _document(ev):
    """evidence.csv の行を、取り込み時の読み取り結果と同じ形にする（突き合わせ・新規仕訳の共通処理に渡す）。"""
    try:
        amount = int(ev["金額"])
    except ValueError:
        raise KessanError(f"証憑 {ev['証憑ID']} の金額「{ev['金額']}」が数字ではない（evidence.csv を確認）") from None
    return {
        "資料": ev["証憑ファイル"], "種類": ev["種類"], "日付": ev["日付"], "金額": amount,
        "取引先": ev["取引先"], "内容": ev["内容"], "科目候補": ev["科目候補"], "補助候補": ev["補助候補"],
        "自信度": ev["自信度"], "支払方法の推定": ev["支払方法の推定"],
    }


def resolve_evidence(year_dir, evidence_id, *, match=None, as_payable=False, as_new=False, sources, accounts=None,
                     payees=None):
    """証憑1件の状態を決める。sources は読み込み済みの kessan-sources.yaml。戻り値は ResolveResult（新しい状態と、--match で明細行に証憑を付けたか）。"""
    if sum([match is not None, bool(as_payable), bool(as_new)]) != 1:
        raise KessanError("--match・--as-payable・--as-new のうち1つだけを指定してください")
    year_dir = Path(year_dir)
    evidence = read_evidence(year_dir)
    found = [r for r in evidence if r["証憑ID"] == evidence_id]
    if not found:
        raise KessanError(f"証憑ID {evidence_id} が evidence.csv に無い")
    ev = found[0]
    state = ev["状態"]
    staging = read_rows(year_dir / "staging.csv")
    known_ids = known_source_ids(year_dir)
    staging_changed = False
    new_row = None

    if match is not None:
        if state != STATE_MULTIPLE:
            raise KessanError(f"証憑 {evidence_id} は状態が「{state}」で、--match で決められるのは「{STATE_MULTIPLE}」だけ")
        if match not in candidate_ids(ev):
            raise KessanError(f"{match} は証憑 {evidence_id} の候補（{'・'.join(candidate_ids(ev))}）に無い")
        if match not in known_ids:
            raise KessanError(f"取り込み元ID {match} の行が staging.csv・journal.csv・adjustments.csv に無い")
        for other in evidence:
            if other["証憑ID"] != evidence_id and other["状態"] == STATE_MATCHED and other["取り込み元ID"].strip() == match:
                raise KessanError(f"取り込み元ID {match} には別の証憑（{other['証憑ID']}）が対応済み")
        new_state, new_source = STATE_MATCHED, match
        data = _document(ev)
        abbreviations = load_abbreviations()
        for r in staging:
            if r["取り込み元ID"].strip() == match and can_attach_receipt(r):
                attach_receipt_to_row(r, data, abbreviations, payees)
                staging_changed = True
    elif as_payable:
        no_row = state == STATE_NEW_ENTRY and ev["取り込み元ID"].strip() not in known_ids
        if state != STATE_MULTIPLE and not no_row:
            raise KessanError(f"証憑 {evidence_id} は状態が「{state}」で、--as-payable にできるのは"
                              f"「{STATE_MULTIPLE}」と、仕訳の行が無い「{STATE_NEW_ENTRY}」だけ")
        new_state, new_source = STATE_UNPAID, ""
    else:
        if state not in (STATE_MULTIPLE, STATE_UNPAID):
            raise KessanError(f"証憑 {evidence_id} は状態が「{state}」で、--as-new にできるのは"
                              f"「{STATE_MULTIPLE}」と「{STATE_UNPAID}」だけ")
        data = _document(ev)
        credit = credit_for_new_entry(data["種類"], data["支払方法の推定"], receipt_default(sources),
                                      payment_accounts(sources))
        if credit is None:
            raise KessanError("支払方法が決まらないので新規仕訳にできない（as-payable を使う）")
        new_state, new_source = STATE_NEW_ENTRY, receipt_source_id(evidence_id)
        if new_source in discarded_source_ids(year_dir):
            raise KessanError(f"{new_source} はオーナーが破棄した行なので作り直せません（discard-log.csv）")
        if new_source not in known_ids:
            new_row = new_entry_row(data, new_source, credit)

    ensure_writable(year_dir, ("staging.csv", EVIDENCE_FILE))
    try:
        if staging_changed:
            replace_rows(year_dir / "staging.csv", STAGING_COLUMNS, [project(r, STAGING_COLUMNS) for r in staging])
        if new_row:
            append_rows(year_dir / "staging.csv", STAGING_COLUMNS, [new_row])
    except OSError:
        raise cannot_write("staging.csv") from None
    ev["状態"], ev["取り込み元ID"] = new_state, new_source
    try:
        replace_rows(year_dir / EVIDENCE_FILE, EVIDENCE_COLUMNS, [project(r, EVIDENCE_COLUMNS) for r in evidence])
    except OSError:
        raise KessanError("staging.csv は更新済み、evidence.csv の書き込みに失敗しました"
                          "（Excelで開いていたら閉じてから、同じコマンドを再実行してください）") from None
    return ResolveResult(new_state, staging_changed)
