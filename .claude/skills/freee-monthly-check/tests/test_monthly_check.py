import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import monthly_check


class TestCheckDuplicates(unittest.TestCase):
    def test_detects_matching_pair(self):
        deals = [
            {
                "id": 1, "issue_date": "2026-07-31", "amount": 50000,
                "partner_id": 100, "type": "income",
                "details": [{"description": "業務委託費用"}],
            },
            {
                "id": 2, "issue_date": "2026-07-31", "amount": 50000,
                "partner_id": 100, "type": "income",
                "details": [{"description": "業務委託費用(再登録)"}],
            },
        ]
        findings = monthly_check.check_duplicates(deals)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["check"], "duplicate")
        self.assertEqual(findings[0]["severity"], "A")
        self.assertEqual(findings[0]["deal_ids"], [1, 2])
        self.assertEqual(
            findings[0]["descriptions"], ["業務委託費用", "業務委託費用(再登録)"]
        )

    def test_ignores_unique_deals(self):
        deals = [
            {
                "id": 1, "issue_date": "2026-07-31", "amount": 50000,
                "partner_id": 100, "type": "income", "details": [],
            },
            {
                "id": 2, "issue_date": "2026-07-31", "amount": 60000,
                "partner_id": 100, "type": "income", "details": [],
            },
        ]
        self.assertEqual(monthly_check.check_duplicates(deals), [])

    def test_treats_different_partner_as_distinct(self):
        deals = [
            {
                "id": 1, "issue_date": "2026-07-31", "amount": 50000,
                "partner_id": 100, "type": "income", "details": [],
            },
            {
                "id": 2, "issue_date": "2026-07-31", "amount": 50000,
                "partner_id": 200, "type": "income", "details": [],
            },
        ]
        self.assertEqual(monthly_check.check_duplicates(deals), [])


class TestCheckRecurringMissing(unittest.TestCase):
    def _deal(self, deal_id, month_day, partner_id, account_item_id):
        return {
            "id": deal_id,
            "issue_date": f"{month_day}",
            "partner_id": partner_id,
            "details": [{"account_item_id": account_item_id}],
        }

    def test_flags_missing_when_present_in_all_prior_months(self):
        deals = [
            self._deal(1, "2026-01-15", 100, 200),
            self._deal(2, "2026-02-15", 100, 200),
            self._deal(3, "2026-03-15", 100, 200),
            self._deal(4, "2026-04-15", 100, 200),
            self._deal(5, "2026-05-15", 100, 200),
            self._deal(6, "2026-06-15", 100, 200),
            # 2026-07には(100, 200)の取引が無い
        ]
        findings = monthly_check.check_recurring_missing(deals, "2026-07")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["check"], "recurring_missing")
        self.assertEqual(findings[0]["severity"], "A")
        self.assertEqual(findings[0]["partner_id"], 100)
        self.assertEqual(findings[0]["account_item_id"], 200)

    def test_does_not_flag_when_present_in_target_month(self):
        deals = [
            self._deal(1, "2026-01-15", 100, 200),
            self._deal(2, "2026-02-15", 100, 200),
            self._deal(3, "2026-03-15", 100, 200),
            self._deal(4, "2026-04-15", 100, 200),
            self._deal(5, "2026-05-15", 100, 200),
            self._deal(6, "2026-06-15", 100, 200),
            self._deal(7, "2026-07-15", 100, 200),
        ]
        self.assertEqual(
            monthly_check.check_recurring_missing(deals, "2026-07"), []
        )

    def test_does_not_flag_when_not_present_in_all_prior_months(self):
        deals = [
            self._deal(1, "2026-01-15", 100, 200),
            self._deal(2, "2026-02-15", 100, 200),
            self._deal(3, "2026-03-15", 100, 200),
            self._deal(4, "2026-04-15", 100, 200),
            self._deal(5, "2026-05-15", 100, 200),
            # 2026-06は無い(6ヶ月中5ヶ月のみ発生)
        ]
        self.assertEqual(
            monthly_check.check_recurring_missing(deals, "2026-07"), []
        )


def _pl_row(account_item_id, name, closing_balance, total_line=False):
    if total_line:
        return {
            "account_category_name": name,
            "total_line": True,
            "closing_balance": closing_balance,
        }
    return {
        "account_item_id": account_item_id,
        "account_item_name": name,
        "closing_balance": closing_balance,
    }


