"""決算整理の項目：減価償却。

固定資産台帳 `fixed-assets.csv`（context/company）の資産ごとに、当期の償却費を計算して候補にする。
- 定額法：floor(取得価額 × 償却率 × 月数 ÷ 12)。上限は 取得価額 − 前期末償却累計額 − 1（備忘価額1円を残す）
- 定率法（200%）：月割り前の調整前償却額 floor(B × 償却率) を償却保証額（取得価額 × 保証率）と比べ、下回った年から
  その年の期首帳簿価額（改定取得価額）× 改定償却率を使う。選んだ1年分を月数÷12で月割りする（floor）。上限は B − 1
- 一括償却：floor(取得価額 × 当期の月数 ÷ 36)。残り（取得価額 − 前期末償却累計額）が上限。除却しても続く（除却日は見ない）
- 少額特例：供用した年度に取得価額の全額（年間上限は300万円×月数÷12。供用年度より後の年度は候補にせず所見）
事業年度が12か月未満のときは、定額法・定率法の償却率が変わる（省令4条）ので候補を作らず所見にする。
月数は、事業供用日が当期内なら供用月から期末月まで、前期以前なら期間の月数。除却日が当期内ならその月まで。
償却率は `config/depreciation/rates.yaml`（国税庁の償却率等表）から読む。端数は円未満切り捨て。

候補は 借方 減価償却費／貸方 減価償却累計額（有形固定資産の定額法・定率法）、または 貸方 その資産の科目
（無形固定資産と、一括償却・少額特例は直接控除）。
消費税の課税の年度は、減価償却費の側を 対象外 にする（貸方は空）。
所見：当期の journal に、台帳に無い固定資産らしい取得（固定資産の科目の借方、10万円以上の消耗品費など）があるとき。
"""
import csv
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path

import yaml

import close
from common import KessanError, parse_amount, parse_date

ITEM = "減価償却"
LEDGER_FILE = "fixed-assets.csv"
JOURNAL_FILE = "journal.csv"
RATES_PATH = Path(__file__).parents[3] / "config/depreciation/rates.yaml"
COLUMNS = ("資産名", "科目", "取得日", "事業供用日", "取得価額", "償却方法", "耐用年数", "前期末償却累計額", "除却日")
STRAIGHT, DECLINING, LUMP, SMALL = "定額法", "定率法", "一括償却", "少額特例"
METHODS = (STRAIGHT, DECLINING, LUMP, SMALL)
YEARS_RANGE = range(2, 51)
LUMP_LIMIT = 200000  # 一括償却は20万円未満
SMALL_LIMIT = 300000  # 少額減価償却資産の特例は30万円未満
SMALL_CAP = 3000000  # 少額特例の年間上限
LUMP_YEARS = 3
INTANGIBLE = "無形固定資産"  # 減価償却累計額を使わず、資産の科目から直接引く
FIXED_SECTIONS = ("有形固定資産", INTANGIBLE)
NOT_DEPRECIABLE = ("減価償却累計額", "土地")
WATCH_EXPENSES = ("消耗品費", "工具器具備品", "ソフトウエア")
WATCH_AMOUNT = 100000
EXPENSE = "減価償却費"
ACCUMULATED = "減価償却累計額"
NOT_SUBJECT = "対象外"


@dataclass(frozen=True)
class Asset:
    name: str
    account: str
    acquired: str
    in_service: str
    price: int
    method: str
    life: int | None
    prior: int
    retired: str


def _text(value):
    return "" if value is None else str(value).strip()


def _floor(value):
    return int(Decimal(value).to_integral_value(rounding=ROUND_FLOOR))


# ---- 償却率 ----

