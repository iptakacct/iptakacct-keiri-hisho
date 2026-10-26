"""決算整理の項目：消費税・法人税等。

- 消費税（課税の年度）：年税額＝ close-overrides.yaml の `消費税`（達人で計算した額）、無ければ tax-summary の概算納付税額
  （簡易課税は概算しないので上書きが要る）。仮払税金の期末残高（中間納付）を引いた残りを未払消費税等、マイナスなら未収入金に。
- 法人税等：段階3では見積もらない。close-overrides.yaml の `法人税等`（達人で計算した期末の未払額）があるときだけ候補にする。
"""
import close
from tax import load_rates, load_transitions
from tax_summary import summarize

ITEM_TAX = "消費税"
ITEM_CORP = "法人税等"
PREPAID = "仮払税金"
PREPAID_SUBS = ("", "消費税")  # 消費税の中間納付として扱う仮払税金の補助科目（他の補助は法人税等の中間納付とみなす）
REFUND_SUB = "消費税"
OUT_OF_SCOPE = "対象外"


def _pl_tax(ctx):
    """損益科目の側の税区分。課税の年度は対象外、免税の年度は空。"""
    return OUT_OF_SCOPE if ctx.tax_settings.taxable else ""


def _yen(n):
    return f"{n:,}円"


def _annual_consumption_tax(ctx, overrides, notes):
    """(年税額, 出どころ)。候補を作らないときは (None, None)。"""
    settings = ctx.tax_settings
    if not settings.taxable:
        if ITEM_TAX in overrides:
            notes.append(close.Note(ITEM_TAX, "免税の年度のため、close-overrides.yaml の消費税の額は使いません"))
        return None, None
    if ITEM_TAX in overrides:
        return overrides[ITEM_TAX], "上書き額"
    if settings.method == "簡易":
        notes.append(close.Note(ITEM_TAX, "消費税は達人で計算した額を close-set-amount で入れる（簡易課税は概算しない）"))
        return None, None
    summary = summarize(ctx.year_dir, ctx.accounts, settings, load_transitions(), load_rates())
    notes += [close.Note(ITEM_TAX, f"消費税の集計表の注記: {n}") for n in summary.notes]
    if summary.estimate is None:
        notes.append(close.Note(ITEM_TAX, "消費税は達人で計算した額を close-set-amount で入れる（概算できない設定）"))
        return None, None
    return summary.estimate, "概算（tax-summary）"


def _consumption_tax(ctx, overrides):
    notes = []
    annual, origin = _annual_consumption_tax(ctx, overrides, notes)
    if annual is None:
        return [], notes
    prepaid = {sub: ctx.balance(PREPAID, sub) for sub in PREPAID_SUBS}
    prepaid = {sub: amount for sub, amount in prepaid.items() if amount}
    others = sorted({s for (n, s), v in ctx.balances.items() if n == PREPAID and s not in PREPAID_SUBS and v["期末残高"]})
    if others:
        notes.append(close.Note(ITEM_TAX, f"仮払税金の補助科目「{'・'.join(others)}」は消費税の中間納付として差し引いていません"
                                          "（法人税等の中間納付なら、法人税等の未払額の計算で扱う）"))
    total_prepaid = sum(prepaid.values())
    if annual < 0:
        notes.append(close.Note(ITEM_TAX, f"年税額がマイナス（還付 {_yen(-annual)}）のため候補を作りません（還付の仕訳は手で確認）"))
        return [], notes
    if annual == 0:
        if total_prepaid:
            notes.append(close.Note(ITEM_TAX, f"年税額が0円のため候補を作りません。仮払税金の残高 {_yen(total_prepaid)} は還付として別に処理"))
        return [], notes

    lines = [{"借方科目": "租税公課", "借方金額": annual, "借方税区分": _pl_tax(ctx), "摘要": "消費税等（年税額）"}]
    for sub, amount in prepaid.items():
        lines.append({"貸方科目": PREPAID, "貸方補助": sub, "貸方金額": amount, "摘要": "消費税等の中間納付"})
    rest = annual - total_prepaid
    if rest > 0:
        lines.append({"貸方科目": "未払消費税等", "貸方金額": rest, "摘要": "消費税等の未払"})
    elif rest < 0:
        lines.append({"借方科目": "未収入金", "借方補助": REFUND_SUB, "借方金額": -rest, "摘要": "消費税等の還付"})
    reason = (f"消費税の年税額 {_yen(annual)}：{origin}。中間納付 {_yen(total_prepaid)} を差し引き"
              f"（最終の額は達人の計算結果を close-set-amount --item 消費税 で入れる）")
    return [close.Candidate(ITEM_TAX, ctx.period[1], ctx.period[1], lines, reason)], notes


def _corporate_tax(ctx, overrides):
    if ITEM_CORP not in overrides:
        return [], [close.Note(ITEM_CORP, "法人税等は達人で計算した額（期末の未払額）を close-set-amount で入れる")]
    amount = overrides[ITEM_CORP]
    if amount == 0:
        return [], [close.Note(ITEM_CORP, "法人税等の上書き額が0円のため候補を作りません")]
    lines = [{"借方科目": "法人税等", "借方金額": amount, "借方税区分": _pl_tax(ctx),
              "貸方科目": "未払法人税等", "貸方金額": amount, "摘要": "法人税、住民税及び事業税"}]
    reason = f"法人税等 {_yen(amount)}：上書き額（達人で計算した期末の未払額）"
    return [close.Candidate(ITEM_CORP, ctx.period[1], ctx.period[1], lines, reason)], []


def collect(ctx):
    overrides = close.load_overrides(ctx.year_dir)
    candidates, notes = _consumption_tax(ctx, overrides)
    corp_candidates, corp_notes = _corporate_tax(ctx, overrides)
    return candidates + corp_candidates, notes + corp_notes
