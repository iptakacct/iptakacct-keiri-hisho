import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import mf_monthly_check


def _journal(journal_id, transaction_date, value, debitor_name, creditor_name, remark=""):
    return {
        "id": journal_id,
        "transaction_date": transaction_date,
        "branches": [{
            "debitor": {"account_name": debitor_name, "value": value},
            "creditor": {"account_name": creditor_name, "value": value},
            "remark": remark,
        }],
    }


class TestFlattenJournalBranches(unittest.TestCase):
    def test_flattens_single_branch_journal(self):
        journals = [_journal("J1", "2026-07-01", 50000, "通信費", "普通預金", "Zoom利用料")]
        lines = mf_monthly_check.flatten_journal_branches(journals)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0], {
            "journal_id": "J1", "transaction_date": "2026-07-01", "value": 50000,
            "debitor_account_name": "通信費", "creditor_account_name": "普通預金",
            "remark": "Zoom利用料",
        })

    def test_flattens_multiple_branches_into_separate_lines(self):
        journal = {
            "id": "J2",
            "transaction_date": "2026-07-02",
            "branches": [
                {"debitor": {"account_name": "通信費", "value": 1000},
                 "creditor": {"account_name": "普通預金", "value": 1000}, "remark": "A"},
                {"debitor": {"account_name": "消耗品費", "value": 2000},
                 "creditor": {"account_name": "普通預金", "value": 2000}, "remark": "B"},
            ],
        }
        lines = mf_monthly_check.flatten_journal_branches([journal])
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["debitor_account_name"], "通信費")
        self.assertEqual(lines[1]["debitor_account_name"], "消耗品費")

    def test_inherits_missing_side_from_same_journal(self):
        # 2026-08-25、株式会社サンプルDの実データで判明：branchの
        # 'debitor'・'creditor'キーは存在していても値がnullのことがある。
        # これは複合仕訳（借方複数行×貸方1行等）を、行数の少ない側をnullで
        # パディングしたzip形式で返すマネーフォワードの仕様によるもの。
        # null側は同一仕訳内の他branchの科目名を引き継ぐ（除外しない）。
        journal = {
            "id": "J3",
            "transaction_date": "2026-07-03",
            "branches": [
                {"debitor": {"account_name": "長期借入金", "value": 190000},
                 "creditor": {"account_name": "普通預金", "value": 213917}, "remark": "元金"},
                {"debitor": {"account_name": "支払利息", "value": 23917},
                 "creditor": None, "remark": "利息"},
            ],
        }
        lines = mf_monthly_check.flatten_journal_branches([journal])
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1]["debitor_account_name"], "支払利息")
        self.assertEqual(lines[1]["creditor_account_name"], "普通預金")
        self.assertEqual(lines[1]["value"], 23917)

    def test_inherits_missing_debitor_from_same_journal(self):
        # 逆パターン（貸方複数行×借方1行）：debitorがnullのbranchは、
        # 同一仕訳の最初の非nullなdebitorの科目名を引き継ぐ。
        journal = {
            "id": "J4",
            "transaction_date": "2026-07-04",
            "branches": [
                {"debitor": {"account_name": "普通預金", "value": 100000},
                 "creditor": {"account_name": "売掛金", "value": 90000}, "remark": "入金"},
                {"debitor": None,
                 "creditor": {"account_name": "雑収入", "value": 10000}, "remark": "差額"},
            ],
        }
        lines = mf_monthly_check.flatten_journal_branches([journal])
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1]["debitor_account_name"], "普通預金")
        self.assertEqual(lines[1]["creditor_account_name"], "雑収入")
        # debitorがnullのbranchのvalueは、貸方側の金額で補完する
        self.assertEqual(lines[1]["value"], 10000)

    def test_skips_branch_when_no_fallback_available(self):
        # 同一仕訳内のどのbranchにも貸方が存在しない場合は、引き継ぎ元が
        # 無いためその行を除外する（クラッシュさせない）。
        journal = {
            "id": "J5",
            "transaction_date": "2026-07-05",
            "branches": [
                {"debitor": {"account_name": "通信費", "value": 3000},
                 "creditor": None, "remark": "調整"},
            ],
        }
        self.assertEqual(mf_monthly_check.flatten_journal_branches([journal]), [])

    def test_skips_branch_with_both_sides_null(self):
        journal = {
            "id": "J6",
            "transaction_date": "2026-07-06",
            "branches": [
                {"debitor": {"account_name": "消耗品費", "value": 1000},
                 "creditor": {"account_name": "普通預金", "value": 1000}, "remark": "正常"},
                {"debitor": None, "creditor": None, "remark": "空"},
            ],
        }
        lines = mf_monthly_check.flatten_journal_branches([journal])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["debitor_account_name"], "消耗品費")