class TestCheckVariance(unittest.TestCase):
    def test_flags_large_increase_over_threshold(self):
        balances_by_month = {
            "2026-01": [_pl_row(300, "通信費", 50000)],
            "2026-02": [_pl_row(300, "通信費", 52000)],
            "2026-03": [_pl_row(300, "通信費", 49000)],
            "2026-04": [_pl_row(300, "通信費", 51000)],
            "2026-05": [_pl_row(300, "通信費", 50000)],
            "2026-06": [_pl_row(300, "通信費", 50000)],
            "2026-07": [_pl_row(300, "通信費", 150000)],
        }
        findings = monthly_check.check_variance(balances_by_month, "2026-07")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["check"], "variance")
        self.assertEqual(findings[0]["severity"], "B")
        self.assertEqual(findings[0]["account_item_name"], "通信費")
        self.assertEqual(findings[0]["current_amount"], 150000)
        self.assertAlmostEqual(findings[0]["historical_avg"], 50333.33, places=1)
        self.assertGreater(findings[0]["variance_pct"], 0.30)

    def test_does_not_flag_within_threshold(self):
        balances_by_month = {
            "2026-01": [_pl_row(300, "通信費", 50000)],
            "2026-02": [_pl_row(300, "通信費", 50000)],
            "2026-07": [_pl_row(300, "通信費", 55000)],
        }
        self.assertEqual(
            monthly_check.check_variance(balances_by_month, "2026-07"), []
        )

    def test_flags_new_account_item_when_historical_avg_zero(self):
        balances_by_month = {
            "2026-01": [_pl_row(400, "雑費", 0)],
            "2026-07": [_pl_row(400, "雑費", 30000)],
        }
        findings = monthly_check.check_variance(balances_by_month, "2026-07")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["check"], "variance_new")
        self.assertEqual(findings[0]["current_amount"], 30000)

    def test_resolves_account_name_when_target_month_has_no_row(self):
        # 対象月のtrial_plにその科目の行が一件も無い(ゼロ発生)ケース。
        # 過去月には行があるので、そこから科目名を復元できるはず。
        balances_by_month = {
            "2026-01": [_pl_row(300, "通信費", 50000)],
            "2026-02": [_pl_row(300, "通信費", 52000)],
            "2026-07": [],  # 対象月には行そのものが無い
        }
        findings = monthly_check.check_variance(balances_by_month, "2026-07")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["account_item_name"], "通信費")
        self.assertEqual(findings[0]["current_amount"], 0)

    def test_ignores_total_line_rows(self):
        balances_by_month = {
            "2026-01": [_pl_row(None, "売上高", 200000, total_line=True)],
            "2026-07": [_pl_row(None, "売上高", 900000, total_line=True)],
        }
        self.assertEqual(
            monthly_check.check_variance(balances_by_month, "2026-07"), []
        )

    def test_variance_pct_is_positive_when_negative_average_increases(self):
        # 過去平均がマイナス(-60000円)で当月が0円＝ゼロに向かって「増加」した。
        # historical_avgで割ると符号が反転して-100%になってしまうため、abs()で割る。
        balances_by_month = {
            "2026-01": [_pl_row(310, "貸倒繰入額(販)", -60000)],
            "2026-02": [_pl_row(310, "貸倒繰入額(販)", -60000)],
            "2026-07": [_pl_row(310, "貸倒繰入額(販)", 0)],
        }
        findings = monthly_check.check_variance(balances_by_month, "2026-07")
        self.assertEqual(len(findings), 1)
        self.assertAlmostEqual(findings[0]["variance_pct"], 1.0)
        detail = monthly_check.format_finding_detail(findings[0])
        self.assertIn("増減率：+100%", detail)

    def test_variance_pct_is_negative_when_negative_average_decreases(self):
        # 過去平均-60000円から当月-120000円へ＝さらにマイナス方向に「減少」した。
        balances_by_month = {
            "2026-01": [_pl_row(310, "貸倒繰入額(販)", -60000)],
            "2026-02": [_pl_row(310, "貸倒繰入額(販)", -60000)],
            "2026-07": [_pl_row(310, "貸倒繰入額(販)", -120000)],
        }
        findings = monthly_check.check_variance(balances_by_month, "2026-07")
        self.assertEqual(len(findings), 1)
        self.assertAlmostEqual(findings[0]["variance_pct"], -1.0)

    def test_skips_immaterial_account_below_materiality_floor(self):
        # 増減率は-100%だが、金額が重要性の基準(デフォルト10000円)未満なのでノイズ。
        balances_by_month = {
            "2026-01": [_pl_row(320, "受取利息", 600)],
            "2026-02": [_pl_row(320, "受取利息", 600)],
            "2026-07": [_pl_row(320, "受取利息", 0)],
        }
        self.assertEqual(
            monthly_check.check_variance(balances_by_month, "2026-07"), []
        )

    def test_flags_same_swing_when_above_materiality_floor(self):
        # 上のテストと同じ-100%でも、金額が基準以上なら検出する。
        balances_by_month = {
            "2026-01": [_pl_row(320, "受取利息", 60000)],
            "2026-02": [_pl_row(320, "受取利息", 60000)],
            "2026-07": [_pl_row(320, "受取利息", 0)],
        }
        findings = monthly_check.check_variance(balances_by_month, "2026-07")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["account_item_name"], "受取利息")

    def test_skips_immaterial_new_account(self):
        # variance_new(過去平均0)にも重要性の基準を適用する。
        balances_by_month = {
            "2026-01": [_pl_row(400, "雑費", 0)],
            "2026-07": [_pl_row(400, "雑費", 500)],
        }
        self.assertEqual(
            monthly_check.check_variance(balances_by_month, "2026-07"), []
        )

    def test_materiality_floor_is_configurable(self):
        balances_by_month = {
            "2026-01": [_pl_row(320, "受取利息", 600)],
            "2026-02": [_pl_row(320, "受取利息", 600)],
            "2026-07": [_pl_row(320, "受取利息", 0)],
        }
        findings = monthly_check.check_variance(
            balances_by_month, "2026-07", variance_materiality_floor=100
        )
        self.assertEqual(len(findings), 1)


