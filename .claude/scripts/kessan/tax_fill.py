"""課税の年度で、staging.csv の未承認の行に税区分・インボイス・個別対応を補う（段階3a）。

空欄だけを埋め、手で入れた値は書き換えない。要確認理由は追記だけなので、何度呼んでも結果は同じ。
免税の年度では何もしない。
"""
from dataclasses import dataclass

from common import (
    STAGING_COLUMNS, add_reasons, cannot_write, ensure_writable, project, read_rows, remove_reason, replace_rows,
    to_int,
)
from evidence import STATE_MATCHED, STATE_NEW_ENTRY, read_evidence
from tax import TAX_CATEGORIES, invoice_class, with_rate

MIXED_RATE_REASON = "税率が複数（split-tax で分ける）"
NO_EVIDENCE_REASON = "証憑が無い課税仕入（インボイスを確認）"
REDUCED_RATE_POSSIBLE_REASON = "軽減税率の可能性（証憑で税率を確認）"
REDUCED_RATE_ACCOUNTS = ("会議費", "交際費", "福利厚生費", "新聞図書費")
FIXED_ASSET_SECTIONS = ("有形固定資産", "無形固定資産")
PURCHASE_KINDS = ("課税仕入", "課税仕入返還")


def default_mismatch_reason(default):
    return f"税区分が科目の既定（{default}）と違う"


@dataclass
class FillResult:
    filled: int = 0   # 税の列を書いた行の数
    flagged: int = 0  # 税の要確認理由が付く行の数


def _kind(name):
    cat = TAX_CATEGORIES.get(name)
    return cat.kind if cat else ""


def _same_kind_default(tax, default):
    """税率の違い（10%／8%軽）は同じ区分とみなして、既定と同じか見る。"""
    return with_rate(tax, 10) == with_rate(default, 10)


def _evidence_by_source(year_dir):
    found = {}
    for r in read_evidence(year_dir):
        source_id = r["取り込み元ID"].strip()
        if r["状態"] in (STATE_MATCHED, STATE_NEW_ENTRY) and source_id:
            found.setdefault(source_id, r)
    return found


def _fill_side(row, side, accounts, settings, transitions, ev, reasons):
    """1行の片側（借方／貸方）を補う。税の列に書いたら True。要確認理由にする文は reasons に足す。"""
    account = accounts.get(row[f"{side}科目"].strip())
    amount = to_int(row[f"{side}金額"])
    if account is None or amount == 0:
        return False
    tax_key, invoice_key, alloc_key = f"{side}税区分", f"{side}インボイス", f"{side}個別対応"
    before = row[tax_key].strip()
    default = account.default_tax
    wrote = False

    if not before and default and (account.category in ("収益", "費用") or account.section in FIXED_ASSET_SECTIONS):
        row[tax_key] = default
        wrote = True
    tax = row[tax_key].strip()

    rate = ev["税率"].strip() if ev else ""
    if rate == "8" and tax and (wrote or tax == default):  # 手で入れた別の区分は変えない
        converted = with_rate(tax, 8)
        if converted != tax:
            row[tax_key] = tax = converted
            wrote = True
    elif rate == "混在":
        reasons.append(MIXED_RATE_REASON)

    kind = _kind(tax)
    if kind in PURCHASE_KINDS:
        if not row[invoice_key].strip():
            if ev:
                row[invoice_key] = invoice_class(
                    row["日付"].strip(), bool(ev["登録番号"].strip()), amount, settings.small_exemption, transitions)
                wrote = True
            else:
                reasons.append(NO_EVIDENCE_REASON)
        if settings.allocation == "個別対応" and not row[alloc_key].strip():
            row[alloc_key] = "共通"
            wrote = True
        if account.name in REDUCED_RATE_ACCOUNTS and not rate:
            reasons.append(REDUCED_RATE_POSSIBLE_REASON)

    if before and default and not _same_kind_default(before, default):
        reasons.append(default_mismatch_reason(default))
    return wrote


def fill_tax(year_dir, accounts, settings, transitions):
    """未承認の行の税区分などを補う。課税の年度だけ動く。変更があれば staging.csv を保存する。"""
    result = FillResult()
    if not settings.taxable:
        return result
    staging = read_rows(year_dir / "staging.csv")
    evidence = _evidence_by_source(year_dir)
    changed = False
    for row in staging:
        if row["承認"].strip() == "済":
            continue
        ev = evidence.get(row["取り込み元ID"].strip())
        reasons = []
        wrote = False
        for side in ("借方", "貸方"):
            wrote |= _fill_side(row, side, accounts, settings, transitions, ev, reasons)
        reasons = list(dict.fromkeys(reasons))
        text = add_reasons(row["要確認理由"], *reasons)
        # 証憑が付く・税区分が変わるなどで当てはまらなくなったら、「証憑が無い」の理由は外す
        if NO_EVIDENCE_REASON not in reasons:
            text = remove_reason(text, NO_EVIDENCE_REASON)
        result.flagged += bool(reasons)
        result.filled += bool(wrote)
        if text != row["要確認理由"]:
            row["要確認理由"] = text
            changed = True
        changed |= wrote
    if changed:
        ensure_writable(year_dir, ["staging.csv"])
        try:
            replace_rows(year_dir / "staging.csv", STAGING_COLUMNS, [project(r, STAGING_COLUMNS) for r in staging])
        except OSError:
            raise cannot_write("staging.csv") from None
    return result
