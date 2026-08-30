"""freee月次経理チェックの分析ロジック・フェッチ層・レポート生成。
分析関数（check_*）はAPIレスポンスと同じ構造のdict/listを受け取る純粋関数で、
API呼び出しから独立してユニットテストできる。"""

import calendar
import re


def check_duplicates(deals):
    """完全一致する(issue_date, amount, partner_id, type)を持つ取引をグループ化し、
    2件以上あるグループを重複候補として返す。摘要(description)は判定キーに含めない
    （表記ゆれで見逃すリスクを避けるため）。"""
    groups = {}
    for deal in deals:
        key = (deal["issue_date"], deal["amount"], deal.get("partner_id"), deal["type"])
        groups.setdefault(key, []).append(deal)

    findings = []
    for (issue_date, amount, partner_id, deal_type), group in groups.items():
        if len(group) < 2:
            continue
        descriptions = []
        for deal in group:
            for detail in deal.get("details", []):
                desc = detail.get("description")
                if desc:
                    descriptions.append(desc)
        findings.append({
            "check": "duplicate",
            "severity": "A",
            "issue_date": issue_date,
            "amount": amount,
            "partner_id": partner_id,
            "type": deal_type,
            "deal_ids": [d["id"] for d in group],
            "descriptions": descriptions,
        })
    findings.sort(key=lambda f: (f["issue_date"], -f["amount"]))
    return findings


def check_recurring_missing(deals, target_month, recurring_min_months=6):
    """(partner_id, account_item_id)の組み合わせごとに、対象月より前の
    recurring_min_months分の月全てで取引が発生しており、かつ対象月に
    1件も無いものを「定例取引の計上漏れ候補」として返す。
    deals: 対象月を含む直近(recurring_min_months+1)ヶ月分の取引一覧
      （GET /api/1/dealsの"deals"配列）。
    target_month: "YYYY-MM"形式。"""
    months_seen = {}
    for deal in deals:
        month = deal["issue_date"][:7]
        partner_id = deal.get("partner_id")
        for detail in deal.get("details", []):
            account_item_id = detail.get("account_item_id")
            key = (partner_id, account_item_id)
            months_seen.setdefault(key, set()).add(month)

    year, month_num = (int(x) for x in target_month.split("-"))
    prior_months = []
    y, m = year, month_num
    for _ in range(recurring_min_months):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        prior_months.append(f"{y:04d}-{m:02d}")

    findings = []
    for (partner_id, account_item_id), months in months_seen.items():
        if target_month in months:
            continue
        if all(pm in months for pm in prior_months):
            findings.append({
                "check": "recurring_missing",
                "severity": "A",
                "partner_id": partner_id,
                "account_item_id": account_item_id,
                "prior_months_present": sorted(prior_months),
            })
    findings.sort(key=lambda f: (f["partner_id"] or 0, f["account_item_id"] or 0))
    return findings


def _normalize_description(text):
    """摘要の照合用に、空白・数字（「5月分」「2026/07」等）を取り除いた文字列を返す。"""
    if not text:
        return ""
    text = text.translate(str.maketrans("０１２３４５６７８９　", "0123456789 "))
    return re.sub(r"[\s0-9/\-\.:]", "", text)


MISSING_ATTRIBUTES = ("partner_id", "item_id")
MISSING_ATTRIBUTE_LABELS = {"partner_id": "取引先", "item_id": "品目"}


