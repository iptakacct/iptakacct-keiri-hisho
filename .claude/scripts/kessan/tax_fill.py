"""課税の年度で、staging.csv の未承認の行に税区分・インボイス・個別対応を補う（段階3a）。

空欄だけを埋め、手で入れた値は書き換えない（証憑の税率が8%で、値が科目の既定のときだけ8%軽に替える）。
このファイルが付ける要確認理由は、当てはまらなくなったら外す。何度呼んでも結果は同じ。
承認済みの行は、証憑の税率と税区分が合わないときだけ承認を外して要確認に戻す。
免税の年度では何もしない。
"""
from collections import Counter
from dataclasses import dataclass

from common import (
    STAGING_COLUMNS, add_reasons, cannot_write, ensure_writable, project, read_rows, remove_reasons, replace_rows,
    split_reasons, to_int,
)
from evidence import evidence_by_source, read_evidence
from tax import FIXED_ASSET_SECTIONS, TAX_CATEGORIES, invoice_class, with_rate

MIXED_RATE_REASON = "税率が複数（split-tax で分ける）"
NO_EVIDENCE_REASON = "証憑が無い課税仕入（インボイスを確認）"
REDUCED_RATE_POSSIBLE_REASON = "軽減税率の可能性（証憑で税率を確認）"
NO_REGISTRATION_REASON = "証憑に登録番号が無い（経過措置で計上。登録番号を確認）"
QUALIFIED_WITHOUT_REGISTRATION_REASON = "インボイスが適格だが証憑に登録番号が無い（登録番号を確認）"
RATE_CHANGED_REASON = "証憑の税率（8%）に合わせて税区分を8%軽に直したため承認を外した（確認して承認し直す）"
REDUCED_RATE_ACCOUNTS = ("会議費", "交際費", "福利厚生費", "新聞図書費")
PURCHASE_KINDS = ("課税仕入", "課税仕入返還")
REVIEW_JUDGEMENT = "要確認"  # accounts_update.AI_JUDGEMENT と同じ（循環importを避けて文字で持つ）
_FIXED_REASONS = (MIXED_RATE_REASON, NO_EVIDENCE_REASON, REDUCED_RATE_POSSIBLE_REASON, NO_REGISTRATION_REASON,
                  QUALIFIED_WITHOUT_REGISTRATION_REASON)


def default_mismatch_reason(default):
    return f"税区分が科目の既定（{default}）と違う"


def rate_mismatch_reason(rate, tax):
    return f"証憑の税率（{rate}%）と税区分（{tax}）が違う"


def _is_managed(part):
    """このファイルが付け外しする要確認理由か（承認を外したことを伝える理由は残す）。"""
    return (part in _FIXED_REASONS
            or (part.startswith("税区分が科目の既定（") and part.endswith("）と違う"))
            or (part.startswith("証憑の税率（") and part.endswith("）が違う")))


@dataclass
class FillResult:
    filled: int = 0      # 税の列を書いた行の数
    flagged: int = 0     # 税の要確認理由が付く行の数
    unapproved: int = 0  # 証憑の税率と合わないため承認を外した行の数


def _kind(name):
    cat = TAX_CATEGORIES.get(name)
    return cat.kind if cat else ""


def _rate(name):
    cat = TAX_CATEGORIES.get(name)
    return cat.rate if cat else None


def _same_kind_default(tax, default):
    """税率の違い（10%／8%軽）は同じ区分とみなして、既定と同じか見る。"""
    return with_rate(tax, 10) == with_rate(default, 10)


def _is_transitional(invoice):
    return invoice.startswith("経過") or invoice == "なし"


def _fill_side(row, side, accounts, settings, transitions, ev, split, existing, reasons):
    """1行の片側（借方／貸方）を補う。税の列に書いたら True。要確認理由にする文は reasons に足す。"""
    account = accounts.get(row[f"{side}科目"].strip())
    amount = to_int(row[f"{side}金額"])
    if account is None or amount == 0:
        return False
    tax_key, invoice_key, alloc_key = f"{side}税区分", f"{side}インボイス", f"{side}個別対応"
    before = row[tax_key].strip()
    default = account.default_tax
    invoice_required = settings.method == "原則"  # 簡易課税・2割特例はインボイスが要らない
    wrote = False

    # 固定資産の既定（課税仕入）は取得（借方）だけ。売却（貸方）は売却価額を課税売上で人が付ける
    if not before and default and (account.category in ("収益", "費用")
                                   or (side == "借方" and account.section in FIXED_ASSET_SECTIONS)):
        row[tax_key] = default
        wrote = True
    tax = row[tax_key].strip()

    rate = ev["税率"].strip() if ev else ""
    if rate == "8" and tax and (wrote or tax == default):  # 手で入れた別の区分は変えない
        converted = with_rate(tax, 8)
        if converted != tax:
            row[tax_key] = tax = converted
            wrote = True
    if rate in ("8", "10") and _rate(tax) not in (None, int(rate)):
        reasons.append(rate_mismatch_reason(rate, tax))
    elif rate == "混在" and not split and _rate(tax) is not None:
        reasons.append(MIXED_RATE_REASON)

    kind = _kind(tax)
    if kind in PURCHASE_KINDS:
        derived = False
        if not row[invoice_key].strip():
            if ev:
                row[invoice_key] = invoice_class(
                    row["日付"].strip(), bool(ev["登録番号"].strip()), amount, settings.small_exemption, transitions)
                wrote = derived = True
            elif invoice_required:
                reasons.append(NO_EVIDENCE_REASON)
        invoice = row[invoice_key].strip()
        if invoice_required and _is_transitional(invoice) and (derived or NO_REGISTRATION_REASON in existing):
            reasons.append(NO_REGISTRATION_REASON)
        if invoice_required and invoice == "適格" and ev and not ev["登録番号"].strip():
            reasons.append(QUALIFIED_WITHOUT_REGISTRATION_REASON)
        if settings.allocation == "個別対応" and not row[alloc_key].strip():
            row[alloc_key] = "共通"
            wrote = True
        if account.name in REDUCED_RATE_ACCOUNTS and not rate:
            reasons.append(REDUCED_RATE_POSSIBLE_REASON)

    if before and default and not _same_kind_default(before, default):
        reasons.append(default_mismatch_reason(default))
    return wrote