class TestCheckDuplicates(unittest.TestCase):
    def test_exact_remark_match_is_severity_a(self):
        # 摘要まで完全一致する二重登録は、本物の可能性が高いためA
        journals = [
            _journal("J1", "2026-07-31", 50000, "支払手数料", "普通預金", "振込手数料"),
            _journal("J2", "2026-07-31", 50000, "支払手数料", "普通預金", "振込手数料"),
        ]
        lines = mf_monthly_check.flatten_journal_branches(journals)
        findings = mf_monthly_check.check_duplicates(lines)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["check"], "duplicate")
        self.assertEqual(findings[0]["severity"], "A")
        self.assertEqual(findings[0]["journal_ids"], ["J1", "J2"])
        self.assertEqual(findings[0]["remarks"], ["振込手数料", "振込手数料"])

    def test_different_remarks_are_severity_c(self):
        # 2026-08-25、株式会社サンプルGの実データで判明：日付・金額・科目が同じでも
        # 摘要が食い違う（別人・別取引）場合は誤検知が非常に多いため、Aではなく
        # C（参考）に落とす。摘要の表記ゆれによる二重登録の見逃しより、
        # 定額課金型ビジネスでの大量誤検知を防ぐことを優先する設計判断。
        journals = [
            _journal("J1", "2026-07-31", 50000, "支払手数料", "普通預金", "振込手数料"),
            _journal("J2", "2026-07-31", 50000, "支払手数料", "普通預金", "振込手数料(再登録)"),
        ]
        lines = mf_monthly_check.flatten_journal_branches(journals)
        findings = mf_monthly_check.check_duplicates(lines)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["check"], "duplicate")
        self.assertEqual(findings[0]["severity"], "C")
        self.assertEqual(findings[0]["journal_ids"], ["J1", "J2"])

    def test_many_distinct_remarks_produce_one_c_finding_not_many_a_findings(self):
        # サンプルGの実データが示した典型パターン：多数の別人が同額を振込。
        # 摘要が全員違うので、A findingは0件、C findingが1件（全員まとめて）になる。
        journals = [
            _journal(f"J{i}", "2026-07-01", 217800, "普通預金", "売上高", f"振込 個人{i}")
            for i in range(5)
        ]
        lines = mf_monthly_check.flatten_journal_branches(journals)
        findings = mf_monthly_check.check_duplicates(lines)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "C")
        self.assertEqual(len(findings[0]["journal_ids"]), 5)

    def test_mixed_group_produces_both_a_and_c_findings(self):
        # 同じキーの中に「摘要が一致する本物候補のペア」と「摘要が食い違う
        # コインシデンス2件」が混在する場合、両方のfindingが独立して出る。
        journals = [
            _journal("J1", "2026-07-31", 50000, "支払手数料", "普通預金", "振込手数料"),
            _journal("J2", "2026-07-31", 50000, "支払手数料", "普通預金", "振込手数料"),
            _journal("J3", "2026-07-31", 50000, "支払手数料", "普通預金", "別件A"),
            _journal("J4", "2026-07-31", 50000, "支払手数料", "普通預金", "別件B"),
        ]
        lines = mf_monthly_check.flatten_journal_branches(journals)
        findings = mf_monthly_check.check_duplicates(lines)
        self.assertEqual(len(findings), 2)
        severities = sorted(f["severity"] for f in findings)
        self.assertEqual(severities, ["A", "C"])
        a_finding = next(f for f in findings if f["severity"] == "A")
        self.assertEqual(a_finding["journal_ids"], ["J1", "J2"])
        c_finding = next(f for f in findings if f["severity"] == "C")
        self.assertEqual(sorted(c_finding["journal_ids"]), ["J3", "J4"])

    def test_single_loner_with_no_partner_produces_no_finding(self):
        # ペアの片方だけ摘要が違う場合、その1件だけでは（比較対象が無いので）
        # findingを作らない
        journals = [
            _journal("J1", "2026-07-31", 50000, "支払手数料", "普通預金", "振込手数料"),
            _journal("J2", "2026-07-31", 50000, "支払手数料", "普通預金", "振込手数料"),
            _journal("J3", "2026-07-31", 50000, "支払手数料", "普通預金", "唯一違う摘要"),
        ]
        lines = mf_monthly_check.flatten_journal_branches(journals)
        findings = mf_monthly_check.check_duplicates(lines)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "A")
        self.assertEqual(findings[0]["journal_ids"], ["J1", "J2"])

    def test_known_safe_pair_produces_no_finding_even_with_exact_remark_match(self):
        # 2026-08-25、オーナーの指示：株式会社サンプルDの
        # 支払手数料／普通預金は、多数の異なる振込に対する定額の銀行手数料で
        # あり、重複の可能性は無いと確認済み。known_safe_pairsに含まれる
        # 組み合わせは、摘要が完全一致していてもA/Cいずれも生成しない。
        journals = [
            _journal("J1", "2026-07-31", 500, "支払手数料", "普通預金", "振込手数料"),
            _journal("J2", "2026-07-31", 500, "支払手数料", "普通預金", "振込手数料"),
        ]
        lines = mf_monthly_check.flatten_journal_branches(journals)
        findings = mf_monthly_check.check_duplicates(
            lines, known_safe_pairs={("支払手数料", "普通預金")}
        )
        self.assertEqual(findings, [])

    def test_known_safe_pair_does_not_affect_other_pairs(self):
        journals = [
            _journal("J1", "2026-07-31", 500, "支払手数料", "普通預金", "振込手数料"),
            _journal("J2", "2026-07-31", 500, "支払手数料", "普通預金", "振込手数料"),
            _journal("J3", "2026-07-31", 500, "通信費", "普通預金", "電話代"),
            _journal("J4", "2026-07-31", 500, "通信費", "普通預金", "電話代"),
        ]
        lines = mf_monthly_check.flatten_journal_branches(journals)
        findings = mf_monthly_check.check_duplicates(
            lines, known_safe_pairs={("支払手数料", "普通預金")}
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["debitor_account_name"], "通信費")

    def test_ignores_unique_lines(self):
        journals = [
            _journal("J1", "2026-07-31", 50000, "支払手数料", "普通預金"),
            _journal("J2", "2026-07-31", 60000, "支払手数料", "普通預金"),
        ]
        lines = mf_monthly_check.flatten_journal_branches(journals)
        self.assertEqual(mf_monthly_check.check_duplicates(lines), [])

    def test_sorting_tolerates_none_value(self):
        # valueがnullの行が混ざっても、ソート時にTypeErrorで落ちない
        # （落ちると重複チェック・定例漏れチェックが両方まとめて失われる）
        lines = [
            {"journal_id": "J1", "transaction_date": "2026-07-31", "value": None,
             "debitor_account_name": "支払手数料", "creditor_account_name": "普通預金",
             "remark": ""},
            {"journal_id": "J2", "transaction_date": "2026-07-31", "value": None,
             "debitor_account_name": "支払手数料", "creditor_account_name": "普通預金",
             "remark": ""},
        ]
        findings = mf_monthly_check.check_duplicates(lines)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["journal_ids"], ["J1", "J2"])

    def test_treats_different_creditor_account_as_distinct(self):
        journals = [
            _journal("J1", "2026-07-31", 50000, "支払手数料", "普通預金"),
            _journal("J2", "2026-07-31", 50000, "支払手数料", "現金"),
        ]
        lines = mf_monthly_check.flatten_journal_branches(journals)
        self.assertEqual(mf_monthly_check.check_duplicates(lines), [])


