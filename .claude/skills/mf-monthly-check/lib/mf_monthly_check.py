"""マネーフォワード月次経理チェックの分析ロジック・レポート生成。
分析関数（check_*）はmfc_ca MCPツールのレスポンスと同じ構造のdict/listを
受け取る純粋関数で、MCP呼び出しから独立してユニットテストできる。
データ取得（mfc_ca MCPツールの呼び出し）はこのファイルの責務ではない——
呼び出し側（Claude）が組み立てたJSONを読むだけ。"""


def flatten_journal_branches(journals):
    """mfc_ca_getJournalsの'journals'配列を、branch単位のフラットなリストに
    変換する。1つのjournalに複数branchがある場合、それぞれ独立した1件として扱う。
    重複・定例チェックの照合キーは(transaction_date, value, debitor_account_name,
    creditor_account_name)——マネーフォワードの生データはtrade_partner_nameが
    空のことが多いため、取引先IDではなく借方・貸方の科目名の組み合わせで代用する。
    2026-08-25、株式会社サンプルDの実データ検証で判明：branchの'debitor'・
    'creditor'キーは、存在していても値がnullのケースが多数存在する（実データでは
    全2,676branch中1,081件＝約40%）。これは「複合」仕訳——借方複数行×貸方1行の
    ように左右の行数が違う仕訳——を、マネーフォワードが借方リストと貸方リストの
    位置対応（zip）で返し、行数の少ない側をnullで埋める仕様によるもの。例：

        branches[0] = {debitor: 長期借入金 190,000, creditor: 普通預金 213,917}
        branches[1] = {debitor: 支払利息    23,917, creditor: null}

    この2branchは同一の普通預金213,917円の出金（元金返済＋利息）であり、
    branches[1]の貸方は暗黙的にbranches[0]と同じ普通預金を指す。そのため、
    片側がnullのbranchは除外せず、**同一仕訳内で最初に見つかった非nullの
    同じ側の科目名を引き継ぐ**。金額は、そのbranch自身が持っている側
    （借方がnullなら貸方）の値を使う。
    これは重複・定例チェックのためのベストエフォートな科目名の推定であり、
    厳密な複式簿記の復元ではない。同一仕訳内のどのbranchにもその側の値が
    存在しない場合（引き継ぎ元が無い場合）と、両側ともnullのbranchのみ、
    科目名が確定しないため対象から除外する。"""
    lines = []
    for journal in journals:
        branches = journal.get("branches", []) or []
        # 同一仕訳内の「暗黙的に共有される科目」＝最初の非nullな借方・貸方
        fallback_debitor = next(
            (b.get("debitor") for b in branches if b.get("debitor")), None)
        fallback_creditor = next(
            (b.get("creditor") for b in branches if b.get("creditor")), None)
        for branch in branches:
            own_debitor = branch.get("debitor")
            own_creditor = branch.get("creditor")
            if not own_debitor and not own_creditor:
                # 両側ともnull——推定の手がかりが無いので除外（通常は発生しない）
                continue
            debitor = own_debitor or fallback_debitor
            creditor = own_creditor or fallback_creditor
            if not debitor or not creditor:
                # 仕訳全体を通してその側の値が一度も無い＝引き継ぎ元が無い
                continue
            lines.append({
                "journal_id": journal.get("id"),
                "transaction_date": journal.get("transaction_date"),
                "value": (own_debitor or own_creditor).get("value"),
                "debitor_account_name": debitor.get("account_name"),
                "creditor_account_name": creditor.get("account_name"),
                "remark": branch.get("remark", ""),
            })
    return lines