def _bs_row(account_item_id, name, closing_balance, partners=None):
    return {
        "account_item_id": account_item_id,
        "account_item_name": name,
        "closing_balance": closing_balance,
        "partners": partners or [],
    }


class TestCheckNegativeBalance(unittest.TestCase):
    def test_flags_negative_total_on_target_account(self):
        bs_balances = [
            _bs_row(500, "売掛金", -30000),
            _bs_row(501, "現金", -1000),  # 対象科目外なので無視される
        ]
        findings = monthly_check.check_negative_balance(bs_balances)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["check"], "negative_balance")
        self.assertEqual(findings[0]["severity"], "A")
        self.assertEqual(findings[0]["account_item_name"], "売掛金")
        self.assertIsNone(findings[0]["partner_id"])
        self.assertEqual(findings[0]["closing_balance"], -30000)

    def test_flags_negative_partner_breakdown(self):
        bs_balances = [
            _bs_row(500, "売掛金", 20000, partners=[
                {"id": 1, "name": "株式会社A", "closing_balance": 50000},
                {"id": 2, "name": "株式会社B", "closing_balance": -30000},
            ]),
        ]
        findings = monthly_check.check_negative_balance(bs_balances)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["partner_id"], 2)
        self.assertEqual(findings[0]["partner_name"], "株式会社B")
        self.assertEqual(findings[0]["closing_balance"], -30000)

    def test_does_not_flag_positive_balances(self):
        bs_balances = [
            _bs_row(500, "売掛金", 50000, partners=[
                {"id": 1, "name": "株式会社A", "closing_balance": 50000},
            ]),
        ]
        self.assertEqual(monthly_check.check_negative_balance(bs_balances), [])


def _recurring_finding(partner_id=100, account_item_id=200):
    return {
        "check": "recurring_missing", "severity": "A",
        "partner_id": partner_id, "account_item_id": account_item_id,
        "prior_months_present": ["2026-05", "2026-06"],
    }