class TestCheckRecurringMissing(unittest.TestCase):
    def _line(self, month_day, debitor_name, creditor_name):
        return {
            "journal_id": "J", "transaction_date": month_day, "value": 1000,
            "debitor_account_name": debitor_name, "creditor_account_name": creditor_name,
            "remark": "",
        }

    def test_flags_missing_when_present_in_all_prior_months(self):
        lines = [
            self._line("2026-01-15", "通信費", "普通預金"),
            self._line("2026-02-15", "通信費", "普通預金"),
            self._line("2026-03-15", "通信費", "普通預金"),
            self._line("2026-04-15", "通信費", "普通預金"),
            self._line("2026-05-15", "通信費", "普通預金"),
            self._line("2026-06-15", "通信費", "普通預金"),
            # 2026-07には(通信費, 普通預金)の仕訳が無い
        ]
        findings = mf_monthly_check.check_recurring_missing(lines, "2026-07")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["check"], "recurring_missing")
        self.assertEqual(findings[0]["severity"], "A")
        self.assertEqual(findings[0]["debitor_account_name"], "通信費")
        self.assertEqual(findings[0]["creditor_account_name"], "普通預金")

    def test_does_not_flag_when_present_in_target_month(self):
        lines = [
            self._line("2026-01-15", "通信費", "普通預金"),
            self._line("2026-02-15", "通信費", "普通預金"),
            self._line("2026-03-15", "通信費", "普通預金"),
            self._line("2026-04-15", "通信費", "普通預金"),
            self._line("2026-05-15", "通信費", "普通預金"),
            self._line("2026-06-15", "通信費", "普通預金"),
            self._line("2026-07-15", "通信費", "普通預金"),
        ]
        self.assertEqual(mf_monthly_check.check_recurring_missing(lines, "2026-07"), [])

    def test_does_not_flag_when_not_present_in_all_prior_months(self):
        lines = [
            self._line("2026-01-15", "通信費", "普通預金"),
            self._line("2026-02-15", "通信費", "普通預金"),
            self._line("2026-03-15", "通信費", "普通預金"),
            self._line("2026-04-15", "通信費", "普通預金"),
            self._line("2026-05-15", "通信費", "普通預金"),
            # 2026-06は無い（6ヶ月中5ヶ月のみ発生）
        ]
        self.assertEqual(mf_monthly_check.check_recurring_missing(lines, "2026-07"), [])


class TestExtractAccountLeaves(unittest.TestCase):
    def test_extracts_leaf_accounts_from_nested_tree(self):
        rows = [
            {
                "name": "販売費及び一般管理費合計",
                "type": "financial_statement_item",
                "rows": [
                    {"name": "通信費", "type": "account", "rows": None,
                     "values": [575686, 270986, 0, 846672, 3.8]},
                    {"name": "消耗品費", "type": "account", "rows": None,
                     "values": [582680, 383277, 0, 965957, 4.4]},
                ],
                "values": [1158366, 654263, 0, 1812629, 8.2],
            },
        ]
        leaves = mf_monthly_check.extract_account_leaves(rows)
        self.assertEqual(leaves, {
            "通信費": [575686, 270986, 0, 846672, 3.8],
            "消耗品費": [582680, 383277, 0, 965957, 4.4],
        })

    def test_handles_deeply_nested_rows(self):
        rows = [
            {"name": "資産の部合計", "type": "financial_statement_item", "rows": [
                {"name": "流動資産合計", "type": "financial_statement_item", "rows": [
                    {"name": "売掛金", "type": "account", "rows": None,
                     "values": [100000, 50000, 30000, 120000, 10.0]},
                ], "values": [100000, 50000, 30000, 120000, 10.0]},
            ], "values": [100000, 50000, 30000, 120000, 10.0]},
        ]
        leaves = mf_monthly_check.extract_account_leaves(rows)
        self.assertEqual(leaves, {"売掛金": [100000, 50000, 30000, 120000, 10.0]})

    def test_returns_empty_dict_for_none_or_empty_rows(self):
        self.assertEqual(mf_monthly_check.extract_account_leaves(None), {})
        self.assertEqual(mf_monthly_check.extract_account_leaves([]), {})


