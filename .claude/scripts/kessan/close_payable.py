"""決算整理の項目：未払・未収。

- 未払：状態が 未払候補 で日付が期末日以前の証憑ごとに、期末日付けの候補（借方 証憑の科目候補／貸方 未払金）を作る。
  翌期の明細（出金）から同額・支払期日の前後（期日が無ければ期末日の翌日から92日以内）・取引先名が摘要に合うものを探し、
  要確認理由に結果（一致／見つからない／複数）を書く。登録後（after_post）に証憑の状態を 未払計上 にする。
- 未収（売上）：翌期の入金のうち、期末日の翌日から60日以内で、当期の売上高の取引先が摘要に合うものごとに、
  期末日付けの候補（借方 売掛金／貸方 売上高）を作る。当期分の売上かどうかは人が確かめる。

翌期の明細は next_year_dir の journal.csv と staging.csv から読む（支払口座の側がある行だけ）。
"""
from datetime import date, timedelta
from pathlib import Path

import close
from common import (
    EVIDENCE_COLUMNS, KessanError, PERIOD_FILE, cannot_write, ensure_writable, parse_amount, parse_date, project,
    read_rows, replace_rows,
)
from evidence import EVIDENCE_FILE, STATE_PAYABLE, STATE_UNPAID
from match import name_in_description, payment_accounts
from tax import TAX_CATEGORIES, invoice_class, load_transitions, with_rate

ITEM_PAYABLE = "未払"
ITEM_RECEIVABLE = "未収"
PAYABLE_ACCOUNT = "未払金"
RECEIVABLE_ACCOUNT = "売掛金"
SALES_ACCOUNT = "売上高"
SALES_TAX = "課税売上10%"
DUE_WINDOW_DAYS = 10         # 支払期日の前後
NO_DUE_WINDOW_DAYS = 92      # 支払期日が無いとき、期末日の翌日から
RECEIPT_WINDOW_DAYS = 60     # 未収：期末日の翌日から
PURCHASE_KINDS = ("課税仕入", "課税仕入返還")
COLLECTED_ACCOUNTS = ("売掛金", "未収入金", "前受金")  # 翌期の入金の相手がこれなら、計上済みの額の回収
PAYABLE_PREFIX = f"{close.SOURCE_PREFIX}{ITEM_PAYABLE}:"


def _day(text):
    return date.fromisoformat(text)


def _next_year_rows(ctx):
    """翌期の明細（journal.csv・staging.csv）で、支払口座の側がある行。{"出金": [...], "入金": [...]}。"""
    out = {"出金": [], "入金": []}
    if ctx.next_year_dir is None:
        return out
    next_dir = Path(ctx.next_year_dir)
    if not (next_dir / PERIOD_FILE).exists():
        raise KessanError(f"翌期の年度フォルダに {PERIOD_FILE} がありません（--next-year-dir を確かめる）: {next_dir}")
    pay = payment_accounts(ctx.sources)
    seen = set()  # 同じ取り込み元IDは1つだけ（journal.csv が staging.csv に優先）
    for name in ("journal.csv", "staging.csv"):
        for r in read_rows(next_dir / name):
            where = f"{next_dir.name}/{name} {r['伝票番号'] or r['日付']}"
            when = parse_date(r["日付"])
            if when is None:
                continue
            sid = r["取り込み元ID"].strip()
            base = {"日付": when, "摘要": r["摘要"], "取り込み元ID": sid}
            for side, other, kind in (("貸方", "借方", "出金"), ("借方", "貸方", "入金")):
                if (r[f"{side}科目"].strip(), r[f"{side}補助"].strip()) not in pay:
                    continue
                if sid and (sid, kind) in seen:
                    continue
                seen.add((sid, kind))
                out[kind].append({**base, "金額": parse_amount(r[f"{side}金額"], where), "相手科目": r[f"{other}科目"].strip()})
    return out


def _payment_window(ev, end):
    """証憑の支払を探す日付の範囲 (最初の日, 最後の日)。"""
    due = parse_date(ev["支払期日"])
    if due:
        return _day(due) - timedelta(days=DUE_WINDOW_DAYS), _day(due) + timedelta(days=DUE_WINDOW_DAYS)
    return _day(end) + timedelta(days=1), _day(end) + timedelta(days=NO_DUE_WINDOW_DAYS)