class TestResolveFindingNames(unittest.TestCase):
    def test_fills_in_names_from_lookup_maps(self):
        findings = [_recurring_finding()]
        monthly_check.resolve_finding_names(
            findings,
            account_item_names={200: "通信費"},
            partner_names={100: "株式会社ABC"},
        )
        self.assertEqual(findings[0]["partner_name"], "株式会社ABC")
        self.assertEqual(findings[0]["account_item_name"], "通信費")
        detail = monthly_check.format_finding_detail(findings[0])
        self.assertIn("取引先：株式会社ABC", detail)
        self.assertIn("勘定科目：通信費", detail)

    def test_falls_back_to_raw_id_when_name_unknown(self):
        findings = [_recurring_finding()]
        monthly_check.resolve_finding_names(findings, {}, {})
        detail = monthly_check.format_finding_detail(findings[0])
        self.assertIn("取引先：(名称不明、ID: 100)", detail)
        self.assertIn("勘定科目：(名称不明、ID: 200)", detail)

    def test_format_detail_without_resolution_does_not_crash(self):
        # resolve_finding_namesを通していないfinding（キーそのものが無い）でも壊れない。
        detail = monthly_check.format_finding_detail(_recurring_finding())
        self.assertIn("(名称不明、ID: 100)", detail)

    def test_collect_account_item_names_ignores_total_lines(self):
        names = monthly_check.collect_account_item_names([
            _pl_row(300, "通信費", 1000),
            _pl_row(None, "売上高", 2000, total_line=True),
        ])
        self.assertEqual(names, {300: "通信費"})


class TestBuildReport(unittest.TestCase):
    def test_includes_summary_counts_and_sections(self):
        findings = [
            {
                "check": "duplicate", "severity": "A",
                "issue_date": "2026-07-31", "amount": 50000,
                "partner_id": 100, "type": "income",
                "deal_ids": [1, 2], "descriptions": ["業務委託費用"],
            },
            {
                "check": "variance", "severity": "B",
                "account_item_id": 300, "account_item_name": "通信費",
                "current_amount": 150000, "historical_avg": 50000.0,
                "variance_pct": 2.0,
            },
        ]
        report = monthly_check.build_report("2026-07", "サンプルB合同会社", findings)
        self.assertIn("対象会社：サンプルB合同会社", report)
        self.assertIn("対象年月：2026-07", report)
        self.assertIn("A：1件", report)
        self.assertIn("B：1件", report)
        self.assertIn("C：0件", report)
        self.assertIn("## A：要確認", report)
        self.assertIn("## B：確認推奨", report)
        self.assertIn("取引の重複候補", report)
        self.assertIn("勘定科目「通信費」の増減", report)

    def test_empty_findings_produces_zero_counts(self):
        report = monthly_check.build_report("2026-07", "サンプルB合同会社", [])
        self.assertIn("A：0件", report)
        self.assertIn("B：0件", report)
        self.assertIn("C：0件", report)

    def test_includes_errors_section_when_errors_present(self):
        report = monthly_check.build_report(
            "2026-07", "サンプルB合同会社", [],
            errors=["BS異常残高チェック: HTTP 500: server error"],
        )
        self.assertIn("## 実行時エラー", report)
        self.assertIn("以下のチェックは実行できませんでした（エラー内容は各項目を参照）：", report)
        self.assertNotIn("データ取得に失敗したため", report)
        self.assertIn("BS異常残高チェック: HTTP 500: server error", report)

    def test_no_errors_section_when_no_errors(self):
        report = monthly_check.build_report("2026-07", "サンプルB合同会社", [])
        self.assertNotIn("## 実行時エラー", report)


class TestMonthHelpers(unittest.TestCase):
    def test_month_range_returns_oldest_first_including_target(self):
        self.assertEqual(
            monthly_check.month_range("2026-07", 3),
            ["2026-05", "2026-06", "2026-07"],
        )

    def test_month_range_crosses_year_boundary(self):
        self.assertEqual(
            monthly_check.month_range("2026-02", 3),
            ["2025-12", "2026-01", "2026-02"],
        )

    def test_month_bounds_returns_first_and_last_day(self):
        self.assertEqual(
            monthly_check.month_bounds("2026-02"), ("2026-02-01", "2026-02-28")
        )
        self.assertEqual(
            monthly_check.month_bounds("2026-04"), ("2026-04-01", "2026-04-30")
        )