def _pl_leaves(**accounts):
    """テスト用ヘルパー：{科目名: 期間発生額}を、values配列形式
    [opening, debit_amount, credit_amount, closing, ratio]に変換する。
    debit_amount=期間発生額（正の値ならdebit側、負の値ならcredit側）とし、
    closing等のダミー値は0で埋める（check_varianceはvalues[1]-values[2]しか見ない）。"""
    leaves = {}
    for name, period_amount in accounts.items():
        if period_amount >= 0:
            leaves[name] = [0, period_amount, 0, 0, 0]
        else:
            leaves[name] = [0, 0, -period_amount, 0, 0]
    return leaves


class TestCheckVariance(unittest.TestCase):
    def test_flags_large_increase_over_threshold(self):
        pl_leaves_by_month = {
            "2026-01": _pl_leaves(通信費=50000),
            "2026-02": _pl_leaves(通信費=52000),
            "2026-03": _pl_leaves(通信費=49000),
            "2026-04": _pl_leaves(通信費=51000),
            "2026-05": _pl_leaves(通信費=50000),
            "2026-06": _pl_leaves(通信費=50000),
            "2026-07": _pl_leaves(通信費=150000),
        }
        findings = mf_monthly_check.check_variance(pl_leaves_by_month, "2026-07")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["check"], "variance")
        self.assertEqual(findings[0]["severity"], "B")
        self.assertEqual(findings[0]["account_name"], "通信費")
        self.assertEqual(findings[0]["current_amount"], 150000)
        self.assertAlmostEqual(findings[0]["historical_avg"], 50333.33, places=1)
        self.assertGreater(findings[0]["variance_pct"], 0.30)

    def test_does_not_flag_within_threshold(self):
        pl_leaves_by_month = {
            "2026-01": _pl_leaves(通信費=50000),
            "2026-02": _pl_leaves(通信費=50000),
            "2026-07": _pl_leaves(通信費=55000),
        }
        self.assertEqual(mf_monthly_check.check_variance(pl_leaves_by_month, "2026-07"), [])

    def test_flags_new_account_when_historical_avg_zero_and_above_floor(self):
        pl_leaves_by_month = {
            "2026-01": _pl_leaves(雑費=0),
            "2026-07": _pl_leaves(雑費=30000),
        }
        findings = mf_monthly_check.check_variance(pl_leaves_by_month, "2026-07")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["check"], "variance_new")
        self.assertEqual(findings[0]["current_amount"], 30000)

    def test_variance_pct_positive_when_negative_average_increases(self):
        # 過去平均がマイナス（貸方超過）の科目が0に近づく＝実質的な増加。
        # (0 - (-60000)) / abs(-60000) = +1.0 になるべき（符号反転しない）。
        pl_leaves_by_month = {
            "2026-01": _pl_leaves(貸倒引当金繰入額=-60000),
            "2026-02": _pl_leaves(貸倒引当金繰入額=-60000),
            "2026-07": _pl_leaves(貸倒引当金繰入額=0),
        }
        findings = mf_monthly_check.check_variance(
            pl_leaves_by_month, "2026-07", variance_materiality_floor=0
        )
        self.assertEqual(len(findings), 1)
        self.assertAlmostEqual(findings[0]["variance_pct"], 1.0)

    def test_skips_immaterial_amounts_below_materiality_floor(self):
        pl_leaves_by_month = {
            "2026-01": _pl_leaves(諸会費=600),
            "2026-02": _pl_leaves(諸会費=600),
            "2026-07": _pl_leaves(諸会費=0),
        }
        # -100%の乖離だが、金額が僅少（floor未満）なのでflagしない
        self.assertEqual(mf_monthly_check.check_variance(pl_leaves_by_month, "2026-07"), [])

    def test_flags_same_swing_when_above_materiality_floor(self):
        pl_leaves_by_month = {
            "2026-01": _pl_leaves(諸会費=60000),
            "2026-02": _pl_leaves(諸会費=60000),
            "2026-07": _pl_leaves(諸会費=0),
        }
        findings = mf_monthly_check.check_variance(pl_leaves_by_month, "2026-07")
        self.assertEqual(len(findings), 1)

    def test_flags_account_with_no_history_at_all(self):
        # 2026-08-25、株式会社サンプルDの実データで判明：過去月に一度も
        # 登場しない勘定科目（当月が初回発生）は、history_by_accountに存在せず
        # ループ対象から漏れていた。当月のみに現れる科目もvariance_newとして
        # 検出する（例：退職金 124,644円）。
        pl_leaves_by_month = {
            "2026-01": _pl_leaves(通信費=50000),
            "2026-02": _pl_leaves(通信費=50000),
            "2026-07": _pl_leaves(通信費=50000, 退職金=124644),
        }
        findings = mf_monthly_check.check_variance(pl_leaves_by_month, "2026-07")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["check"], "variance_new")
        self.assertEqual(findings[0]["account_name"], "退職金")
        self.assertEqual(findings[0]["current_amount"], 124644)
        self.assertEqual(findings[0]["historical_avg"], 0)

    def test_does_not_flag_no_history_account_below_materiality_floor(self):
        pl_leaves_by_month = {
            "2026-01": _pl_leaves(通信費=50000),
            "2026-02": _pl_leaves(通信費=50000),
            "2026-07": _pl_leaves(通信費=50000, 雑費=500),
        }
        self.assertEqual(mf_monthly_check.check_variance(pl_leaves_by_month, "2026-07"), [])

    def test_variance_history_months_is_independent_setting(self):
        pl_leaves_by_month = {
            "2026-05": _pl_leaves(通信費=50000),
            "2026-06": _pl_leaves(通信費=50000),
            "2026-07": _pl_leaves(通信費=100000),
        }
        findings = mf_monthly_check.check_variance(
            pl_leaves_by_month, "2026-07", variance_history_months=2
        )
        self.assertEqual(len(findings), 1)
        self.assertAlmostEqual(findings[0]["historical_avg"], 50000)