def check_missing_partner_or_item(deals, target_month, lookback_months=2):
    """monthly-closing-checklist.mdの項目8の注記（2026-08-31、オーナーの指示）：
    定例取引の計上漏れ照合と同じdealsを使って、補助科目の付け忘れに相当する
    「取引先（partner_id）・品目（item_id）の付け忘れ」を拾う。freeeには補助科目が
    無く、取引先・品目がその役割を担うため。

    照合キーは（account_item_id, 摘要を正規化したもの）。摘要が無い明細は照合できない
    ので除外。対象月の明細で属性が空のものについて、直近lookback_months分の同じキーの
    明細が1ヶ月以上存在し、かつ全てその属性付きなら候補（重要度B）。過去に空が混在する
    キーは運用が揺れているだけなので対象外。"""
    prior_months = month_range(target_month, lookback_months + 1)[:-1]
    window = set(prior_months) | {target_month}

    # attribute -> key -> month -> [value or None]
    seen = {a: {} for a in MISSING_ATTRIBUTES}
    labels = {}
    target_deal_ids = {a: {} for a in MISSING_ATTRIBUTES}
    for deal in deals:
        month = (deal.get("issue_date") or "")[:7]
        if month not in window:
            continue
        for detail in deal.get("details", []) or []:
            desc = (detail.get("description") or "").strip()
            key_text = _normalize_description(desc)
            if not key_text:
                continue
            key = (detail.get("account_item_id"), key_text)
            labels.setdefault(key, desc)
            values = {"partner_id": deal.get("partner_id"), "item_id": detail.get("item_id")}
            for attr in MISSING_ATTRIBUTES:
                seen[attr].setdefault(key, {}).setdefault(month, []).append(values[attr])
                if month == target_month and values[attr] is None:
                    ids = target_deal_ids[attr].setdefault(key, [])
                    if deal.get("id") not in ids:
                        ids.append(deal.get("id"))

    findings = []
    for attr in MISSING_ATTRIBUTES:
        for key, by_month in seen[attr].items():
            target_values = by_month.get(target_month)
            if not target_values or all(v is not None for v in target_values):
                continue
            prior_present = [m for m in prior_months if m in by_month]
            if not prior_present:
                continue
            prior_values = [v for m in prior_present for v in by_month[m]]
            if any(v is None for v in prior_values):
                continue
            account_item_id, _ = key
            findings.append({
                "check": "missing_subaccount",
                "severity": "B",
                "attribute": attr,
                "account_item_id": account_item_id,
                "key_label": labels[key],
                "deal_ids": target_deal_ids[attr].get(key, []),
                "prior_values": sorted(set(prior_values)),
                "prior_months_present": prior_present,
            })
    findings.sort(key=lambda f: (f["attribute"], f["account_item_id"] or 0, f["key_label"]))
    return findings


DEFAULT_VARIANCE_MATERIALITY_FLOOR = 10000


def check_variance(balances_by_month, target_month, variance_threshold=0.30,
                   variance_materiality_floor=DEFAULT_VARIANCE_MATERIALITY_FLOOR):
    """勘定科目ごとに、対象月の金額(closing_balance)と、対象月以外の月の平均との
    乖離率をチェックする。abs(乖離率) >= variance_thresholdなら検出する。
    過去平均が0で当月に金額がある場合は「新規発生」として個別に検出する
    （ゼロ除算回避）。過去データが全く無い勘定科目は対象外。
    balances_by_month: {"YYYY-MM": trial_plの"balances"配列}。
      "account_item_id"を持たない集計行(total_line)は無視する。

    variance_materiality_floor: 重要性の基準（円）。当月・過去平均の絶対値が
      どちらもこの金額未満の勘定科目は検出しない。historical_avgは「その科目の
      行が存在する月」だけの平均なので、たまにしか発生しない少額科目は
      発生しなかった月に必ず-100%になり、閾値をいくら上げてもノイズとして
      残り続けるため（2026-08-23、サンプルB実データのB分類11件中10件が
      これに該当した）。"""
    target_rows = balances_by_month.get(target_month, [])
    target_by_account = {
        row["account_item_id"]: row
        for row in target_rows
        if row.get("account_item_id") is not None
    }

    history_by_account = {}
    history_names = {}
    for month, rows in balances_by_month.items():
        if month == target_month:
            continue
        for row in rows:
            account_item_id = row.get("account_item_id")
            if account_item_id is None:
                continue
            history_by_account.setdefault(account_item_id, []).append(
                row["closing_balance"]
            )
            name = row.get("account_item_name")
            if name is not None:
                history_names[account_item_id] = name

    findings = []
    for account_item_id, history_amounts in history_by_account.items():
        current_row = target_by_account.get(account_item_id)
        current_amount = current_row["closing_balance"] if current_row else 0
        # 対象月にその科目の行が無い場合でも、過去月の同じaccount_item_idの行から
        # 科目名を復元する（対象月に行が無い＝ゼロ発生というだけで、科目自体は
        # 存在するため、名前が分からない理由は無い）。
        if current_row is not None:
            account_item_name = current_row["account_item_name"]
        else:
            account_item_name = history_names.get(account_item_id)
        historical_avg = sum(history_amounts) / len(history_amounts)

        if max(abs(current_amount), abs(historical_avg)) < variance_materiality_floor:
            continue

        if historical_avg == 0:
            if current_amount != 0:
                findings.append({
                    "check": "variance_new",
                    "severity": "B",
                    "account_item_id": account_item_id,
                    "account_item_name": account_item_name,
                    "current_amount": current_amount,
                    "historical_avg": 0,
                })
            continue

        # abs()で割る：過去平均がマイナスの科目でも「増えた＝正、減った＝負」に
        # なるようにするため（historical_avgで割ると符号が反転する）。
        variance_pct = (current_amount - historical_avg) / abs(historical_avg)
        if abs(variance_pct) >= variance_threshold:
            findings.append({
                "check": "variance",
                "severity": "B",
                "account_item_id": account_item_id,
                "account_item_name": account_item_name,
                "current_amount": current_amount,
                "historical_avg": historical_avg,
                "variance_pct": variance_pct,
            })

    findings.sort(key=lambda f: -abs(f.get("variance_pct", 1.0)))
    return findings


