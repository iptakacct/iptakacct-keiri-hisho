"""決算整理の項目：前払。

登録簿 `kessan-prepaid.yaml`（context/company）に登録した年払いの費用（保険料など）を対象にする。
- 前払：当期の journal.csv の行のうち、借方が登録簿の科目で、摘要キーワード・取引先が摘要・取引先に合うものごとに、
  支払日の属する月から `月数` か月を役務の期間とし、期末日の月より後の月数 m を数える。前払額 = 金額 × m ÷ 月数（円未満切り捨て）。
  期末日付けの候補（借方 前払費用（補助＝名前）／貸方 その科目）。0円なら候補なし。
- 前払戻し：期首残高の 前払費用 の補助ごとに、登録簿の同じ名前の科目へ戻す候補（期首日付け）。名前が登録簿に無ければ所見。
- 所見：登録簿に無いが、1件5万円以上の保険料・地代家賃などの支払（年払いなら登録簿へ）。

adjustments.csv の行は見ない（登録済みの 前払戻し が費用の借方に入るので、支払として数え直さないため）。
消費税は支払時の行で扱うので、課税の年度では前払・戻しの両側を 対象外 にする。
"""
from pathlib import Path

import yaml

import close
from common import KessanError, parse_amount, parse_date
from match import name_in_description, reading_key

ITEM_PREPAID = "前払"
ITEM_REVERSAL = "前払戻し"
PREPAID_ACCOUNT = "前払費用"
REGISTRY_FILE = "kessan-prepaid.yaml"
JOURNAL_FILE = "journal.csv"
DEFAULT_MONTHS = 12
MAX_MONTHS = 12
MIN_KEY_LENGTH = 2
LARGE_PAYMENT = 50000
WATCH_ACCOUNTS = ("保険料", "地代家賃", "リース料", "支払手数料", "諸会費", "通信費")
NOT_SUBJECT = "対象外"


def _text(value):
    return "" if value is None else str(value).strip()


def load_registry(company_dir, accounts, abbreviations):
    """登録簿を [{名前, 科目, キーワード, 取引先, 月数}] にする。無ければ空。不正なら全部の誤りをまとめて KessanError。"""
    if company_dir is None:
        return []
    path = Path(company_dir) / REGISTRY_FILE
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise KessanError(f"{REGISTRY_FILE} を読めません: {e}") from None
    except UnicodeDecodeError:
        raise KessanError(f"{REGISTRY_FILE} が UTF-8 で読めません") from None
    items = data.get("prepaid") if isinstance(data, dict) else None
    if items is None:
        items = []
    if not isinstance(items, list):
        raise KessanError(f"{REGISTRY_FILE}: prepaid は「- 名前: …」の並び（リスト）で書く")
    entries, errors, names = [], [], set()
    for i, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"{i}番目: 「名前: …」などの項目で書く")
            continue
        name, subject = _text(item.get("名前")), _text(item.get("科目"))
        keyword, partner = _text(item.get("摘要キーワード")), _text(item.get("取引先"))
        where = f"{i}番目（{name}）"
        if not name:
            errors.append(f"{where}: 名前がありません")
        elif name in names:
            errors.append(f"{where}: 名前「{name}」が重複しています")
        names.add(name)
        account = accounts.get(subject)
        if account is None:
            errors.append(f"{where}: 科目マスタに無い科目「{subject}」")
        elif account.category != "費用":
            errors.append(f"{where}: 科目「{subject}」は費用の科目ではありません")
        if not keyword and not partner:
            errors.append(f"{where}: 摘要キーワード・取引先のどちらかを書く")
        for label, value in (("摘要キーワード", keyword), ("取引先", partner)):
            if value and len(reading_key(value, abbreviations)) < MIN_KEY_LENGTH:
                errors.append(f"{where}: {label}「{value}」は短すぎます（最小{MIN_KEY_LENGTH}文字）")
        months = item.get("月数", DEFAULT_MONTHS)
        if not isinstance(months, int) or isinstance(months, bool) or not 1 <= months <= MAX_MONTHS:
            errors.append(f"{where}: 月数は1〜{MAX_MONTHS}（1年を超える前払は長期前払費用として手で扱う）の整数で書く（値: {months!r}）")
            months = DEFAULT_MONTHS
        entries.append({"名前": name, "科目": subject, "キーワード": keyword, "取引先": partner, "月数": months})
    if errors:
        raise KessanError(f"{REGISTRY_FILE} の書き方に誤りがあります:\n" + "\n".join(errors))
    return entries


def _matches(ctx, entry, row):
    """行の摘要・取引先に、登録簿の摘要キーワード・取引先（書いてあるものすべて）が合うか。"""
    for key in (entry["キーワード"], entry["取引先"]):
        if key and not any(name_in_description(key, row[col], ctx.abbreviations, ctx.payees)
                           for col in ("摘要", "取引先") if _text(row[col])):
            return False
    return True