def check_duplicates(lines, known_safe_pairs=frozenset()):
    """(transaction_date, value, debitor_account_name, creditor_account_name)が
    完全一致する仕訳行をグループ化する。2026-08-25、株式会社サンプルD・
    株式会社サンプルGの実データ検証で判明：この4項目だけで判定すると、定額課金型
    ビジネス（例：多数の異なる個人が同額の受講料を同日に振り込む）や、たまたま
    同額になった無関係な別取引で、大量の誤検知が発生する（サンプルGでは実データで
    A区分77件が全て別人からの入金という誤検知だった）。
    そこで、同じキーのグループをさらに摘要（remark）で分ける：
    - 摘要が完全一致する行が2件以上 → 本物の二重登録の可能性が高いためA
      （表記ゆれのある二重登録は見逃すが、定額課金型の大量誤検知を防ぐことを
      優先する。2026-08-25、オーナーと合意した設計変更）
    - 摘要が食い違う行が2件以上 → 別々の実在取引がたまたま同額になった
      可能性が高いためC（参考情報として残すが、要確認とはしない）
    摘要が1件しかない行（グループ内で他に同じ摘要が無い）は、単独では
    findingを生成しない。
    known_safe_pairs: {(debitor_account_name, creditor_account_name), ...}。
    ここに含まれる科目の組み合わせは、オーナーが実務上「重複の可能性は
    無い」と判断済みのものとして、A/Cいずれのfindingも一切生成しない
    （例：株式会社サンプルDの支払手数料／普通預金＝多数の異なる振込に
    対する定額の銀行手数料であり、2026-08-25にオーナーから確認済み）。"""
    groups = {}
    for line in lines:
        key = (
            line["transaction_date"], line["value"],
            line["debitor_account_name"], line["creditor_account_name"],
        )
        groups.setdefault(key, []).append(line)

    findings = []
    for (transaction_date, value, debitor_name, creditor_name), group in groups.items():
        if len(group) < 2:
            continue
        if (debitor_name, creditor_name) in known_safe_pairs:
            continue

        by_remark = {}
        for line in group:
            by_remark.setdefault(line["remark"], []).append(line)

        def make_finding(subgroup, severity):
            return {
                "check": "duplicate",
                "severity": severity,
                "transaction_date": transaction_date,
                "value": value,
                "debitor_account_name": debitor_name,
                "creditor_account_name": creditor_name,
                "journal_ids": [line["journal_id"] for line in subgroup],
                "remarks": [line["remark"] for line in subgroup if line["remark"]],
            }

        loners = []
        for remark_group in by_remark.values():
            if len(remark_group) >= 2:
                findings.append(make_finding(remark_group, "A"))
            else:
                loners.append(remark_group[0])

        if len(loners) >= 2:
            findings.append(make_finding(loners, "C"))

    # valueがNoneの行が混ざってもソートで落ちないようにする（TypeErrorになると
    # run_monthly_check側のtry/exceptで重複・定例漏れチェックが両方失われる）
    findings.sort(key=lambda f: (f["transaction_date"], -(f["value"] or 0)))
    return findings


def check_recurring_missing(lines, target_month, recurring_min_months=6):
    """(debitor_account_name, creditor_account_name)の組み合わせごとに、対象月より
    前のrecurring_min_months分の月全てで仕訳が発生しており、かつ対象月に1件も
    無いものを「定例取引の計上漏れ候補」として返す。
    lines: 対象月を含む直近(recurring_min_months+1)ヶ月分のflatten済み仕訳行
      （flatten_journal_branchesの戻り値）。
    target_month: "YYYY-MM"形式。"""
    months_seen = {}
    for line in lines:
        month = line["transaction_date"][:7]
        key = (line["debitor_account_name"], line["creditor_account_name"])
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
    for (debitor_name, creditor_name), months in months_seen.items():
        if target_month in months:
            continue
        if all(pm in months for pm in prior_months):
            findings.append({
                "check": "recurring_missing",
                "severity": "A",
                "debitor_account_name": debitor_name,
                "creditor_account_name": creditor_name,
                "prior_months_present": sorted(prior_months),
            })
    findings.sort(key=lambda f: (f["debitor_account_name"], f["creditor_account_name"]))
    return findings


def extract_account_leaves(rows):
    """mfc_ca_getReportsTrialBalanceProfitLoss/BalanceSheetの'rows'配列
    （ネストしたツリー構造）を再帰的にたどり、type=='account'のリーフノードだけを
    {科目名: values配列}の辞書として返す。values配列の構成は
    [opening_balance, debit_amount, credit_amount, closing_balance, ratio]で固定。
    マネーフォワードの勘定科目名は一意という前提（科目IDが存在しないため）。"""
    leaves = {}
    for row in rows or []:
        if row.get("type") == "account":
            leaves[row["name"]] = row["values"]
        else:
            leaves.update(extract_account_leaves(row.get("rows")))
    return leaves


