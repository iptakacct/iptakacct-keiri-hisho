"""決算整理の項目：消費税・法人税等。

- 消費税（課税の年度）：年税額＝ close-overrides.yaml の `消費税`（達人で計算した額）、無ければ tax-summary の概算納付税額
  （簡易課税は概算しないので上書きが要る）。概算には、同じ回に前の項目が出した候補（ctx.pending）も含める。
  仮払税金（補助なし・補助「消費税」）の期末残高（中間納付）を引いた残りを未払消費税等、マイナスなら未収入金にする。
  年税額が0以下でも、中間納付の残高があるか年税額がマイナス（還付）なら、未収入金（補助 消費税）の候補にする。
- 法人税等：段階3では見積もらない。close-overrides.yaml の `法人税等`（達人で計算した期末の未払額）があるときだけ候補にする。
  仮払税金（補助「法人税等」）の残高（中間納付）があれば、法人税等＝未払額＋中間納付として仮払税金を消す。
"""
import close
from common import parse_amount
from tax import load_rates, load_transitions
from tax_summary import summarize

ITEM_TAX = "消費税"
ITEM_CORP = "法人税等"
PREPAID = "仮払税金"
PREPAID_SUBS = ("", "消費税")  # 消費税の中間納付として扱う仮払税金の補助科目
CORP_SUB = "法人税等"          # 法人税等の中間納付として扱う仮払税金の補助科目
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
    summary = summarize(ctx.year_dir, ctx.accounts, settings, load_transitions(), load_rates(),
                        extra_entries=ctx.pending_entries())
    notes += [close.Note(ITEM_TAX, f"消費税の集計表の注記: {n}") for n in summary.notes]
    if summary.estimate is None:
        notes.append(close.Note(ITEM_TAX, "消費税は達人で計算した額を close-set-amount で入れる（概算できない設定）"))
        return None, None
    origin = "概算（tax-summary）"
    if ctx.pending_entries():
        origin += "。この回の他の決算整理の候補を含む"
    return summary.estimate, origin


def _posted_interim(ctx, prefix, sub):
    """登録済みの同じ項目の候補が仮払税金から引いた額。登録後は残高が0になるので、再計算では足し戻して同じ額にする。"""
    return sum(parse_amount(r["貸方金額"], f"adjustments.csv 伝票{r['伝票番号']}") for r in ctx.entries
               if r.get("_file") == close.ADJUSTMENTS_FILE and r["取り込み元ID"].strip().startswith(prefix)
               and r["貸方科目"].strip() == PREPAID and r["貸方補助"].strip() == sub)


def _consumption_tax(ctx, overrides):
    notes = []
    annual, origin = _annual_consumption_tax(ctx, overrides, notes)
    if annual is None:
        return [], notes
    prepaid = {sub: ctx.balance(PREPAID, sub) + _posted_interim(ctx, close.TAX_PREFIX, sub) for sub in PREPAID_SUBS}
    prepaid = {sub: amount for sub, amount in prepaid.items() if amount}
    others = sorted({s for (n, s), v in ctx.balances.items()
                     if n == PREPAID and s not in PREPAID_SUBS + (CORP_SUB,) and v["期末残高"]})
    if others:
        notes.append(close.Note(ITEM_TAX, f"仮払税金の補助科目「{'・'.join(others)}」は消費税の中間納付として差し引いていません"
                                          "（消費税の中間納付は補助なしか補助「消費税」、法人税等の中間納付は補助「法人税等」にする）"))
    total_prepaid = sum(prepaid.values())
    if annual == 0 and not total_prepaid:
        return [], notes

    credit_prepaid = [{"貸方科目": PREPAID, "貸方補助": sub, "貸方金額": amount, "摘要": "消費税等の中間納付"}
                      for sub, amount in prepaid.items()]
    if annual <= 0:
        # 納める額が無い：中間納付と還付額を未収入金にする（税込経理なので還付額は雑収入）
        lines = [{"借方科目": "未収入金", "借方補助": REFUND_SUB, "借方金額": total_prepaid - annual, "摘要": "消費税等の還付"}]
        lines += credit_prepaid
        if annual < 0:
            lines.append({"貸方科目": "雑収入", "貸方金額": -annual, "貸方税区分": _pl_tax(ctx), "摘要": "消費税等の還付"})
    else:
        lines = [{"借方科目": "租税公課", "借方金額": annual, "借方税区分": _pl_tax(ctx), "摘要": "消費税等（年税額）"}]
        lines += credit_prepaid
        rest = annual - total_prepaid
        if rest > 0:
            lines.append({"貸方科目": "未払消費税等", "貸方金額": rest, "摘要": "消費税等の未払"})
        elif rest < 0:
            lines.append({"借方科目": "未収入金", "借方補助": REFUND_SUB, "借方金額": -rest, "摘要": "消費税等の還付"})
    reason = (f"消費税の年税額 {_yen(annual)}：{origin}。中間納付 {_yen(total_prepaid)} を差し引き"
              f"（最終の額は達人の計算結果を close-set-amount --item 消費税 で入れる）")
    return [close.Candidate(ITEM_TAX, ctx.period[1], ctx.period[1], lines, reason)], notes


def _corporate_tax(ctx, overrides):
    interim = ctx.balance(PREPAID, CORP_SUB) + _posted_interim(ctx, close.CORP_PREFIX, CORP_SUB)
    if ITEM_CORP not in overrides:
        notes = [close.Note(ITEM_CORP, "法人税等は達人で計算した額（期末の未払額）を close-set-amount で入れる")]
        if interim:
            notes.append(close.Note(ITEM_CORP, f"仮払税金（法人税等）に中間納付 {_yen(interim)} があります"
                                               "（未払額を入れると、法人税等＝未払額＋中間納付の候補にします）"))
        return [], notes
    unpaid = overrides[ITEM_CORP]
    total = unpaid + interim
    if total <= 0:
        return [], [close.Note(ITEM_CORP, "法人税等の未払額が0円で中間納付も無いため候補を作りません")]
    if interim:
        lines = [{"借方科目": "法人税等", "借方金額": total, "借方税区分": _pl_tax(ctx), "摘要": "法人税、住民税及び事業税"},
                 {"貸方科目": PREPAID, "貸方補助": CORP_SUB, "貸方金額": interim, "摘要": "法人税等の中間納付"}]
        if unpaid:
            lines.append({"貸方科目": "未払法人税等", "貸方金額": unpaid, "摘要": "法人税等の未払"})
    else:
        lines = [{"借方科目": "法人税等", "借方金額": total, "借方税区分": _pl_tax(ctx),
                  "貸方科目": "未払法人税等", "貸方金額": unpaid, "摘要": "法人税、住民税及び事業税"}]
    reason = (f"法人税等 {_yen(total)}（期末の未払額 {_yen(unpaid)}：上書き額（達人で計算した額）"
              f"＋中間納付 {_yen(interim)}）")
    return [close.Candidate(ITEM_CORP, ctx.period[1], ctx.period[1], lines, reason)], []


def collect(ctx):
    overrides = close.load_overrides(ctx.year_dir)
    candidates, notes = _consumption_tax(ctx, overrides)
    corp_candidates, corp_notes = _corporate_tax(ctx, overrides)
    return candidates + corp_candidates, notes + corp_notes