def load_rates(path=RATES_PATH):
    """償却率表を {"定額法": {年: Decimal}, "定率法": {年: {償却率, 改定償却率?, 保証率?}}} にする。誤りは全部まとめて KessanError。"""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        raise KessanError(f"償却率の表 {path.name} を読めません: {e}") from None
    if not isinstance(data, dict):
        raise KessanError(f"償却率の表 {path.name} の形が違います")
    errors = [f"{label}がありません" for label in ("根拠URL", "確認日") if not _text(data.get(label))]

    def number(value, where):
        try:
            result = Decimal(str(value))
        except InvalidOperation:
            result = None
        if result is None or not result.is_finite() or not 0 < result <= 1:
            errors.append(f"{where}: 0を超え1以下の数値で書いてください（{value!r}）")
            return None
        return result

    straight, declining = {}, {}
    for year in YEARS_RANGE:
        raw = (data.get(STRAIGHT) or {}).get(year)
        if raw is None:
            errors.append(f"{STRAIGHT}: 耐用年数{year}年がありません")
        else:
            straight[year] = number(raw, f"{STRAIGHT} 耐用年数{year}年")
        raw = (data.get(DECLINING) or {}).get(year)
        if not isinstance(raw, dict) or raw.get("償却率") is None:
            errors.append(f"{DECLINING}: 耐用年数{year}年の償却率がありません")
            continue
        entry = {"償却率": number(raw["償却率"], f"{DECLINING} 耐用年数{year}年の償却率")}
        if year > 2 or "改定償却率" in raw or "保証率" in raw:
            for key in ("改定償却率", "保証率"):
                if raw.get(key) is None:
                    errors.append(f"{DECLINING}: 耐用年数{year}年の{key}がありません")
                else:
                    entry[key] = number(raw[key], f"{DECLINING} 耐用年数{year}年の{key}")
        declining[year] = entry
    if errors:
        raise KessanError(f"償却率の表 {path.name} に誤りがあります:\n" + "\n".join(errors))
    return {STRAIGHT: straight, DECLINING: declining}


# ---- 台帳 ----

def load_assets(path, accounts):
    """固定資産台帳を [Asset] にする。無ければ空。不正なら全部の行の誤りをまとめて KessanError。"""
    path = Path(path)
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            rows = list(reader)
    except UnicodeDecodeError:
        raise KessanError(f"{path.name}: UTF-8で読めません（Excelでは『CSV UTF-8』で保存）") from None
    duplicated = sorted({c for c in header if header.count(c) > 1})
    if duplicated:
        raise KessanError(f"{path.name}: 列名が重複しています（{'・'.join(duplicated)}）")
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        raise KessanError(f"{path.name}: 列がありません（{'・'.join(missing)}）")
    assets, errors, names = [], [], set()
    for i, r in enumerate(rows, start=2):  # 1行目はヘッダー
        where = f"{path.name} {i}行目"
        problems = []
        name = _text(r["資産名"])
        if not name:
            problems.append("資産名がありません")
        elif name in names:
            problems.append(f"資産名「{name}」が重複しています")
        names.add(name)
        account_name = _text(r["科目"])
        account = accounts.get(account_name)
        if account is None:
            problems.append(f"科目マスタに無い科目「{account_name}」")
        elif account.section not in FIXED_SECTIONS or account_name in NOT_DEPRECIABLE:
            problems.append(f"科目「{account_name}」は償却する有形・無形固定資産の科目ではありません")
        acquired = parse_date(r["取得日"])
        if acquired is None:
            problems.append(f"取得日「{_text(r['取得日'])}」が YYYY-MM-DD の日付ではありません")
        in_service = acquired
        if _text(r["事業供用日"]):
            in_service = parse_date(r["事業供用日"])
            if in_service is None:
                problems.append(f"事業供用日「{_text(r['事業供用日'])}」が YYYY-MM-DD の日付ではありません")
            elif acquired and in_service < acquired:
                problems.append("事業供用日が取得日より前です")
        retired = ""
        if _text(r["除却日"]):
            retired = parse_date(r["除却日"]) or ""
            if not retired:
                problems.append(f"除却日「{_text(r['除却日'])}」が YYYY-MM-DD の日付ではありません")
            elif in_service and retired < in_service:
                problems.append("除却日が事業供用日より前です")
        price = prior = 0
        try:
            price = parse_amount(r["取得価額"], "取得価額")
            if price <= 0:
                problems.append("取得価額は1円以上にしてください")
        except KessanError as e:
            problems.append(str(e))
        try:
            prior = parse_amount(r["前期末償却累計額"], "前期末償却累計額")
            if prior < 0 or (price > 0 and prior > price):
                problems.append(f"前期末償却累計額（{prior:,}円）は0以上、取得価額以下にしてください")
        except KessanError as e:
            problems.append(str(e))
        method = _text(r["償却方法"])
        if method not in METHODS:
            problems.append(f"償却方法「{method}」は {'／'.join(METHODS)} のどれかにしてください")
        if method == DECLINING and account is not None and account.section == INTANGIBLE:
            problems.append("無形固定資産は定額法だけです（定率法にできない）")
        if method == LUMP and 0 < price >= LUMP_LIMIT:
            problems.append(f"一括償却は取得価額が{LUMP_LIMIT:,}円未満の資産だけです")
        if method == SMALL and 0 < price >= SMALL_LIMIT:
            problems.append(f"少額特例は取得価額が{SMALL_LIMIT:,}円未満の資産だけです")
        life = None
        if method in (STRAIGHT, DECLINING):
            text = _text(r["耐用年数"])
            if not text.isdigit() or int(text) not in YEARS_RANGE:
                problems.append(f"耐用年数「{text}」は{YEARS_RANGE.start}〜{YEARS_RANGE.stop - 1}の整数で書いてください")
            else:
                life = int(text)
        errors += [f"{where}（{name}）: {p}" for p in problems]
        if not problems:
            assets.append(Asset(name, account_name, acquired, in_service, price, method, life, prior, retired))
    if errors:
        raise KessanError(f"{path.name} の書き方に誤りがあります:\n" + "\n".join(errors))
    return assets