class TestCheckNegativeBalance(unittest.TestCase):
    def test_flags_negative_balance_on_target_account(self):
        bs_leaves = {
            "売掛金": [100000, 50000, 20000, -30000, 0],
            "現金": [100000, 0, 50000, -1000, 0],  # 対象科目外なので無視される
        }
        findings = mf_monthly_check.check_negative_balance(bs_leaves)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["check"], "negative_balance")
        self.assertEqual(findings[0]["severity"], "A")
        self.assertEqual(findings[0]["account_name"], "売掛金")
        self.assertEqual(findings[0]["closing_balance"], -30000)

    def test_does_not_flag_positive_balances(self):
        bs_leaves = {"売掛金": [100000, 50000, 20000, 130000, 0]}
        self.assertEqual(mf_monthly_check.check_negative_balance(bs_leaves), [])

    def test_ignores_accounts_not_present(self):
        # 対象科目が今月ゼロ残高でAPIレスポンスに含まれない（マネーフォワードは
        # 全項目ゼロの科目を返さない）場合も、単に見つからないだけでエラーにしない
        bs_leaves = {"普通預金": [100000, 0, 0, 100000, 0]}
        self.assertEqual(mf_monthly_check.check_negative_balance(bs_leaves), [])


class TestCheckStaleSubaccounts(unittest.TestCase):
    def _journal(self, date, debitor=None, creditor=None, is_realized=True):
        return {
            "transaction_date": date, "is_realized": is_realized,
            "branches": [{"debitor": debitor, "creditor": creditor}],
        }

    def test_flags_subaccount_only_seen_on_one_side_across_lookback_window(self):
        # 「A社」が売掛金の借方（計上）だけで3ヶ月連続発生し、貸方（消込）が一度も無い
        journals = [
            self._journal("2026-05-15", debitor={"account_name": "売掛金", "sub_account_name": "A社", "value": 1000}),
            self._journal("2026-06-15", debitor={"account_name": "売掛金", "sub_account_name": "A社", "value": 1000}),
            self._journal("2026-07-15", debitor={"account_name": "売掛金", "sub_account_name": "A社", "value": 1000}),
        ]
        findings = mf_monthly_check.check_stale_subaccounts(journals, "2026-07", lookback_months=3)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["check"], "stale_subaccount")
        self.assertEqual(findings[0]["severity"], "B")
        self.assertEqual(findings[0]["account_name"], "売掛金")
        self.assertEqual(findings[0]["sub_account_name"], "A社")
        self.assertEqual(findings[0]["side"], "借方")

    def test_does_not_flag_when_both_sides_appear_within_window(self):
        # 5月に計上、6月に消込（貸方側で解消）——正常なサイクル
        journals = [
            self._journal("2026-05-15", debitor={"account_name": "売掛金", "sub_account_name": "A社", "value": 1000}),
            self._journal("2026-06-15", creditor={"account_name": "売掛金", "sub_account_name": "A社", "value": 1000}),
            self._journal("2026-07-15", debitor={"account_name": "売掛金", "sub_account_name": "A社", "value": 1000}),
        ]
        findings = mf_monthly_check.check_stale_subaccounts(journals, "2026-07", lookback_months=3)
        self.assertEqual(findings, [])

    def test_does_not_flag_when_appeared_fewer_months_than_lookback(self):
        # 対象月にしか出現していない＝まだ支払/入金サイトが到来していないだけの可能性
        journals = [
            self._journal("2026-07-15", debitor={"account_name": "売掛金", "sub_account_name": "A社", "value": 1000}),
        ]
        findings = mf_monthly_check.check_stale_subaccounts(journals, "2026-07", lookback_months=3)
        self.assertEqual(findings, [])

    def test_ignores_non_target_accounts(self):
        journals = [
            self._journal("2026-05-15", debitor={"account_name": "普通預金", "sub_account_name": "A社", "value": 1000}),
            self._journal("2026-06-15", debitor={"account_name": "普通預金", "sub_account_name": "A社", "value": 1000}),
            self._journal("2026-07-15", debitor={"account_name": "普通預金", "sub_account_name": "A社", "value": 1000}),
        ]
        findings = mf_monthly_check.check_stale_subaccounts(journals, "2026-07", lookback_months=3)
        self.assertEqual(findings, [])

    def test_ignores_unrealized_journals(self):
        # マネーフォワードクラウド請求書等の未実現(is_realized: false)仕訳案は
        # 対象から除外する。2026-08-26、株式会社サンプルGの実データで判明した
        # 「常に貸方専用に見えたが実際は未実現の請求書仕訳案だった」を踏まえた挙動。
        journals = [
            self._journal("2026-05-15", creditor={"account_name": "売掛金", "sub_account_name": "A社", "value": 1000}, is_realized=False),
            self._journal("2026-06-15", creditor={"account_name": "売掛金", "sub_account_name": "A社", "value": 1000}, is_realized=False),
            self._journal("2026-07-15", creditor={"account_name": "売掛金", "sub_account_name": "A社", "value": 1000}, is_realized=False),
        ]
        findings = mf_monthly_check.check_stale_subaccounts(journals, "2026-07", lookback_months=3)
        self.assertEqual(findings, [])

    def test_falls_back_to_trade_partner_name_then_account_name_when_no_sub_account(self):
        journals = [
            self._journal("2026-05-15", debitor={"account_name": "未払金", "trade_partner_name": "B社", "value": 1000}),
            self._journal("2026-06-15", debitor={"account_name": "未払金", "trade_partner_name": "B社", "value": 1000}),
            self._journal("2026-07-15", debitor={"account_name": "未払金", "trade_partner_name": "B社", "value": 1000}),
        ]
        findings = mf_monthly_check.check_stale_subaccounts(journals, "2026-07", lookback_months=3)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["sub_account_name"], "B社")