def _months_after(paid, months, end):
    """支払月から months か月の役務の期間のうち、期末日の月より後の月数。"""
    pay_index = int(paid[:4]) * 12 + int(paid[5:7]) - 1
    end_index = int(end[:4]) * 12 + int(end[5:7]) - 1
    return max(0, min(months, pay_index + months - 1 - end_index))


def _tax(ctx):
    return NOT_SUBJECT if ctx.tax_settings.taxable else ""


def _prepaid(ctx, registry):
    candidates, notes, matched = [], [], set()
    end = ctx.period[1]
    for r in ctx.entries:
        if r.get("_file") != JOURNAL_FILE:
            continue
        account = _text(r["借方科目"])
        when = parse_date(r["日付"])
        if when is None or not account or not _text(r["借方金額"]):
            continue
        hits = [e for e in registry if e["科目"] == account and _matches(ctx, e, r)]
        if not hits:
            continue
        matched.add(id(r))
        entry = hits[0]
        amount = parse_amount(r["借方金額"], f"{JOURNAL_FILE} 伝票{r['伝票番号']}")
        memo = _text(r["摘要"])
        sid = _text(r["取り込み元ID"])
        if not sid:
            notes.append(close.Note(ITEM_PREPAID, f"{when} {memo} {amount:,}円は{entry['名前']}に当たりますが、"
                                                  "取り込み元IDが無いため前払の候補にできません"))
            continue
        if amount <= 0:
            notes.append(close.Note(ITEM_PREPAID, f"{when} {memo} {amount:,}円は{entry['名前']}に当たりますが、"
                                                  "金額が正でないため前払の候補にしません（返金・取消なら前払の額を確かめる）"))
            continue
        if len(hits) > 1:
            notes.append(close.Note(ITEM_PREPAID, f"{when} {memo} は登録簿の複数の項目に当たるため候補にしません"
                                                  f"（{'・'.join(h['名前'] for h in hits)}。登録簿の摘要キーワード・取引先を見直す）"))
            continue
        m = _months_after(when, entry["月数"], end)
        prepaid = amount * m // entry["月数"]
        if prepaid <= 0:
            continue
        tax = _tax(ctx)
        line = {"借方科目": PREPAID_ACCOUNT, "借方補助": entry["名前"], "借方金額": prepaid, "借方税区分": tax,
                "貸方科目": account, "貸方金額": prepaid, "貸方税区分": tax,
                "摘要": f"前払費用 {entry['名前']}（{when} 支払分の{m}か月分）"}
        candidates.append(close.Candidate(
            ITEM_PREPAID, sid, end, [line],
            f"{when} {memo} {amount:,}円を{entry['月数']}か月分として、期末後の{m}か月分を前払"))
    return candidates, notes, matched


def _large_unregistered(ctx, matched):
    notes = []
    for r in ctx.entries:
        if r.get("_file") != JOURNAL_FILE or id(r) in matched:
            continue
        if _text(r["借方科目"]) not in WATCH_ACCOUNTS or not _text(r["借方金額"]):
            continue
        amount = parse_amount(r["借方金額"], f"{JOURNAL_FILE} 伝票{r['伝票番号']}")
        if amount >= LARGE_PAYMENT:
            notes.append(close.Note(ITEM_PREPAID, f"年払いなら {REGISTRY_FILE} に登録（{r['日付']} {_text(r['摘要'])} {amount:,}円）"))
    return notes


def _reversals(ctx, registry):
    candidates, notes = [], []
    by_name = {e["名前"]: e for e in registry}
    for (name, sub), amount in sorted(ctx.opening.items()):
        if name != PREPAID_ACCOUNT or amount == 0:
            continue
        if amount < 0:
            notes.append(close.Note(ITEM_REVERSAL, f"期首の前払費用（{sub}）の残高がマイナス（{amount:,}円）のため戻しの候補にしません（符号を確かめる）"))
            continue
        entry = by_name.get(sub)
        if not sub or entry is None:
            notes.append(close.Note(ITEM_REVERSAL, f"期首の前払費用（{sub}）の戻し先が {REGISTRY_FILE} に無い"))
            continue
        tax = _tax(ctx)
        line = {"借方科目": entry["科目"], "借方金額": amount, "借方税区分": tax,
                "貸方科目": PREPAID_ACCOUNT, "貸方補助": sub, "貸方金額": amount, "貸方税区分": tax,
                "摘要": f"前払費用の戻し {sub}"}
        candidates.append(close.Candidate(ITEM_REVERSAL, sub, ctx.period[0], [line],
                                          f"期首の前払費用（{sub}）{amount:,}円を費用へ戻す"))
    return candidates, notes


def collect(ctx):
    registry = load_registry(ctx.company_dir, ctx.accounts, ctx.abbreviations)
    prepaid, notes, matched = _prepaid(ctx, registry)
    reversals, reversal_notes = _reversals(ctx, registry)
    return prepaid + reversals, notes + _large_unregistered(ctx, matched) + reversal_notes