def check_variance(pl_leaves_by_month, target_month, variance_threshold=0.30,
                    variance_history_months=6, variance_materiality_floor=10000):
    """勘定科目ごとに、対象月の期間発生額(values[1]-values[2])と、対象月以外の月
    （最大variance_history_months分）の平均との乖離率をチェックする。
    abs(乖離率) >= variance_thresholdかつ金額がvariance_materiality_floor以上
    なら検出する。過去平均が0で当月に金額がある場合は「新規発生」として個別に
    検出する（ゼロ除算回避）。過去に一度も発生実績が無く当月に初めて発生した
    勘定科目も、過去平均0として同じ「新規発生」で検出する（2026-08-25修正。
    従来は過去実績のある科目しかループ対象にしておらず、退職金のような
    完全新規の科目が一切検出されなかった）。
    pl_leaves_by_month: {"YYYY-MM": extract_account_leavesの戻り値}。
    variance_history_monthsは対象月を除く直近何ヶ月を過去平均の母数にするかの
    上限（実際に渡された月数がそれより少なければ渡された分だけを使う）。"""
    target_leaves = pl_leaves_by_month.get(target_month, {})

    history_months = sorted(m for m in pl_leaves_by_month if m != target_month)
    # variance_history_months=0は「過去実績を使わない」の意味。負のスライスは
    # -0 == 0 で全件を返してしまうため、正の値のときだけスライスする
    history_months = history_months[-variance_history_months:] if variance_history_months > 0 else []

    history_by_account = {}
    for month in history_months:
        for name, values in pl_leaves_by_month[month].items():
            period_amount = values[1] - values[2]
            history_by_account.setdefault(name, []).append(period_amount)

    findings = []
    # 過去に実績のある科目と、当月に登場する科目の和集合を対象にする
    # （当月が初回発生の科目を取りこぼさないため）
    # （setのまま回すとレポートの並びが実行ごとに変わるためsortedで固定する）
    for name in sorted(set(history_by_account) | set(target_leaves)):
        history_amounts = history_by_account.get(name, [])
        current_values = target_leaves.get(name)
        current_amount = (current_values[1] - current_values[2]) if current_values else 0
        historical_avg = sum(history_amounts) / len(history_amounts) if history_amounts else 0

        if max(abs(current_amount), abs(historical_avg)) < variance_materiality_floor:
            continue

        if historical_avg == 0:
            if current_amount != 0:
                findings.append({
                    "check": "variance_new",
                    "severity": "B",
                    "account_name": name,
                    "current_amount": current_amount,
                    "historical_avg": 0,
                })
            continue

        variance_pct = (current_amount - historical_avg) / abs(historical_avg)
        if abs(variance_pct) >= variance_threshold:
            findings.append({
                "check": "variance",
                "severity": "B",
                "account_name": name,
                "current_amount": current_amount,
                "historical_avg": historical_avg,
                "variance_pct": variance_pct,
            })

    # 乖離率(%)ではなく金額インパクト(円)の絶対値で並べる。2026-08-25、
    # 株式会社サンプルEの実データで判明：%ソートだと「売上高が前月比+99%
    # （実額は数億円規模の急減）」が、「役員報酬が-100%（実額は数十万円）」より
    # 下に並んでしまい、最も重要な異常が見落とされた。会計チェックで重要なのは
    # 乖離率ではなく実額のインパクトなので、金額の絶対値を優先する。
    findings.sort(key=lambda f: -abs(f["current_amount"] - f["historical_avg"]))
    return findings


TARGET_BS_ACCOUNTS = {
    "売掛金", "買掛金", "未払金", "未払費用", "仮払金", "仮受金",
    "立替金", "預り金", "前払費用", "未収入金", "前受金",
}


def check_negative_balance(bs_leaves, target_account_names=TARGET_BS_ACCOUNTS):
    """対象科目(売掛金・買掛金等)のうち、closing_balance(values[3])が負のものを
    異常候補として抽出する。資産科目・負債科目とも、負の残高は貸借の向きが
    逆転している異常な状態として扱う（2026-08-24、株式会社サンプルDの
    実データで資産・負債それぞれの符号を確認済み）。
    bs_leaves: extract_account_leavesの戻り値（{科目名: values配列}）。
    対象科目がその月ゼロ残高でレスポンスに存在しない場合は単に無視する
    （マネーフォワードは全項目ゼロの科目をレスポンスに含めない仕様）。"""
    findings = []
    for name, values in bs_leaves.items():
        if name not in target_account_names:
            continue
        closing_balance = values[3]
        if closing_balance < 0:
            findings.append({
                "check": "negative_balance",
                "severity": "A",
                "account_name": name,
                "closing_balance": closing_balance,
            })
    findings.sort(key=lambda f: f["closing_balance"])
    return findings