class TestCheckUpsiderBillingDuplicate(unittest.TestCase):
    def _journal(self, journal_id, date, entered_by, value=2190719, trade_partner_name="株式会社UPSIDER",
                 account_name="ツール費", memo=""):
        return {
            "id": journal_id, "transaction_date": date, "entered_by": entered_by, "memo": memo,
            "branches": [{
                "debitor": {"account_name": account_name, "trade_partner_name": trade_partner_name, "value": value},
                "creditor": {"account_name": "未払金", "sub_account_name": "総合振込", "value": value},
            }],
        }

    def test_flags_upsider_billing_entry_in_target_month(self):
        journals = [self._journal("J1", "2026-07-31", "JOURNAL_TYPE_BILLING", memo="仕訳メモ:invox:IR2136686009")]
        findings = mf_monthly_check.check_upsider_billing_duplicate(journals, "2026-07")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["check"], "upsider_billing_duplicate")
        self.assertEqual(findings[0]["severity"], "A")
        self.assertEqual(findings[0]["journal_id"], "J1")
        self.assertEqual(findings[0]["value"], 2190719)
        self.assertIn("invox:IR2136686009", findings[0]["memo"])

    def test_ignores_normal_and_external_entries(self):
        # 日々のカード利用明細の自動計上（JOURNAL_TYPE_NORMAL/EXTERNAL）は対象外
        journals = [
            self._journal("J1", "2026-07-10", "JOURNAL_TYPE_NORMAL", value=3278, account_name="ツール費"),
            self._journal("J2", "2026-07-11", "JOURNAL_TYPE_EXTERNAL", value=44000, account_name="ツール費"),
        ]
        findings = mf_monthly_check.check_upsider_billing_duplicate(journals, "2026-07")
        self.assertEqual(findings, [])

    def test_ignores_billing_entries_without_upsider(self):
        # 請求書取り込みでもUPSIDER以外の取引先なら対象外
        journals = [self._journal("J1", "2026-07-31", "JOURNAL_TYPE_BILLING", trade_partner_name="株式会社サンプルA")]
        findings = mf_monthly_check.check_upsider_billing_duplicate(journals, "2026-07")
        self.assertEqual(findings, [])

    def test_ignores_entries_outside_target_month(self):
        journals = [self._journal("J1", "2026-06-30", "JOURNAL_TYPE_BILLING")]
        findings = mf_monthly_check.check_upsider_billing_duplicate(journals, "2026-07")
        self.assertEqual(findings, [])

    def test_matches_upsider_on_creditor_side_too(self):
        journal = {
            "id": "J1", "transaction_date": "2026-07-31", "entered_by": "JOURNAL_TYPE_BILLING", "memo": "",
            "branches": [{
                "debitor": {"account_name": "ツール費", "value": 1000},
                "creditor": {"account_name": "未払金", "trade_partner_name": "株式会社UPSIDER", "value": 1000},
            }],
        }
        findings = mf_monthly_check.check_upsider_billing_duplicate([journal], "2026-07")
        self.assertEqual(len(findings), 1)