def _approved_conflicts(row, accounts, settings, ev, split):
    """承認済みの行で、証憑の税率・登録番号と合わない理由の一覧。8%の証憑で税区分が科目の既定なら8%軽に直す。"""
    rate = ev["税率"].strip() if ev else ""
    reasons = []
    if ev and settings.method == "原則" and not ev["登録番号"].strip():
        for side in ("借方", "貸方"):
            if _kind(row[f"{side}税区分"].strip()) in PURCHASE_KINDS and row[f"{side}インボイス"].strip() == "適格":
                reasons.append(QUALIFIED_WITHOUT_REGISTRATION_REASON)
    if not rate:
        return list(dict.fromkeys(reasons))
    for side in ("借方", "貸方"):
        tax = row[f"{side}税区分"].strip()
        tax_rate = _rate(tax)
        if tax_rate is None:
            continue
        if rate in ("8", "10") and tax_rate != int(rate):
            account = accounts.get(row[f"{side}科目"].strip())
            if rate == "8" and account is not None and tax == account.default_tax:
                row[f"{side}税区分"] = with_rate(tax, 8)
                reasons.append(RATE_CHANGED_REASON)
            else:
                reasons.append(rate_mismatch_reason(rate, tax))
        elif rate == "混在" and not split:
            reasons.append(MIXED_RATE_REASON)
    return list(dict.fromkeys(reasons))


def _unapprove(row, result):
    row["承認"] = ""
    row["判定"] = REVIEW_JUDGEMENT
    result.unapproved += 1


def fill_tax(year_dir, accounts, settings, transitions):
    """未承認の行の税区分などを補い、証憑の税率と合わない承認済みの行の承認を外す。課税の年度だけ動く。

    変更があれば staging.csv を保存する。
    """
    result = FillResult()
    if not settings.taxable:
        return result
    staging = read_rows(year_dir / "staging.csv")
    evidence = evidence_by_source(read_evidence(year_dir))
    id_counts = Counter(r["取り込み元ID"].strip() for r in staging if r["取り込み元ID"].strip())
    changed = False
    unapprove_ids = set()
    for row in staging:
        source_id = row["取り込み元ID"].strip()
        ev = evidence.get(source_id)
        # 税率で分けた伝票（split-tax の S…、同じ取り込み元IDが複数行）には「税率が複数」を付けない
        split = row["伝票番号"].strip().startswith("S") or id_counts[source_id] > 1
        if row["承認"].strip() == "済":
            conflicts = _approved_conflicts(row, accounts, settings, ev, split)
            if conflicts:
                row["要確認理由"] = add_reasons(row["要確認理由"], *conflicts)
                _unapprove(row, result)
                unapprove_ids.add(source_id)
                changed = True
            continue
        existing = split_reasons(row["要確認理由"])
        reasons = []
        wrote = False
        for side in ("借方", "貸方"):
            wrote |= _fill_side(row, side, accounts, settings, transitions, ev, split, existing, reasons)
        reasons = list(dict.fromkeys(reasons))
        # 当てはまらなくなった理由（証憑が付いた・税区分が変わった・税率で分けた 等）は外してから足す
        stale = [p for p in existing if _is_managed(p) and p not in reasons]
        text = add_reasons(remove_reasons(row["要確認理由"], *stale), *reasons)
        result.flagged += bool(reasons)
        result.filled += bool(wrote)
        if text != row["要確認理由"]:
            row["要確認理由"] = text
            changed = True
        changed |= wrote
    # 同じ明細から分けた行（伝票）は、まとめて承認を外す（一部の行だけが登録されないように）
    for row in staging:
        if row["承認"].strip() == "済" and row["取り込み元ID"].strip() in unapprove_ids - {""}:
            _unapprove(row, result)
    if changed:
        ensure_writable(year_dir, ["staging.csv"])
        try:
            replace_rows(year_dir / "staging.csv", STAGING_COLUMNS, [project(r, STAGING_COLUMNS) for r in staging])
        except OSError:
            raise cannot_write("staging.csv") from None
    return result