class TestFetchLayer(unittest.TestCase):
    def test_fetch_deals_for_months_paginates_until_short_page(self):
        calls = []

        def fake_api_call(token, company_id, method, path, json_body=None):
            calls.append(path)
            if "offset=0" in path:
                return {"deals": [{"id": i} for i in range(100)]}
            return {"deals": [{"id": 100}]}

        deals = monthly_check.fetch_deals_for_months(
            fake_api_call, "TOKEN", 999, ["2026-07"]
        )
        self.assertEqual(len(deals), 101)
        self.assertEqual(len(calls), 2)
        self.assertIn("start_issue_date=2026-07-01", calls[0])
        self.assertIn("end_issue_date=2026-07-31", calls[0])

    def test_fetch_trial_pl_for_month_builds_correct_path(self):
        captured = {}

        def fake_api_call(token, company_id, method, path, json_body=None):
            captured["path"] = path
            return {"trial_pl": {"balances": [{"account_item_id": 1}]}}

        result = monthly_check.fetch_trial_pl_for_month(
            fake_api_call, "TOKEN", 999, "2026-07"
        )
        self.assertEqual(result, [{"account_item_id": 1}])
        self.assertIn("start_date=2026-07-01", captured["path"])
        self.assertIn("end_date=2026-07-31", captured["path"])

    def test_fetch_fiscal_year_start_picks_matching_year(self):
        def fake_api_call(token, company_id, method, path, json_body=None):
            return {
                "company": {
                    "fiscal_years": [
                        {"start_date": "2025-06-01", "end_date": "2026-05-31"},
                        {"start_date": "2026-06-01", "end_date": "2027-05-31"},
                    ]
                }
            }

        start = monthly_check.fetch_fiscal_year_start(
            fake_api_call, "TOKEN", 999, "2026-07"
        )
        self.assertEqual(start, "2026-06-01")

    def test_fetch_trial_bs_for_month_uses_fiscal_year_start_and_partner_breakdown(self):
        captured = {}

        def fake_api_call(token, company_id, method, path, json_body=None):
            captured["path"] = path
            return {"trial_bs": {"balances": [{"account_item_id": 2}]}}

        result = monthly_check.fetch_trial_bs_for_month(
            fake_api_call, "TOKEN", 999, "2026-07", "2026-06-01"
        )
        self.assertEqual(result, [{"account_item_id": 2}])
        self.assertIn("start_date=2026-06-01", captured["path"])
        self.assertIn("end_date=2026-07-31", captured["path"])
        self.assertIn("breakdown_display_type=partner", captured["path"])


    def test_fetch_partner_names_returns_id_to_name_map_with_paging(self):
        calls = []

        def fake_api_call(token, company_id, method, path, json_body=None):
            calls.append(path)
            if "offset=0" in path:
                return {"partners": [
                    {"id": i, "name": f"取引先{i}"} for i in range(100)
                ]}
            return {"partners": [{"id": 100, "name": "取引先100"}]}

        names = monthly_check.fetch_partner_names(fake_api_call, "TOKEN", 999)
        self.assertEqual(len(names), 101)
        self.assertEqual(names[100], "取引先100")
        self.assertEqual(len(calls), 2)