def _payment_reason(ctx, ev, amount, outgoing):
    first, last = _payment_window(ev, ctx.period[1])
    partner = ev["取引先"].strip()
    found = [r for r in outgoing
             if r["金額"] == amount and first <= _day(r["日付"]) <= last
             and name_in_description(partner, r["摘要"], ctx.abbreviations, ctx.payees)]
    if len(found) == 1:
        return f"翌期の支払 {found[0]['日付']} {found[0]['取り込み元ID'] or '（取り込み元IDなし）'} と一致"
    return "翌期の支払が見つからない" if not found else "翌期の支払の候補が複数"


def _debit_tax(ctx, ev, account, amount, line, reasons):
    """課税の年度の借方の税の列（tax_fill と同じ既定）。"""
    settings = ctx.tax_settings
    if not settings.taxable or not account.default_tax:
        return
    tax = account.default_tax
    rate = ev["税率"].strip()
    if rate == "8":
        tax = with_rate(tax, 8)
    elif rate == "混在":
        reasons.append("証憑の税率が混在（税区分を確認）")
    line["借方税区分"] = tax
    cat = TAX_CATEGORIES.get(tax)
    if cat is not None and cat.kind in PURCHASE_KINDS:
        if settings.method == "原則":
            line["借方インボイス"] = invoice_class(ev["日付"].strip(), bool(ev["登録番号"].strip()), amount,
                                                   settings.small_exemption, load_transitions())
        if settings.allocation == "個別対応":
            line["借方個別対応"] = "共通"


def _paid_this_year(ctx, ev, amount):
    """証憑の日付から期末日までに、同額・同じ取引先の支払（貸方が支払口座）が帳簿にあれば、その行。"""
    pay = payment_accounts(ctx.sources)
    first, last = ev["日付"].strip(), ctx.period[1]
    partner = ev["取引先"].strip()
    for r in ctx.entries:
        when = parse_date(r["日付"])
        if (when is None or not first <= when <= last
                or (r["貸方科目"].strip(), r["貸方補助"].strip()) not in pay
                or parse_amount(r["貸方金額"], f"{r.get('_file', 'journal.csv')} {r['伝票番号']}") != amount):
            continue
        if _same_partner(ctx, partner, r["摘要"]) or _same_partner(ctx, partner, r["取引先"]):
            return {"日付": when, "取り込み元ID": r["取り込み元ID"].strip()}
    return None


def _same_partner(ctx, partner, text):
    return bool(partner and text and text.strip() and name_in_description(partner, text, ctx.abbreviations, ctx.payees))


def _receivable_balance(ctx, partner):
    """期末の売掛金のうち、取引先（または補助科目）がその取引先に当たる分（期首残高＋当期の仕訳）。"""
    total = sum(v for (n, sub), v in ctx.opening.items() if n == RECEIVABLE_ACCOUNT and _same_partner(ctx, partner, sub))
    for r in ctx.entries:
        for side, sign in (("借方", 1), ("貸方", -1)):
            if r[f"{side}科目"].strip() != RECEIVABLE_ACCOUNT:
                continue
            if _same_partner(ctx, partner, r["取引先"]) or _same_partner(ctx, partner, r[f"{side}補助"]):
                total += sign * parse_amount(r[f"{side}金額"], f"{r.get('_file', 'journal.csv')} {r['伝票番号']}")
    return total


def _payables(ctx, outgoing):
    candidates, notes = [], []
    end = ctx.period[1]
    for ev in ctx.evidence:
        if ev["状態"] != STATE_UNPAID or not parse_date(ev["日付"]) or ev["日付"].strip() > end:
            continue
        label = f"証憑 {ev['証憑ID']}（{ev['日付']} {ev['取引先']} {ev['金額']}円）"
        account = ctx.accounts.get(ev["科目候補"].strip())
        if account is None:
            notes.append(close.Note(ITEM_PAYABLE, f"{label}: 科目候補が空かマスタに無いため未払の候補にできません"
                                                   "（evidence.csv の科目候補を直して再実行）"))
            continue
        amount = parse_amount(ev["金額"], f"evidence.csv {ev['証憑ID']}")
        partner = ev["取引先"].strip()
        debit = {"借方科目": account.name, "借方補助": ev["補助候補"].strip(), "借方金額": amount}
        reasons = [_payment_reason(ctx, ev, amount, outgoing)]
        paid = _paid_this_year(ctx, ev, amount)
        if paid:
            reasons.append(f"当期中に同額の支払あり（{paid['日付']} {paid['取り込み元ID'] or '取り込み元IDなし'}）。二重計上の恐れ")
        _debit_tax(ctx, ev, account, amount, debit, reasons)
        line = {**debit, "貸方科目": PAYABLE_ACCOUNT, "貸方金額": amount, "取引先": partner,
                "摘要": " ".join(x for x in ("未払計上", partner, ev["内容"].strip()) if x)}
        candidates.append(close.Candidate(ITEM_PAYABLE, ev["証憑ID"], end, [line], "。".join(reasons)))
    return candidates, notes