def check_stale_subaccounts(journals, target_month, target_account_names=TARGET_BS_ACCOUNTS,
                             lookback_months=3):
    """monthly-closing-checklist.mdの項目9（未払金・売掛金等が通常の支払/入金サイトを
    2ヶ月以上超えて滞留していないか）を補助科目単位で機械的にチェックする。
    2026-08-26、オーナーの指示で追加（毎回口頭で依頼するのが手間なので標準化）。

    BSの残高スナップショット（trial_bs）は科目合計のみで補助科目別の内訳を
    持たないため、これだけでは「対象科目の残高が大きい」ことしか分からず、
    どの取引先分が滞留しているかは判定できない。そこで、既に取得済みの
    journals（対象月＋直近数ヶ月分）から補助科目（sub_account_name。
    無ければtrade_partner_name、それも無ければ科目名自体）単位の出現月を集計し、
    「直近lookback_months分（対象月含む）に渡って借方・貸方どちらか片側でしか
    出現していない＝一度も相殺（消込）されていない」ものを滞留候補として検出する。
    通常の消込サイクルが機能していれば、同じ補助科目が借方・貸方の両方に
    現れる（計上→入金/支払で消込）はずなので、片側だけが続くのは異常の兆候。
    厳密な残高計算（金額の相殺）ではなく出現サイドの偏りによる簡易判定であり、
    ベストエフォートのヒューリスティックである点に注意（正確な残高滞留の
    最終確認は人が行う）。

    `is_realized`がFalseの仕訳（未実現）は対象から除外する。2026-08-26、
    株式会社サンプルGの実データで判明：マネーフォワードクラウド請求書で発行した
    売上側の請求書には、確定操作をしない限り`is_realized: false`のまま残る
    仕訳案が自動生成される。サンプルAグループグループの各社は請求書
    ベースの売上計上ルート自体を使っていないことが多く、この未実現仕訳案は
    実際の残高（試算表）にも影響せず、単なる制度上の副産物として恒常的に
    発生しうる。放置してよいものとして、オーナーの指示で除外対象とした。"""
    def sub_key(branch_side, journal):
        return (
            branch_side.get("sub_account_name")
            or branch_side.get("trade_partner_name")
            or branch_side.get("account_name")
        )

    year, month_num = (int(x) for x in target_month.split("-"))
    window_months = []
    y, m = year, month_num
    for _ in range(lookback_months):
        window_months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    window_months = set(window_months)

    # (account_name, sub_key) -> {"debit_months": set, "credit_months": set}
    seen = {}
    for journal in journals:
        if journal.get("is_realized") is False:
            continue
        transaction_date = journal.get("transaction_date") or ""
        month = transaction_date[:7]
        if month not in window_months:
            continue
        for branch in journal.get("branches", []) or []:
            debitor = branch.get("debitor")
            creditor = branch.get("creditor")
            if debitor and debitor.get("account_name") in target_account_names:
                key = (debitor["account_name"], sub_key(debitor, journal))
                seen.setdefault(key, {"debit_months": set(), "credit_months": set()})
                seen[key]["debit_months"].add(month)
            if creditor and creditor.get("account_name") in target_account_names:
                key = (creditor["account_name"], sub_key(creditor, journal))
                seen.setdefault(key, {"debit_months": set(), "credit_months": set()})
                seen[key]["credit_months"].add(month)

    findings = []
    for (account_name, sub_name), months in seen.items():
        # 対象月にそもそも出現していない（＝残高が動いていない）ものは対象外
        debit_months = months["debit_months"]
        credit_months = months["credit_months"]
        if target_month not in debit_months and target_month not in credit_months:
            continue
        # lookback_months分すべてに渡って出現し続けているが、片側にしか現れない
        # ＝一度も相殺されていない、を検出する
        appeared_months = debit_months | credit_months
        if len(appeared_months) < lookback_months:
            continue
        if debit_months and credit_months:
            continue
        side = "借方" if debit_months else "貸方"
        findings.append({
            "check": "stale_subaccount",
            "severity": "B",
            "account_name": account_name,
            "sub_account_name": sub_name or "(補助科目なし)",
            "side": side,
            "months_seen": len(appeared_months),
            "lookback_months": lookback_months,
        })
    findings.sort(key=lambda f: (f["account_name"], f["sub_account_name"]))
    return findings