class TestCheckPrepaidExpenseAmortizationMissing(unittest.TestCase):
    def _entry_journal(self, date, side, value, other_account="ツール費"):
        entry = {"account_name": "前払費用", "value": value}
        other = {"account_name": other_account, "value": value}
        branch = {side: entry, ("creditor" if side == "debitor" else "debitor"): other}
        return {"transaction_date": date, "branches": [branch]}

    def test_flags_when_opening_balance_unamortized_in_target_month(self):
        journals = [
            self._entry_journal("2026-06-01", "debitor", 840000),  # 前月に新規計上、未償却
        ]
        findings = mf_monthly_check.check_prepaid_expense_amortization_missing(journals, "2026-07")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["check"], "prepaid_expense_amortization_missing")
        self.assertEqual(findings[0]["severity"], "B")
        self.assertEqual(findings[0]["opening_balance"], 840000)
        self.assertEqual(findings[0]["credit_this_month"], 0)

    def test_no_finding_when_amortized_in_target_month(self):
        journals = [
            self._entry_journal("2026-06-01", "debitor", 840000),
            self._entry_journal("2026-07-01", "creditor", 168000),  # 対象月に償却あり
        ]
        findings = mf_monthly_check.check_prepaid_expense_amortization_missing(journals, "2026-07")
        self.assertEqual(findings, [])

    def test_no_finding_when_fully_amortized_before_target_month(self):
        journals = [
            self._entry_journal("2026-01-01", "debitor", 100000),
            self._entry_journal("2026-06-01", "creditor", 100000),  # 期首残高ゼロ
        ]
        findings = mf_monthly_check.check_prepaid_expense_amortization_missing(journals, "2026-07")
        self.assertEqual(findings, [])

    def test_no_finding_when_no_prepaid_activity_at_all(self):
        findings = mf_monthly_check.check_prepaid_expense_amortization_missing([], "2026-07")
        self.assertEqual(findings, [])

    def test_ignores_dates_after_target_month(self):
        journals = [
            self._entry_journal("2026-06-01", "debitor", 840000),
            self._entry_journal("2026-08-01", "creditor", 168000),  # 対象月より後は無視
        ]
        findings = mf_monthly_check.check_prepaid_expense_amortization_missing(journals, "2026-07")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["opening_balance"], 840000)


class TestBuildReport(unittest.TestCase):
    def test_includes_summary_counts_and_sections(self):
        findings = [
            {
                "check": "duplicate", "severity": "A",
                "transaction_date": "2026-07-31", "value": 50000,
                "debitor_account_name": "支払手数料", "creditor_account_name": "普通預金",
                "journal_ids": ["J1", "J2"], "remarks": ["振込手数料"],
            },
            {
                "check": "variance", "severity": "B",
                "account_name": "通信費", "current_amount": 150000,
                "historical_avg": 50000.0, "variance_pct": 2.0,
            },
        ]
        report = mf_monthly_check.build_report("2026-07", "株式会社サンプルD", findings)
        self.assertIn("対象会社：株式会社サンプルD", report)
        self.assertIn("対象年月：2026-07", report)
        self.assertIn("A：1件", report)
        self.assertIn("B：1件", report)
        self.assertIn("C：0件", report)
        self.assertIn("## A：要確認", report)
        self.assertIn("## B：確認推奨", report)
        self.assertIn("仕訳の重複候補", report)
        self.assertIn("勘定科目「通信費」の増減", report)

    def test_empty_findings_produces_zero_counts(self):
        report = mf_monthly_check.build_report("2026-07", "株式会社サンプルD", [])
        self.assertIn("A：0件", report)
        self.assertIn("B：0件", report)
        self.assertIn("C：0件", report)

    def test_includes_errors_section_when_errors_present(self):
        report = mf_monthly_check.build_report(
            "2026-07", "株式会社サンプルD", [],
            errors=["BS異常残高チェック: HTTP 500: server error"],
        )
        self.assertIn("## 実行時エラー", report)
        self.assertIn("BS異常残高チェック: HTTP 500: server error", report)

    def test_no_errors_section_when_no_errors(self):
        report = mf_monthly_check.build_report("2026-07", "株式会社サンプルD", [])
        self.assertNotIn("## 実行時エラー", report)

    def test_recurring_missing_and_negative_balance_render_readable_text(self):
        findings = [
            {
                "check": "recurring_missing", "severity": "A",
                "debitor_account_name": "通信費", "creditor_account_name": "普通預金",
                "prior_months_present": ["2026-01", "2026-02", "2026-03",
                                          "2026-04", "2026-05", "2026-06"],
            },
            {
                "check": "negative_balance", "severity": "A",
                "account_name": "売掛金", "closing_balance": -30000,
            },
        ]
        report = mf_monthly_check.build_report("2026-07", "株式会社サンプルD", findings)
        self.assertIn("借方科目：通信費", report)
        self.assertIn("貸方科目：普通預金", report)
        self.assertIn("科目：売掛金", report)
        self.assertIn("残高：-30000円", report)