TARGET_BS_ACCOUNTS = {
    "売掛金", "買掛金", "未払金", "未払費用", "仮払金", "仮受金",
    "立替金", "預り金", "前払費用", "未収入金", "前受金",
}


def check_negative_balance(bs_balances, target_account_names=TARGET_BS_ACCOUNTS):
    """対象科目(売掛金・買掛金等)のうち、closing_balanceが負のもの
    (勘定科目合計・取引先別内訳の両方)を異常候補として抽出する。
    資産科目・負債科目とも、負の残高は貸借の向きが逆転している異常な
    状態として扱う（2026-08-23、サンプルBの実データで資産科目側の
    符号を確認済み。詳細はplanのTask4冒頭を参照）。
    bs_balances: GET /api/1/reports/trial_bsの"trial_bs"キーの中身の
      "balances"配列（breakdown_display_type=partnerで取得したもの）。"""
    findings = []
    for row in bs_balances:
        name = row.get("account_item_name")
        if name not in target_account_names:
            continue
        account_item_id = row["account_item_id"]
        total_balance = row["closing_balance"]
        if total_balance < 0:
            findings.append({
                "check": "negative_balance",
                "severity": "A",
                "account_item_id": account_item_id,
                "account_item_name": name,
                "partner_id": None,
                "partner_name": None,
                "closing_balance": total_balance,
            })
        for partner in row.get("partners", []):
            if partner["closing_balance"] < 0:
                findings.append({
                    "check": "negative_balance",
                    "severity": "A",
                    "account_item_id": account_item_id,
                    "account_item_name": name,
                    "partner_id": partner["id"],
                    "partner_name": partner["name"],
                    "closing_balance": partner["closing_balance"],
                })
    findings.sort(key=lambda f: f["closing_balance"])
    return findings


def collect_account_item_names(rows, into=None):
    """trial_pl/trial_bsの"balances"配列から、{account_item_id: account_item_name}を
    集める（集計行=account_item_idを持たない行は無視）。既に取得済みのデータを
    使い回すためのヘルパーで、追加のAPI呼び出しは行わない。"""
    names = {} if into is None else into
    for row in rows:
        account_item_id = row.get("account_item_id")
        name = row.get("account_item_name")
        if account_item_id is not None and name:
            names[account_item_id] = name
    return names


def resolve_finding_names(findings, account_item_names=None, partner_names=None):
    """IDしか持たないfinding（recurring_missing）に、表示用の名称を付与する純粋関数。
    名称が引けなかった場合はキーを付けない（format_finding_detailがIDで代替表示する）。"""
    account_item_names = account_item_names or {}
    partner_names = partner_names or {}
    for finding in findings:
        if finding["check"] == "missing_subaccount":
            account_item_name = account_item_names.get(finding["account_item_id"])
            if account_item_name:
                finding["account_item_name"] = account_item_name
            if finding["attribute"] == "partner_id":
                names = [partner_names.get(v) for v in finding["prior_values"]]
                if all(names):
                    finding["prior_value_names"] = names
            continue
        if finding["check"] != "recurring_missing":
            continue
        account_item_name = account_item_names.get(finding["account_item_id"])
        if account_item_name:
            finding["account_item_name"] = account_item_name
        partner_name = partner_names.get(finding["partner_id"])
        if partner_name:
            finding["partner_name"] = partner_name
    return findings