def _receivables(ctx, incoming):
    end = ctx.period[1]
    partners = sorted({r["取引先"].strip() for r in ctx.entries
                       if r["貸方科目"].strip() == SALES_ACCOUNT and r["取引先"].strip()})
    first, last = _day(end) + timedelta(days=1), _day(end) + timedelta(days=RECEIPT_WINDOW_DAYS)
    candidates, notes = [], []
    for r in incoming:
        if not first <= _day(r["日付"]) <= last or r["金額"] <= 0 or r["相手科目"] in COLLECTED_ACCOUNTS:
            continue
        partner = next((p for p in partners if name_in_description(p, r["摘要"], ctx.abbreviations, ctx.payees)), None)
        if partner is None:
            continue
        balance = _receivable_balance(ctx, partner)
        if balance >= r["金額"]:
            notes.append(close.Note(ITEM_RECEIVABLE, f"翌期の入金 {r['日付']} {r['金額']:,}円（{partner}）は期末の売掛金で説明できる"
                                                      "可能性があるので候補にしない"))
            continue
        if not r["取り込み元ID"]:
            notes.append(close.Note(ITEM_RECEIVABLE, f"翌期の入金 {r['日付']} {r['金額']:,}円「{r['摘要']}」は取り込み元IDが無いため"
                                                      "未収の候補にできません（当期分の売上か確かめる）"))
            continue
        line = {"借方科目": RECEIVABLE_ACCOUNT, "借方金額": r["金額"], "貸方科目": SALES_ACCOUNT, "貸方金額": r["金額"],
                "貸方税区分": SALES_TAX if ctx.tax_settings.taxable else "", "取引先": partner,
                "摘要": f"未収計上 {partner}（翌期の入金 {r['日付']}）"}
        candidates.append(close.Candidate(ITEM_RECEIVABLE, r["取り込み元ID"], end, [line],
                                          "当期分の売上か確認（翌期の入金から推定）"
                                          + (f"。期末の売掛金が{balance:,}円あるので、一部は計上済みの可能性" if balance > 0 else "")))
    return candidates, notes


def collect(ctx):
    rows = _next_year_rows(ctx)
    payables, notes = _payables(ctx, rows["出金"])
    receivables, receivable_notes = _receivables(ctx, rows["入金"])
    return payables + receivables, notes + receivable_notes


def after_post(year_dir, posted_source_ids):
    """登録した未払の候補の証憑を 未払計上 にし、取り込み元IDを close:未払:<証憑ID> にする（何度呼んでも同じ）。"""
    posted = {s[len(PAYABLE_PREFIX):] for s in posted_source_ids if s.startswith(PAYABLE_PREFIX)}
    if not posted:
        return
    year_dir = Path(year_dir)
    rows = read_rows(year_dir / EVIDENCE_FILE)
    changed = False
    for r in rows:
        if r["証憑ID"] in posted and r["状態"] == STATE_UNPAID:
            r["状態"] = STATE_PAYABLE
            r["取り込み元ID"] = f"{PAYABLE_PREFIX}{r['証憑ID']}"
            changed = True
    if not changed:
        return
    ensure_writable(year_dir, [EVIDENCE_FILE])
    try:
        replace_rows(year_dir / EVIDENCE_FILE, EVIDENCE_COLUMNS, [project(r, EVIDENCE_COLUMNS) for r in rows])
    except OSError:
        raise cannot_write(EVIDENCE_FILE) from None