class TestRunMonthlyCheck(unittest.TestCase):
    def test_produces_expected_findings_end_to_end(self):
        input_data = {
            "target_month": "2026-07",
            "company_name": "株式会社サンプルD",
            "journals": [
                _journal("J1", "2026-07-31", 50000, "支払手数料", "普通預金", "振込手数料"),
                _journal("J2", "2026-07-31", 50000, "支払手数料", "普通預金", "振込手数料"),
                _journal("J3", "2026-06-30", 50000, "支払手数料", "普通預金", "先月分"),
            ],
            "trial_pl_by_month": {
                "2026-01": {"通信費": [0, 50000, 0, 50000, 0]},
                "2026-02": {"通信費": [0, 50000, 0, 50000, 0]},
                "2026-03": {"通信費": [0, 50000, 0, 50000, 0]},
                "2026-04": {"通信費": [0, 50000, 0, 50000, 0]},
                "2026-05": {"通信費": [0, 50000, 0, 50000, 0]},
                "2026-06": {"通信費": [0, 50000, 0, 50000, 0]},
                "2026-07": {"通信費": [0, 150000, 0, 150000, 0]},
            },
            "trial_bs": {"売掛金": [100000, 0, 30000, -30000, 0]},
            "fetch_errors": {"journals": None, "trial_pl": None, "trial_bs": None},
        }
        report = mf_monthly_check.run_monthly_check(input_data)
        self.assertIn("A：2件", report)
        self.assertIn("B：1件", report)
        self.assertIn("C：0件", report)
        self.assertIn("仕訳ID：J1, J2", report)
        self.assertIn("残高：-30000円", report)
        self.assertIn("増減率：+200%", report)

    def test_duplicate_known_safe_pairs_config_suppresses_matching_finding(self):
        # 2026-08-25、オーナーの指示を反映：configのduplicate_known_safe_pairs
        # に含めた組み合わせは、重複チェックのfindingが一切出なくなる。
        input_data = {
            "target_month": "2026-07",
            "company_name": "株式会社サンプルD",
            "journals": [
                _journal("J1", "2026-07-31", 50000, "支払手数料", "普通預金", "振込手数料"),
                _journal("J2", "2026-07-31", 50000, "支払手数料", "普通預金", "振込手数料"),
            ],
            "trial_pl_by_month": {},
            "trial_bs": {},
            "fetch_errors": {"journals": None, "trial_pl": None, "trial_bs": None},
        }
        config = {"duplicate_known_safe_pairs": [["支払手数料", "普通預金"]]}
        report = mf_monthly_check.run_monthly_check(input_data, config)
        self.assertIn("A：0件", report)
        self.assertNotIn("仕訳ID：J1, J2", report)

    def test_isolates_failure_in_one_input_and_continues_others(self):
        input_data = {
            "target_month": "2026-07",
            "company_name": "株式会社サンプルD",
            "journals": None,
            "trial_pl_by_month": {"2026-07": {"通信費": [0, 50000, 0, 50000, 0]}},
            "trial_bs": {"売掛金": [100000, 0, 30000, -30000, 0]},
            "fetch_errors": {
                "journals": "取得失敗：mfc_ca_getJournalsがエラーを返した",
                "trial_pl": None, "trial_bs": None,
            },
        }
        report = mf_monthly_check.run_monthly_check(input_data)
        self.assertIn("## 実行時エラー", report)
        self.assertIn("取得失敗：mfc_ca_getJournalsがエラーを返した", report)
        # journalsが無くても、BSチェックは正常に実行される
        self.assertIn("残高：-30000円", report)

    def test_missing_optional_keys_do_not_crash(self):
        input_data = {
            "target_month": "2026-07",
            "company_name": "株式会社サンプルD",
            "journals": [],
            "trial_pl_by_month": {},
            "trial_bs": {},
            "fetch_errors": {},
        }
        report = mf_monthly_check.run_monthly_check(input_data)
        self.assertIn("A：0件", report)
        self.assertIn("B：0件", report)


class TestCLIArgumentParsing(unittest.TestCase):
    def setUp(self):
        # Import mf_monthly_check_cli from sibling directory
        cli_dir = os.path.join(os.path.dirname(__file__), "..", "lib")
        sys.path.insert(0, cli_dir)
        import mf_monthly_check_cli
        self.mf_monthly_check_cli = mf_monthly_check_cli

    def test_parses_flags_first_then_subcommand(self):
        """Verify the exact SKILL.md documented invocation works:
        --input <path> --output <path> (no subcommand needed)"""
        parser = self.mf_monthly_check_cli.build_parser()
        # SKILL.md documents: python cli.py --input ... --output ...
        args = parser.parse_args(["--input", "input.json", "--output", "output.md"])
        self.assertEqual(args.input, "input.json")
        self.assertEqual(args.output, "output.md")

    def test_input_and_output_are_required(self):
        """Verify --input and --output are required arguments."""
        parser = self.mf_monthly_check_cli.build_parser()
        # Missing --input should fail
        with self.assertRaises(SystemExit):
            parser.parse_args(["--output", "output.md"])
        # Missing --output should fail
        with self.assertRaises(SystemExit):
            parser.parse_args(["--input", "input.json"])

    def test_bare_filename_without_directory(self):
        """Verify parsing works when --output has no directory component."""
        parser = self.mf_monthly_check_cli.build_parser()
        args = parser.parse_args(["--input", "input.json", "--output", "output.md"])
        # Should parse successfully even though output.md has no directory path
        self.assertEqual(args.output, "output.md")


if __name__ == "__main__":
    unittest.main()