def _name_or_id(name, raw_id):
    return name if name else f"(名称不明、ID: {raw_id})"


_SEVERITY_LABELS = {"A": "要確認", "B": "確認推奨", "C": "参考"}


def describe_finding(finding):
    check = finding["check"]
    if check == "duplicate":
        return "取引の重複候補"
    if check == "recurring_missing":
        return "定例取引の計上漏れ候補"
    if check in ("variance", "variance_new"):
        return f"勘定科目「{finding['account_item_name']}」の増減"
    if check == "negative_balance":
        return f"「{finding['account_item_name']}」のマイナス残高"
    if check == "missing_subaccount":
        account_item = _name_or_id(finding.get("account_item_name"), finding["account_item_id"])
        label = MISSING_ATTRIBUTE_LABELS[finding["attribute"]]
        return f"「{account_item}」の{label}の付け忘れ候補"
    return check


def format_finding_detail(finding):
    check = finding["check"]
    if check == "duplicate":
        descs = "、".join(finding["descriptions"]) if finding["descriptions"] else "(摘要なし)"
        deal_ids = ", ".join(str(i) for i in finding["deal_ids"])
        return (
            f"発生日：{finding['issue_date']}\n"
            f"金額：{finding['amount']}円\n"
            f"取引ID：{deal_ids}\n"
            f"摘要：{descs}\n\n"
            "理由：\n"
            "同一日付・同一金額・同一取引先の取引が複数件登録されています。\n\n"
            "確認事項：\n"
            "二重登録になっていないか確認してください。"
        )
    if check == "recurring_missing":
        prior = "、".join(finding["prior_months_present"])
        partner = _name_or_id(finding.get("partner_name"), finding["partner_id"])
        account_item = _name_or_id(
            finding.get("account_item_name"), finding["account_item_id"]
        )
        return (
            f"取引先：{partner}\n"
            f"勘定科目：{account_item}\n"
            f"過去に発生していた月：{prior}\n\n"
            "理由：\n"
            "過去連続して発生していますが、対象月は取引がありません。\n\n"
            "確認事項：\n"
            "計上漏れがないか確認してください。"
        )
    if check == "variance_new":
        return (
            f"当月：{finding['current_amount']}円\n"
            "過去平均：0円（過去実績なし）\n\n"
            "理由：\n"
            "過去に発生実績のない勘定科目に、当月新たに金額が発生しています。\n\n"
            "確認事項：\n"
            "計上区分・金額に誤りがないか確認してください。"
        )
    if check == "variance":
        pct = finding["variance_pct"] * 100
        return (
            f"当月：{finding['current_amount']}円\n"
            f"過去平均：{finding['historical_avg']:.0f}円\n"
            f"増減率：{pct:+.0f}%\n\n"
            "理由：\n"
            "過去平均から大きく乖離しています。\n\n"
            "確認事項：\n"
            "計上内容・金額に誤りがないか確認してください。"
        )
    if check == "negative_balance":
        partner_desc = (
            f"（取引先：{finding['partner_name']}）" if finding["partner_name"] else "（科目合計）"
        )
        return (
            f"科目：{finding['account_item_name']}{partner_desc}\n"
            f"残高：{finding['closing_balance']}円\n\n"
            "理由：\n"
            "通常マイナスにならない科目がマイナス残高になっています。\n\n"
            "確認事項：\n"
            "消込先の誤りや二重計上・計上漏れがないか確認してください。"
        )
    if check == "missing_subaccount":
        label = MISSING_ATTRIBUTE_LABELS[finding["attribute"]]
        account_item = _name_or_id(finding.get("account_item_name"), finding["account_item_id"])
        prior = "、".join(finding["prior_months_present"])
        names = finding.get("prior_value_names")
        values = "、".join(str(v) for v in (names or finding["prior_values"]))
        deal_ids = ", ".join(str(i) for i in finding["deal_ids"])
        return (
            f"勘定科目：{account_item}\n"
            f"摘要：{finding['key_label']}\n"
            f"対象月の取引ID（{label}なし）：{deal_ids}\n"
            f"過去に使われていた{label}：{values}（{prior}）\n\n"
            "理由：\n"
            f"同じ勘定科目・摘要の取引が直近では一貫して{label}付きで登録されていましたが、"
            f"対象月は{label}なしで登録されています。\n\n"
            "確認事項：\n"
            f"{label}の付け忘れでないか確認し、必要なら{label}を設定してください。"
        )
    return ""