def check_upsider_billing_duplicate(journals, target_month):
    """UPSIDERの未払金は、カード利用明細のAPI連携により日々`postTransactionJournalize`
    （entered_by: JOURNAL_TYPE_NORMALまたはJOURNAL_TYPE_EXTERNAL）で計上されていく
    設計。これとは別に、UPSIDER宛の請求書（月次利用明細等）が「請求書取り込み」
    （invox等、entered_by: JOURNAL_TYPE_BILLING）経由でも計上されると、
    同じ支払いが二重に計上されてしまう。2026-08-27、株式会社サンプルFで実例が
    見つかったことを受けて追加（仕訳#2238、invox:IR2136686009、
    ツール費2,190,719円／未払金2,190,719円——UPSIDERの日々のカード利用明細で
    既に個別計上済みの金額が、invox経由でもう一度まとめて計上されていた）。
    サンプルAグループグループ全般で起こりうるため、全社共通の標準チェックとして追加。

    対象月のjournalsのうち、entered_byがJOURNAL_TYPE_BILLINGで、branchの
    debitor/creditorいずれかのtrade_partner_nameに"UPSIDER"を含むものを検出する。"""
    findings = []
    for journal in journals:
        transaction_date = journal.get("transaction_date") or ""
        if transaction_date[:7] != target_month:
            continue
        if journal.get("entered_by") != "JOURNAL_TYPE_BILLING":
            continue
        matched_entry = None
        for branch in journal.get("branches", []) or []:
            for side in ("debitor", "creditor"):
                entry = branch.get(side)
                if entry and entry.get("trade_partner_name") and "UPSIDER" in entry["trade_partner_name"]:
                    matched_entry = entry
                    break
            if matched_entry:
                break
        if matched_entry:
            findings.append({
                "check": "upsider_billing_duplicate",
                "severity": "A",
                "journal_id": journal.get("id"),
                "transaction_date": transaction_date,
                "account_name": matched_entry.get("account_name"),
                "value": matched_entry.get("value"),
                "memo": journal.get("memo", ""),
            })
    findings.sort(key=lambda f: f["transaction_date"])
    return findings


def check_prepaid_expense_amortization_missing(journals, target_month):
    """前払費用の当月償却（貸方への振替）が漏れていないかを検出する。

    `monthly-closing-checklist.md`項目5「前払費用が振り替えられているか、または
    今後振り替える仕訳が入っているか」の機械チェック版。2026-08-27、オーナーの
    指示で全社共通の標準チェックに追加。

    厳密な残高追跡（sub_account単位での個々の契約の残存期間管理）はせず、
    渡されたjournals（対象月＋過去分）の範囲内で、対象月より前の月における
    「前払費用」勘定の借方合計（新規計上）から貸方合計（償却済み）を差し引いた
    簡易的な期首残高を求め、これが正（＝まだ償却されていない残高がある）にも
    関わらず、対象月に「前払費用」の貸方（償却）エントリが一件も無い場合に
    B判定で報告する。

    簡易判定のため、`journals`に含まれない、より過去の期間（取得ウィンドウ外）
    の残高は見えない点に注意。また前払費用に補助科目が付いていない運用の会社が
    多いため、勘定科目全体（「前払費用」という科目名）を対象にしており、
    個別の契約ごとの過不足までは判定しない。"""
    debit_before = 0
    credit_before = 0
    debit_target = 0
    credit_target = 0
    for journal in journals:
        transaction_date = journal.get("transaction_date") or ""
        month = transaction_date[:7]
        if month > target_month:
            continue
        for branch in journal.get("branches", []) or []:
            for side in ("debitor", "creditor"):
                entry = branch.get(side)
                if not entry or entry.get("account_name") != "前払費用":
                    continue
                value = entry.get("value") or 0
                if month < target_month:
                    if side == "debitor":
                        debit_before += value
                    else:
                        credit_before += value
                elif month == target_month:
                    if side == "debitor":
                        debit_target += value
                    else:
                        credit_target += value

    opening_balance = debit_before - credit_before
    findings = []
    if opening_balance > 0 and credit_target == 0:
        findings.append({
            "check": "prepaid_expense_amortization_missing",
            "severity": "B",
            "target_month": target_month,
            "opening_balance": opening_balance,
            "debit_this_month": debit_target,
            "credit_this_month": credit_target,
        })
    return findings