# ---- 計算 ----

def _index(day):
    return int(day[:4]) * 12 + int(day[5:7]) - 1


def _months(asset, start, end):
    """当期の償却月数。供用が当期内なら供用月から、除却が当期内なら除却月まで（両端含む）。"""
    first = max(_index(asset.in_service), _index(start))
    last = _index(end)
    if asset.retired and start <= asset.retired <= end:
        last = _index(asset.retired)
    return last - first + 1


def _replay_declining(asset, rate, revised, guarantee, start, end):
    """供用から前期末までを年ごとにたどり、(累計, 改定取得価額 or None)。期末が12か月ごとに揃っていなければ None。"""
    end_month = int(end[5:7]) - 1
    supply = _index(asset.in_service)
    first_end = supply + (end_month - supply) % 12
    gap = _index(start) - 1 - first_end
    if gap < 0 or gap % 12:
        return None
    cumulative, base = 0, None
    for year in range(gap // 12 + 1):
        months = first_end - supply + 1 if year == 0 else 12
        book = asset.price - cumulative
        full = _floor(Decimal(book) * rate)  # 償却保証額との比較は月割り前の1年分で行う
        if base is None and guarantee is not None and full < guarantee:
            base = book
        chosen = _floor(Decimal(base) * revised) if base is not None else full
        cumulative += max(0, min(chosen * months // 12, book - 1))
    return cumulative, base


def _declining(asset, rates, start, end, months):
    """定率法の当期の償却費と、改定取得価額を再現できなかったかどうか。

    月割り前の1年分（調整前償却額 floor(B × 償却率)）を償却保証額と比べて調整前か改定かを選び、選んだ額を月割り（floor）する。
    """
    entry = rates[DECLINING][asset.life]
    rate, revised = entry["償却率"], entry.get("改定償却率")
    guarantee = _floor(Decimal(asset.price) * entry["保証率"]) if "保証率" in entry else None
    book = asset.price - asset.prior
    replay = _replay_declining(asset, rate, revised, guarantee, start, end) if revised is not None else None
    base = replay[1] if replay is not None and replay[0] == asset.prior else None
    full = _floor(Decimal(book) * rate)
    unsure = False
    if base is None and guarantee is not None and full < guarantee:
        base = book
        unsure = asset.in_service < start and (replay is None or replay[0] != asset.prior)
    chosen = _floor(Decimal(base) * revised) if base is not None else full
    return max(0, min(chosen * months // 12, book - 1)), unsure


def _lump(asset, period_months):
    """一括償却資産の当期の損金算入額：取得価額 × 当期の月数 ÷ 36（円未満切り捨て）。残りを超えない。"""
    return min(asset.price * period_months // 36, asset.price - asset.prior)


def _skip(asset, start, end, intangible=False):
    """償却しない資産（供用前・除却済み・償却済み）。所見も出さない。一括償却は除却しても続くので除却日を見ない。"""
    if asset.in_service > end:
        return True
    if asset.method != LUMP and asset.retired and asset.retired < start:
        return True
    if asset.method in (STRAIGHT, DECLINING):
        return asset.price - asset.prior <= (0 if intangible else 1)  # 無形固定資産は備忘価額を残さず0円まで
    return asset.price - asset.prior <= 0


def _tax(ctx):
    return NOT_SUBJECT if ctx.tax_settings.taxable else ""


def _candidates(ctx, assets):
    start, end = ctx.period
    period_months = _index(end) - _index(start) + 1
    candidates, notes, small_total = [], [], 0
    rates = load_rates() if any(a.method in (STRAIGHT, DECLINING) for a in assets) else None
    if any(a.method == SMALL for a in assets):
        notes.append(close.Note(ITEM, "少額減価償却資産の特例は中小企業者等だけ（適用できるか確認）"))
    for asset in assets:
        intangible = ctx.accounts[asset.account].section == INTANGIBLE
        if _skip(asset, start, end, intangible):
            continue
        months = _months(asset, start, end)
        if asset.method in (STRAIGHT, DECLINING) and period_months < 12:
            notes.append(close.Note(ITEM, f"事業年度が12か月未満のため、{asset.name}の償却は耐用年数省令4条の率で別に計算する（候補は作っていない）"))
            continue
        if asset.method == SMALL and asset.in_service < start:
            notes.append(close.Note(ITEM, f"少額減価償却資産の特例は事業供用年度だけ（{asset.name}）"))
            continue
        if asset.method == STRAIGHT:
            amount = _floor(Decimal(asset.price) * rates[STRAIGHT][asset.life] * months / 12)
            amount = max(0, min(amount, asset.price - asset.prior - (0 if intangible else 1)))
        elif asset.method == DECLINING:
            amount, unsure = _declining(asset, rates, start, end, months)
            if unsure:
                notes.append(close.Note(ITEM, f"{asset.name}は償却保証額を下回る年に入っていますが、前期末償却累計額から改定取得価額を"
                                              "再現できないため、期首帳簿価額を改定取得価額として計算しました（達人などの償却額と照合する）"))
        elif asset.method == LUMP:
            amount = _lump(asset, period_months)
        else:
            amount = asset.price - asset.prior
            small_total += amount
        if asset.method != LUMP and asset.retired and start <= asset.retired <= end:
            notes.append(close.Note(ITEM, f"{asset.name}は除却日{asset.retired}が当期内です。{months}か月分まで償却しました"
                                          "（除却の仕訳は手で。除却後は台帳から外すか除却日を残す）"))
        if amount <= 0:
            continue
        credit = ACCUMULATED if asset.method in (STRAIGHT, DECLINING) and not intangible else asset.account
        if asset.method in (STRAIGHT, DECLINING):
            basis = f"{asset.method}・耐用年数{asset.life}年・{months}か月分"
        elif asset.method == LUMP:
            basis = f"一括償却（3年均等・{period_months}か月分）"
        else:
            basis = "少額減価償却資産の特例"
        line = {"借方科目": EXPENSE, "借方金額": amount, "借方税区分": _tax(ctx),
                "貸方科目": credit, "貸方金額": amount, "摘要": f"減価償却費 {asset.name}"}
        candidates.append(close.Candidate(ITEM, asset.name, end, [line],
                                          f"{asset.name}（{asset.account} 取得価額{asset.price:,}円）{basis}"))
    cap = SMALL_CAP * period_months // 12
    if small_total > cap:
        label = f"{SMALL_CAP // 10000}万円" if period_months == 12 else f"{cap:,}円＝{SMALL_CAP // 10000}万円×{period_months}/12"
        notes.append(close.Note(ITEM, f"少額減価償却資産の特例の年間上限（{label}）を超えている"
                                      f"（当期の合計{small_total:,}円。超えた分は一括償却または通常の償却にする）"))
    return candidates, notes


def _unregistered(ctx, assets):
    notes = []
    known = {(a.acquired, a.price) for a in assets}
    for r in ctx.entries:
        if r.get("_file") != JOURNAL_FILE or not _text(r["借方金額"]):
            continue
        account = _text(r["借方科目"])
        master = ctx.accounts.get(account)
        fixed = master is not None and master.section in FIXED_SECTIONS and account not in NOT_DEPRECIABLE
        if not fixed and account not in WATCH_EXPENSES:
            continue
        amount = parse_amount(r["借方金額"], f"{JOURNAL_FILE} 伝票{r['伝票番号']}")
        if (not fixed and amount < WATCH_AMOUNT) or amount <= 0:
            continue
        if (_text(r["日付"]), amount) in known:
            continue
        notes.append(close.Note(ITEM, f"固定資産台帳への登録を検討（{_text(r['日付'])} {_text(r['摘要'])} {amount:,}円。{account}）"))
    return notes


def collect(ctx):
    path = Path(ctx.company_dir) / LEDGER_FILE if ctx.company_dir else None
    assets = load_assets(path, ctx.accounts) if path else []
    candidates, notes = _candidates(ctx, assets)
    return candidates, notes + _unregistered(ctx, assets)