def build_report(target_month, company_name, findings, errors=None):
    """findingsを重要度(A/B/C)ごとにグループ化し、Markdownレポート文字列を組み立てる。
    errorsは、個別のチェックがデータ取得等で失敗した際のエラーメッセージのリスト
    （run_monthly_checkが収集する）。指定があれば「実行時エラー」節を追加する。"""
    grouped = {"A": [], "B": [], "C": []}
    for finding in findings:
        grouped[finding["severity"]].append(finding)

    lines = [
        "# 月次経理チェック結果",
        "",
        f"対象会社：{company_name}",
        f"対象年月：{target_month}",
        "",
        "## サマリー",
        "",
        f"A：{len(grouped['A'])}件",
        f"B：{len(grouped['B'])}件",
        f"C：{len(grouped['C'])}件",
        "",
    ]

    if errors:
        lines.append("## 実行時エラー")
        lines.append("")
        lines.append("以下のチェックは実行できませんでした（エラー内容は各項目を参照）：")
        lines.append("")
        for error in errors:
            lines.append(f"- {error}")
        lines.append("")

    for severity in ("A", "B", "C"):
        items = grouped[severity]
        lines.append("---")
        lines.append("")
        lines.append(f"## {severity}：{_SEVERITY_LABELS[severity]}")
        lines.append("")
        for i, finding in enumerate(items, start=1):
            lines.append(f"### {i}. {describe_finding(finding)}")
            lines.append("")
            lines.append(format_finding_detail(finding))
            lines.append("")

    return "\n".join(lines)


def month_range(target_month, count):
    """target_monthを含めてcountヶ月分の"YYYY-MM"を、古い順のリストで返す。"""
    year, month = (int(x) for x in target_month.split("-"))
    months = []
    y, m = year, month
    for _ in range(count):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(months))


def month_bounds(month_str):
    """"YYYY-MM" -> ("YYYY-MM-01", "YYYY-MM-<末日>") のタプル。"""
    year, month = (int(x) for x in month_str.split("-"))
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"


def fetch_deals_for_months(api_call, token, company_id, months):
    """指定した月群(YYYY-MMのリスト)に発生した取引を全件取得する（ページング処理込み）。"""
    if not months:
        return []
    start_date, _ = month_bounds(min(months))
    _, end_date = month_bounds(max(months))
    deals = []
    offset = 0
    limit = 100
    while True:
        path = (
            f"/api/1/deals?start_issue_date={start_date}&end_issue_date={end_date}"
            f"&offset={offset}&limit={limit}"
        )
        result = api_call(token, company_id, "GET", path)
        batch = result.get("deals", [])
        deals.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return deals


def fetch_partner_names(api_call, token, company_id):
    """{partner_id: 取引先名}を返す（ページング処理込み、読み取り専用）。
    dealsのレスポンスにはpartner_idしか入っておらず取引先名が無いため
    （2026-08-23、サンプルBの実レスポンスで確認済み）、名称表示にはこの
    1回のGETが必要。"""
    names = {}
    offset = 0
    limit = 100
    while True:
        result = api_call(
            token, company_id, "GET",
            f"/api/1/partners?offset={offset}&limit={limit}",
        )
        batch = result.get("partners", [])
        for partner in batch:
            names[partner["id"]] = partner.get("name")
        if len(batch) < limit:
            break
        offset += limit
    return names


def fetch_trial_pl_for_month(api_call, token, company_id, month_str):
    start_date, end_date = month_bounds(month_str)
    result = api_call(
        token, company_id, "GET",
        f"/api/1/reports/trial_pl?start_date={start_date}&end_date={end_date}",
    )
    return result["trial_pl"]["balances"]