_SEVERITY_LABELS = {"A": "要確認", "B": "確認推奨", "C": "参考"}


def describe_finding(finding):
    """findingの種類に応じた簡潔な日本語説明を返す。"""
    check = finding["check"]
    if check == "duplicate":
        return "仕訳の重複候補"
    if check == "recurring_missing":
        return "定例仕訳の計上漏れ候補"
    if check in ("variance", "variance_new"):
        return f"勘定科目「{finding['account_name']}」の増減"
    if check == "negative_balance":
        return f"「{finding['account_name']}」のマイナス残高"
    if check == "stale_subaccount":
        return f"「{finding['account_name']}（{finding['sub_account_name']}）」の滞留候補"
    if check == "upsider_billing_duplicate":
        return "UPSIDER請求書取り込みによる二重計上候補"
    if check == "prepaid_expense_amortization_missing":
        return "前払費用の当月償却漏れ候補"
    return check


def format_finding_detail(finding):
    """findingの詳細情報を、見出し下の説明文として整形する。"""
    check = finding["check"]
    if check == "duplicate":
        remarks = "、".join(finding["remarks"]) if finding["remarks"] else "(摘要なし)"
        journal_ids = ", ".join(str(i) for i in finding["journal_ids"])
        return (
            f"取引日：{finding['transaction_date']}\n"
            f"金額：{finding['value']}円\n"
            f"借方科目：{finding['debitor_account_name']}／貸方科目：{finding['creditor_account_name']}\n"
            f"仕訳ID：{journal_ids}\n"
            f"摘要：{remarks}\n\n"
            "理由：\n"
            "同一日付・同一金額・同一科目の組み合わせの仕訳が複数件登録されています。\n\n"
            "確認事項：\n"
            "二重登録になっていないか確認してください。"
        )
    if check == "recurring_missing":
        prior = "、".join(finding["prior_months_present"])
        return (
            f"借方科目：{finding['debitor_account_name']}\n"
            f"貸方科目：{finding['creditor_account_name']}\n"
            f"過去に発生していた月：{prior}\n\n"
            "理由：\n"
            "過去連続して発生していますが、対象月は該当する仕訳がありません。\n\n"
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
        return (
            f"科目：{finding['account_name']}\n"
            f"残高：{finding['closing_balance']}円\n\n"
            "理由：\n"
            "通常マイナスにならない科目がマイナス残高になっています。\n\n"
            "確認事項：\n"
            "消込先の誤りや二重計上・計上漏れがないか確認してください。"
        )
    if check == "stale_subaccount":
        return (
            f"科目：{finding['account_name']}（補助科目：{finding['sub_account_name']}）\n"
            f"直近{finding['lookback_months']}ヶ月、{finding['side']}側にしか出現していません\n\n"
            "理由：\n"
            "通常は計上（借方または貸方への発生）と消込（反対側での解消）が交互に発生するはずですが、"
            "この補助科目は片側にしか現れておらず、一度も消込されていない可能性があります。\n\n"
            "確認事項：\n"
            "通常の支払・入金サイトを超えて滞留していないか確認してください（簡易判定のため、"
            "サイトがまだ到来していないだけの正常なケースも含まれます）。"
        )
    if check == "upsider_billing_duplicate":
        return (
            f"仕訳ID：{finding['journal_id']}\n"
            f"取引日：{finding['transaction_date']}\n"
            f"科目：{finding['account_name']}\n"
            f"金額：{finding['value']}円\n"
            f"仕訳メモ：{finding['memo'] or '(なし)'}\n\n"
            "理由：\n"
            "UPSIDERの未払金は、カード利用明細のAPI連携により日々個別に計上されていく設計です。"
            "この仕訳は「請求書取り込み」（invox等）経由でUPSIDER宛にまとめて計上されており、"
            "日々の個別計上と二重計上になっている可能性があります。\n\n"
            "確認事項：\n"
            "同じ支払いが日々のカード利用明細で既に個別計上されていないか確認し、"
            "二重計上であればこの仕訳を削除してください（マネフォAPIには削除機能が無いため、画面から削除）。"
        )
    if check == "prepaid_expense_amortization_missing":
        return (
            f"対象月時点の未償却残高（簡易集計）：{finding['opening_balance']}円\n"
            f"対象月の前払費用・借方（新規計上）：{finding['debit_this_month']}円\n"
            f"対象月の前払費用・貸方（償却）：{finding['credit_this_month']}円\n\n"
            "理由：\n"
            "前払費用に未償却残高があると見られますが、対象月に償却（貸方への振替）"
            "仕訳が見当たりません。\n\n"
            "確認事項：\n"
            "当月分の前払費用の振替仕訳が漏れていないか確認してください（簡易判定のため、"
            "取得ウィンドウ外の期間の残高は見えていない可能性がある点に注意）。"
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


def run_monthly_check(input_data, config=None):
    """組み立て済みの入力JSON（dict）を受け取り、4つのチェックを実行して
    Markdownレポート文字列を返す。データ種別ごとにtry/exceptで区切り、
    あるチェックの失敗が他のチェックを止めないようにする。
    fetch_errorsに記録済みのエラー（Claude側のMCP取得失敗）も、対応する
    チェックをスキップした上でレポートのエラー節に転記する。"""
    config = config or {}
    variance_threshold = config.get("variance_threshold", 0.30)
    recurring_min_months = config.get("recurring_min_months", 6)
    variance_history_months = config.get("variance_history_months", 6)
    variance_materiality_floor = config.get("variance_materiality_floor", 10000)
    duplicate_known_safe_pairs = {
        tuple(pair) for pair in config.get("duplicate_known_safe_pairs", [])
    }

    target_month = input_data["target_month"]
    company_name = input_data["company_name"]
    fetch_errors = input_data.get("fetch_errors") or {}

    findings = []
    errors = []

    stale_lookback_months = config.get("stale_lookback_months", 3)

    if fetch_errors.get("journals"):
        errors.append(f"重複取引チェック・定例取引漏れチェック: {fetch_errors['journals']}")
        errors.append(f"BS科目の滞留チェック: {fetch_errors['journals']}")
        errors.append(f"UPSIDER請求書取り込み二重計上チェック: {fetch_errors['journals']}")
        errors.append(f"前払費用の当月償却漏れチェック: {fetch_errors['journals']}")
    else:
        try:
            journals = input_data.get("journals") or []
            lines = flatten_journal_branches(journals)
            target_lines = [l for l in lines if l["transaction_date"][:7] == target_month]
            findings += check_duplicates(target_lines, duplicate_known_safe_pairs)
            findings += check_recurring_missing(lines, target_month, recurring_min_months)
        except Exception as e:
            errors.append(f"重複取引チェック・定例取引漏れチェック: {e}")

        try:
            journals = input_data.get("journals") or []
            findings += check_stale_subaccounts(journals, target_month, lookback_months=stale_lookback_months)
        except Exception as e:
            errors.append(f"BS科目の滞留チェック: {e}")

        try:
            journals = input_data.get("journals") or []
            findings += check_upsider_billing_duplicate(journals, target_month)
        except Exception as e:
            errors.append(f"UPSIDER請求書取り込み二重計上チェック: {e}")

        try:
            journals = input_data.get("journals") or []
            findings += check_prepaid_expense_amortization_missing(journals, target_month)
        except Exception as e:
            errors.append(f"前払費用の当月償却漏れチェック: {e}")

    if fetch_errors.get("trial_pl"):
        errors.append(f"勘定科目の増減チェック: {fetch_errors['trial_pl']}")
    else:
        try:
            pl_leaves_by_month = input_data.get("trial_pl_by_month") or {}
            findings += check_variance(
                pl_leaves_by_month, target_month, variance_threshold,
                variance_history_months, variance_materiality_floor,
            )
        except Exception as e:
            errors.append(f"勘定科目の増減チェック: {e}")

    if fetch_errors.get("trial_bs"):
        errors.append(f"BS異常残高チェック: {fetch_errors['trial_bs']}")
    else:
        try:
            bs_leaves = input_data.get("trial_bs") or {}
            findings += check_negative_balance(bs_leaves)
        except Exception as e:
            errors.append(f"BS異常残高チェック: {e}")

    return build_report(target_month, company_name, findings, errors=errors)