class TestRunMonthlyCheck(unittest.TestCase):
    def test_isolates_failure_in_one_check_and_continues_others(self):
        def fake_api_call(token, company_id, method, path, json_body=None):
            if path.startswith("/api/1/deals"):
                return {"deals": []}
            if path.startswith("/api/1/reports/trial_pl"):
                raise RuntimeError("HTTP 500: server error")
            if path.startswith(f"/api/1/companies/{company_id}"):
                return {
                    "company": {
                        "fiscal_years": [
                            {"start_date": "2026-06-01", "end_date": "2027-05-31"}
                        ]
                    }
                }
            if path.startswith("/api/1/reports/trial_bs"):
                return {"trial_bs": {"balances": []}}
            raise AssertionError(f"unexpected path: {path}")

        report = monthly_check.run_monthly_check(
            fake_api_call, "TOKEN", 999, "テスト会社", "2026-07"
        )
        self.assertIn("## 実行時エラー", report)
        self.assertIn("勘定科目の増減チェック", report)
        self.assertIn("HTTP 500: server error", report)
        # 他のチェック(BS異常残高チェック)は正常に完了しているのでA/B/Cサマリーは出る
        self.assertIn("A：0件", report)

    def test_variance_window_is_independent_of_recurring_min_months(self):
        # recurring_min_monthsを3に下げても、増減チェックの過去平均は
        # variance_history_months(デフォルト6)ヶ月分のままであること。
        pl_months = []
        deals_paths = []

        def fake_api_call(token, company_id, method, path, json_body=None):
            if path.startswith("/api/1/deals"):
                deals_paths.append(path)
                return {"deals": []}
            if path.startswith("/api/1/reports/trial_pl"):
                pl_months.append(path.split("start_date=")[1][:7])
                return {"trial_pl": {"balances": []}}
            if path.startswith(f"/api/1/companies/{company_id}"):
                return {"company": {"fiscal_years": [
                    {"start_date": "2026-06-01", "end_date": "2027-05-31"}
                ]}}
            if path.startswith("/api/1/reports/trial_bs"):
                return {"trial_bs": {"balances": []}}
            raise AssertionError(f"unexpected path: {path}")

        monthly_check.run_monthly_check(
            fake_api_call, "TOKEN", 999, "テスト会社", "2026-07",
            {"recurring_min_months": 3},
        )
        self.assertEqual(
            pl_months,
            ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07"],
        )
        # dealsはrecurring_min_months+1=4ヶ月分だけ（PLの窓に引きずられない）
        self.assertIn("start_issue_date=2026-04-01", deals_paths[0])

    def test_variance_history_months_is_configurable(self):
        pl_months = []

        def fake_api_call(token, company_id, method, path, json_body=None):
            if path.startswith("/api/1/deals"):
                return {"deals": []}
            if path.startswith("/api/1/reports/trial_pl"):
                pl_months.append(path.split("start_date=")[1][:7])
                return {"trial_pl": {"balances": []}}
            if path.startswith(f"/api/1/companies/{company_id}"):
                return {"company": {"fiscal_years": [
                    {"start_date": "2026-06-01", "end_date": "2027-05-31"}
                ]}}
            if path.startswith("/api/1/reports/trial_bs"):
                return {"trial_bs": {"balances": []}}
            raise AssertionError(f"unexpected path: {path}")

        monthly_check.run_monthly_check(
            fake_api_call, "TOKEN", 999, "テスト会社", "2026-07",
            {"variance_history_months": 2},
        )
        self.assertEqual(pl_months, ["2026-05", "2026-06", "2026-07"])

    def test_produces_expected_findings_end_to_end(self):
        def _deal(deal_id, issue_date, amount, description):
            return {
                "id": deal_id, "issue_date": issue_date, "amount": amount,
                "partner_id": 100, "type": "expense",
                "details": [{"account_item_id": 300, "description": description}],
            }

        def fake_api_call(token, company_id, method, path, json_body=None):
            if path.startswith("/api/1/deals"):
                return {"deals": [
                    # 対象月(2026-07)の重複ペア → 検出されるべき
                    _deal(11, "2026-07-10", 80000, "7月分家賃"),
                    _deal(12, "2026-07-10", 80000, "7月分家賃(再登録)"),
                    # 前月(2026-06)の同型ペア → 対象月フィルタで除外されるべき
                    _deal(21, "2026-06-10", 80000, "6月分家賃"),
                    _deal(22, "2026-06-10", 80000, "6月分家賃(再登録)"),
                ]}
            if path.startswith("/api/1/reports/trial_pl"):
                month = path.split("start_date=")[1][:7]
                amount = 150000 if month == "2026-07" else 50000
                return {"trial_pl": {"balances": [
                    {"account_item_id": 300, "account_item_name": "通信費",
                     "closing_balance": amount},
                    {"account_item_id": 301, "account_item_name": "売上高",
                     "closing_balance": 1000000},
                ]}}
            if path.startswith(f"/api/1/companies/{company_id}"):
                return {"company": {"fiscal_years": [
                    {"start_date": "2026-06-01", "end_date": "2027-05-31"}
                ]}}
            if path.startswith("/api/1/reports/trial_bs"):
                return {"trial_bs": {"balances": [
                    {"account_item_id": 500, "account_item_name": "売掛金",
                     "closing_balance": -30000, "partners": []},
                ]}}
            raise AssertionError(f"unexpected path: {path}")

        report = monthly_check.run_monthly_check(
            fake_api_call, "TOKEN", 999, "テスト会社", "2026-07"
        )
        self.assertNotIn("## 実行時エラー", report)
        # A: 重複1件 + マイナス残高1件 / B: 通信費の増減1件（売上高は横ばいで対象外）
        self.assertIn("A：2件", report)
        self.assertIn("B：1件", report)
        self.assertIn("C：0件", report)
        # 重複は対象月のペアだけ
        self.assertIn("取引ID：11, 12", report)
        self.assertIn("7月分家賃", report)
        self.assertNotIn("6月分家賃", report)
        self.assertNotIn("取引ID：21, 22", report)
        # 増減
        self.assertIn("勘定科目「通信費」の増減", report)
        self.assertIn("増減率：+200%", report)
        self.assertNotIn("勘定科目「売上高」の増減", report)
        # マイナス残高
        self.assertIn("「売掛金」のマイナス残高", report)
        self.assertIn("残高：-30000円", report)

    def test_resolves_names_for_recurring_missing_finding(self):
        def _deal(deal_id, issue_date):
            return {
                "id": deal_id, "issue_date": issue_date, "partner_id": 97764408,
                "amount": 33000, "type": "expense",
                "details": [{"account_item_id": 846101262, "description": "顧問料"}],
            }

        def fake_api_call(token, company_id, method, path, json_body=None):
            if path.startswith("/api/1/deals"):
                return {"deals": [
                    _deal(i + 1, f"2026-0{i + 1}-20") for i in range(6)
                ]}  # 2026-01〜2026-06のみ、対象月(2026-07)は無い
            if path.startswith("/api/1/reports/trial_pl"):
                return {"trial_pl": {"balances": [
                    {"account_item_id": 846101262, "account_item_name": "支払報酬料",
                     "closing_balance": 300000},
                ]}}
            if path.startswith(f"/api/1/companies/{company_id}"):
                return {"company": {"fiscal_years": [
                    {"start_date": "2026-06-01", "end_date": "2027-05-31"}
                ]}}
            if path.startswith("/api/1/reports/trial_bs"):
                return {"trial_bs": {"balances": []}}
            if path.startswith("/api/1/partners"):
                return {"partners": [
                    {"id": 97764408, "name": "サンプルC合同会社"},
                ]}
            raise AssertionError(f"unexpected path: {path}")

        report = monthly_check.run_monthly_check(
            fake_api_call, "TOKEN", 999, "テスト会社", "2026-07"
        )
        self.assertIn("A：1件", report)
        self.assertIn("取引先：サンプルC合同会社", report)
        self.assertIn("勘定科目：支払報酬料", report)
        self.assertNotIn("取引先ID：", report)

    def test_falls_back_to_ids_when_partner_lookup_fails(self):
        def _deal(deal_id, issue_date):
            return {
                "id": deal_id, "issue_date": issue_date, "partner_id": 97764408,
                "amount": 33000, "type": "expense",
                "details": [{"account_item_id": 846101262, "description": "顧問料"}],
            }

        def fake_api_call(token, company_id, method, path, json_body=None):
            if path.startswith("/api/1/deals"):
                return {"deals": [
                    _deal(i + 1, f"2026-0{i + 1}-20") for i in range(6)
                ]}
            if path.startswith("/api/1/reports/trial_pl"):
                return {"trial_pl": {"balances": [
                    {"account_item_id": 846101262, "account_item_name": "支払報酬料",
                     "closing_balance": 300000},
                ]}}
            if path.startswith(f"/api/1/companies/{company_id}"):
                return {"company": {"fiscal_years": [
                    {"start_date": "2026-06-01", "end_date": "2027-05-31"}
                ]}}
            if path.startswith("/api/1/reports/trial_bs"):
                return {"trial_bs": {"balances": []}}
            if path.startswith("/api/1/partners"):
                raise RuntimeError("HTTP 403: forbidden")
            raise AssertionError(f"unexpected path: {path}")

        report = monthly_check.run_monthly_check(
            fake_api_call, "TOKEN", 999, "テスト会社", "2026-07"
        )
        # 取引先名が引けなくても落ちず、勘定科目名はPLから解決できている
        self.assertIn("取引先：(名称不明、ID: 97764408)", report)
        self.assertIn("勘定科目：支払報酬料", report)
        self.assertIn("HTTP 403: forbidden", report)

    def test_does_not_fetch_partners_when_no_recurring_findings(self):
        def fake_api_call(token, company_id, method, path, json_body=None):
            if path.startswith("/api/1/deals"):
                return {"deals": []}
            if path.startswith("/api/1/reports/trial_pl"):
                return {"trial_pl": {"balances": []}}
            if path.startswith(f"/api/1/companies/{company_id}"):
                return {"company": {"fiscal_years": [
                    {"start_date": "2026-06-01", "end_date": "2027-05-31"}
                ]}}
            if path.startswith("/api/1/reports/trial_bs"):
                return {"trial_bs": {"balances": []}}
            raise AssertionError(f"unexpected path: {path}")

        report = monthly_check.run_monthly_check(
            fake_api_call, "TOKEN", 999, "テスト会社", "2026-07"
        )
        self.assertNotIn("## 実行時エラー", report)