def fetch_fiscal_year_start(api_call, token, company_id, month_str):
    """month_str(YYYY-MM)が属する会計年度の開始日("YYYY-MM-DD")を返す。"""
    result = api_call(token, company_id, "GET", f"/api/1/companies/{company_id}")
    _, target_end = month_bounds(month_str)
    for fy in result["company"]["fiscal_years"]:
        if fy["start_date"] <= target_end <= fy["end_date"]:
            return fy["start_date"]
    raise ValueError(f"{month_str}を含む会計年度が見つかりません")


def fetch_trial_bs_for_month(api_call, token, company_id, month_str, fiscal_year_start):
    """trial_bsは会計年度開始日をstart_dateにしないと、月内の純増減だけになり
    真の残高にならない（実データ検証済み。plan冒頭のGlobal Constraints参照）。"""
    _, end_date = month_bounds(month_str)
    result = api_call(
        token, company_id, "GET",
        f"/api/1/reports/trial_bs?start_date={fiscal_year_start}&end_date={end_date}"
        f"&breakdown_display_type=partner",
    )
    return result["trial_bs"]["balances"]


def run_monthly_check(api_call, token, company_id, company_name, target_month, config=None):
    """4つのチェックを実行し、Markdownレポート文字列を返す。データ取得元ごとに
    try/exceptで区切り、あるチェックの取得失敗が他のチェックを止めないようにする
    （スペック「エラーハンドリング」節の要件）。"""
    config = config or {}
    variance_threshold = config.get("variance_threshold", 0.30)
    recurring_min_months = config.get("recurring_min_months", 6)
    # 「定例」の定義(recurring_min_months)と、増減チェックの過去平均の窓
    # (variance_history_months)は独立した設定。片方を調整しても、もう片方の
    # 挙動が黙って変わらないようにする。
    variance_history_months = config.get("variance_history_months", 6)
    variance_materiality_floor = config.get(
        "variance_materiality_floor", DEFAULT_VARIANCE_MATERIALITY_FLOOR
    )
    subaccount_lookback_months = config.get("subaccount_lookback_months", 2)

    findings = []
    errors = []
    # findingの表示用にIDから名称を引くための対応表。既に取得済みのレスポンス
    # （trial_pl・trial_bs）から組み立て、追加のAPI呼び出しはしない。
    account_item_names = {}

    try:
        months = month_range(target_month, recurring_min_months + 1)
        deals = fetch_deals_for_months(api_call, token, company_id, months)
        target_month_deals = [d for d in deals if d["issue_date"][:7] == target_month]
        findings += check_duplicates(target_month_deals)
        findings += check_recurring_missing(deals, target_month, recurring_min_months)
        findings += check_missing_partner_or_item(
            deals, target_month, lookback_months=subaccount_lookback_months)
    except Exception as e:
        errors.append(f"重複取引チェック・定例取引漏れチェック・取引先/品目の付け忘れチェック: {e}")

    try:
        months = month_range(target_month, variance_history_months + 1)
        balances_by_month = {
            m: fetch_trial_pl_for_month(api_call, token, company_id, m) for m in months
        }
        for rows in balances_by_month.values():
            collect_account_item_names(rows, into=account_item_names)
        findings += check_variance(
            balances_by_month, target_month, variance_threshold,
            variance_materiality_floor,
        )
    except Exception as e:
        errors.append(f"勘定科目の増減チェック: {e}")

    try:
        fiscal_year_start = fetch_fiscal_year_start(api_call, token, company_id, target_month)
        bs_balances = fetch_trial_bs_for_month(
            api_call, token, company_id, target_month, fiscal_year_start
        )
        collect_account_item_names(bs_balances, into=account_item_names)
        findings += check_negative_balance(bs_balances)
    except Exception as e:
        errors.append(f"BS異常残高チェック: {e}")

    # 取引先名はdealsのレスポンスに入っていないため、IDを表示するfinding
    # （定例取引の計上漏れ）がある場合に限り、1回だけGETで引く。
    partner_names = {}
    if any(f["check"] == "recurring_missing"
           or (f["check"] == "missing_subaccount" and f["attribute"] == "partner_id")
           for f in findings):
        try:
            partner_names = fetch_partner_names(api_call, token, company_id)
        except Exception as e:
            errors.append(f"取引先名の取得（表示用）: {e}")
    resolve_finding_names(findings, account_item_names, partner_names)

    return build_report(target_month, company_name, findings, errors=errors)
