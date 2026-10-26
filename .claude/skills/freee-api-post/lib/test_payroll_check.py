import datetime
import unittest
from unittest import mock

from payroll_check import (
    pay_month, to_int, summarize, diff_statements,
    build_remind_message, build_check_message, should_notify,
)
import payroll_check

STMT = {
    "employee_display_name": "従業員A",
    "fixed": False,
    "pay_calc_type": "monthly",
    "gross_payment_amount": "63,000",
    "net_payment_amount": "51521",
    "deductions": [
        {"name": "健康保険", "amount": "3349"},
        {"name": "厚生年金", "amount": "8052"},
        {"name": "雇用保険", "amount": "78"},
    ],
}


class TestPayMonth(unittest.TestCase):
    def test_offset_zero_returns_current_month(self):
        self.assertEqual(pay_month(datetime.date(2026, 8, 20), 0), (2026, 8))

    def test_offset_one_returns_next_month(self):
        self.assertEqual(pay_month(datetime.date(2026, 12, 20), 1), (2027, 1))

    def test_offset_minus_one_crosses_year(self):
        self.assertEqual(pay_month(datetime.date(2026, 1, 1), -1), (2025, 12))


class TestToInt(unittest.TestCase):
    def test_plain_and_comma_and_empty(self):
        self.assertEqual(to_int("63000"), 63000)
        self.assertEqual(to_int("63,000"), 63000)
        self.assertEqual(to_int(""), 0)
        self.assertEqual(to_int(None), 0)

    def test_decimal_string_from_hr_api(self):
        # 人事労務APIは金額を "63000.0" のような小数文字列で返す（2026-08-31実データで確認）
        self.assertEqual(to_int("63000.0"), 63000)


class TestSummarize(unittest.TestCase):
    def test_basic_fields(self):
        s = summarize(STMT)
        self.assertEqual(s["employee"], "従業員A")
        self.assertFalse(s["fixed"])
        self.assertEqual(s["gross"], 63000)
        self.assertEqual(s["net"], 51521)
        self.assertEqual(s["deductions"]["健康保険"], 3349)
        self.assertFalse(s["hourly_zero"])

    def test_hourly_zero_flag(self):
        stmt = dict(STMT, pay_calc_type="hourly", gross_payment_amount="0")
        self.assertTrue(summarize(stmt)["hourly_zero"])


class TestDiff(unittest.TestCase):
    def test_no_diff_returns_empty(self):
        cur = [summarize(STMT)]
        prev = [summarize(STMT)]
        self.assertEqual(diff_statements(cur, prev), [])

    def test_deduction_change_reported(self):
        cur = [summarize(dict(STMT, deductions=[{"name": "健康保険", "amount": "3500"}]))]
        prev = [summarize(STMT)]
        out = diff_statements(cur, prev)
        self.assertTrue(any("健康保険" in d and "3,349" in d and "3,500" in d for d in out))

    def test_new_employee_marked(self):
        out = diff_statements([summarize(STMT)], [])
        self.assertTrue(any("新規" in d for d in out))


class TestMessages(unittest.TestCase):
    def test_remind_mentions_and_url(self):
        msg = build_remind_message(
            "サンプルA株式会社", 2026, 8, [summarize(STMT)], [],
            "https://p.secure.freee.co.jp/", "<@UXXXXXXXX>")
        self.assertIn("<@UXXXXXXXX>", msg)
        self.assertIn("2026年8月", msg)
        self.assertIn("https://p.secure.freee.co.jp/", msg)
        self.assertIn("未確定", msg)

    def test_remind_shows_diff_and_transfer_note(self):
        msg = build_remind_message(
            "サンプルA株式会社", 2026, 8, [summarize(STMT)],
            ["従業員A: 健康保険 3,349→3,500"], "u", "m")
        self.assertIn("健康保険 3,349→3,500", msg)
        self.assertIn("定期振込", msg)

    def test_check_all_fixed_and_journal(self):
        s = dict(summarize(STMT), fixed=True)
        msg = build_check_message("サンプルA株式会社", 2026, 8, [s], True, "<@UXXXXXXXX>")
        self.assertIn("確定済み", msg)
        self.assertNotIn("<@UXXXXXXXX>", msg)  # 完了報告はメンション不要

    def test_check_unfixed_mentions(self):
        msg = build_check_message("サンプルA株式会社", 2026, 8, [summarize(STMT)], False, "<@UXXXXXXXX>")
        self.assertIn("<@UXXXXXXXX>", msg)


class TestHasSalaryJournal(unittest.TestCase):
    def _fake_api(self, journals, deals, account_items):
        def fake(token, cid, method, path, json_body=None):
            if "/account_items" in path:
                return {"account_items": account_items}
            if "/manual_journals" in path:
                return {"manual_journals": journals}
            if "/deals" in path:
                return {"deals": deals}
            raise AssertionError(path)
        return fake

    def test_found_in_manual_journals(self):
        fake = self._fake_api(
            journals=[{"details": [{"account_item_id": 10}]}], deals=[],
            account_items=[{"id": 10, "name": "役員報酬"}])
        with mock.patch.object(payroll_check.freee_api, "api_call", side_effect=fake):
            self.assertTrue(payroll_check.has_salary_journal("t", 1, 2026, 8))

    def test_not_found(self):
        fake = self._fake_api(
            journals=[], deals=[{"details": [{"account_item_id": 99}]}],
            account_items=[{"id": 10, "name": "役員報酬"}])
        with mock.patch.object(payroll_check.freee_api, "api_call", side_effect=fake):
            self.assertFalse(payroll_check.has_salary_journal("t", 1, 2026, 8))


class TestMainDryRun(unittest.TestCase):
    def test_remind_dry_run_prints_message(self):
        companies = {"companies": [{
            "name": "サンプルA株式会社", "company_id": 1,
            "credentials_path": "dummy.json",
            "payroll": {"enabled": True, "pay_month_offset": 0,
                        "slack_webhook_env": "X",
                        "fix_url": "http://fix/{year}/{month}"}}]}
        with mock.patch.object(payroll_check, "load_companies", return_value=companies), \
             mock.patch.object(payroll_check.freee_api, "ensure_access_token", return_value="t"), \
             mock.patch.object(payroll_check, "fetch_statements", return_value=[STMT]), \
             mock.patch("builtins.print") as p:
            rc = payroll_check.main(["remind", "--dry-run", "--date", "2026-08-20"])
        self.assertEqual(rc, 0)
        out = "\n".join(str(c) for c in p.call_args_list)
        self.assertIn("給与確定リマインド", out)
        self.assertIn("http://fix/2026/8", out)  # fix_urlの{year}/{month}が展開される

class ShouldNotifyTest(unittest.TestCase):
    """確定済みなら出さない（2026-10-22）。"""

    def test_未確定がいれば出す(self):
        self.assertTrue(should_notify("remind", [{"fixed": True}, {"fixed": False}]))
        self.assertTrue(should_notify("check", [{"fixed": False}], True))

    def test_明細が未作成なら出す(self):
        self.assertTrue(should_notify("remind", []))

    def test_全員確定済みなら出さない(self):
        self.assertFalse(should_notify("remind", [{"fixed": True}]))
        self.assertFalse(should_notify("check", [{"fixed": True}], True))

    def test_確定済みでも仕訳が無ければ出す(self):
        self.assertTrue(should_notify("check", [{"fixed": True}], False))



if __name__ == "__main__":
    unittest.main()