if __name__ == "__main__":
    unittest.main()


class TestCheckMissingPartnerOrItem(unittest.TestCase):
    """monthly-closing-checklist.md 項目8の注記（2026-08-31）：freeeには補助科目が無いので、
    取引先(partner_id)・品目(item_id)の付け忘れを同じ考え方で拾う。"""

    @staticmethod
    def _d(did, date, account, desc, partner=None, item=None):
        return {
            "id": did, "issue_date": date, "amount": 1000, "type": "expense",
            "partner_id": partner,
            "details": [{"account_item_id": account, "description": desc, "item_id": item}],
        }

    def test_flags_missing_partner(self):
        deals = [
            self._d(1, "2026-05-31", 500, "通信サービス利用料", partner=10),
            self._d(2, "2026-06-30", 500, "通信サービス利用料", partner=10),
            self._d(3, "2026-07-31", 500, "通信サービス利用料", partner=None),
        ]
        findings = monthly_check.check_missing_partner_or_item(deals, "2026-07", lookback_months=2)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f["check"], "missing_subaccount")
        self.assertEqual(f["severity"], "B")
        self.assertEqual(f["attribute"], "partner_id")
        self.assertEqual(f["account_item_id"], 500)
        self.assertEqual(f["key_label"], "通信サービス利用料")
        self.assertEqual(f["deal_ids"], [3])
        self.assertEqual(f["prior_values"], [10])
        self.assertEqual(f["prior_months_present"], ["2026-05", "2026-06"])

    def test_flags_missing_item_independently(self):
        deals = [
            self._d(1, "2026-05-31", 500, "通信サービス利用料", partner=10, item=7),
            self._d(2, "2026-06-30", 500, "通信サービス利用料", partner=10, item=7),
            self._d(3, "2026-07-31", 500, "通信サービス利用料", partner=10, item=None),
        ]
        findings = monthly_check.check_missing_partner_or_item(deals, "2026-07")
        self.assertEqual([f["attribute"] for f in findings], ["item_id"])

    def test_does_not_flag_when_prior_months_inconsistent(self):
        deals = [
            self._d(1, "2026-05-31", 500, "通信サービス利用料", partner=10),
            self._d(2, "2026-06-30", 500, "通信サービス利用料", partner=None),
            self._d(3, "2026-07-31", 500, "通信サービス利用料", partner=None),
        ]
        self.assertEqual(monthly_check.check_missing_partner_or_item(deals, "2026-07"), [])

    def test_does_not_flag_when_target_has_value(self):
        deals = [
            self._d(1, "2026-06-30", 500, "通信サービス利用料", partner=10),
            self._d(3, "2026-07-31", 500, "通信サービス利用料", partner=10),
        ]
        self.assertEqual(monthly_check.check_missing_partner_or_item(deals, "2026-07"), [])

    def test_skips_details_without_description(self):
        deals = [
            self._d(1, "2026-06-30", 500, "", partner=10),
            self._d(3, "2026-07-31", 500, "", partner=None),
        ]
        self.assertEqual(monthly_check.check_missing_partner_or_item(deals, "2026-07"), [])

    def test_resolve_and_format_use_names(self):
        deals = [
            self._d(1, "2026-05-31", 500, "通信サービス利用料", partner=10),
            self._d(2, "2026-06-30", 500, "通信サービス利用料", partner=10),
            self._d(3, "2026-07-31", 500, "通信サービス利用料", partner=None),
        ]
        findings = monthly_check.check_missing_partner_or_item(deals, "2026-07")
        monthly_check.resolve_finding_names(findings, {500: "通信費"}, {10: "通信A社"})
        self.assertEqual(monthly_check.describe_finding(findings[0]), "「通信費」の取引先の付け忘れ候補")
        detail = monthly_check.format_finding_detail(findings[0])
        self.assertIn("通信A社", detail)
        self.assertIn("通信サービス利用料", detail)
