import datetime
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from payroll_todo import (  # noqa: E402
    build_message,
    dedupe_key,
    should_post,
    in_window,
    check_company as _check_company,
    load_rules,
)
import payroll_todo  # noqa: E402


def check_company(rules, statements, expenses, pay_date, today=datetime.date(2026, 9, 10)):
    """基準日を固定する。省略すると実行した日が基準になり、支払日を過ぎると「未確定」が
    混ざって結果が変わる。"""
    return _check_company(rules, statements, expenses, pay_date, today)

PAY_DATE = datetime.date(2026, 9, 25)


def stmt(employee_id, **kw):
    """給与明細の最小形。テストで見る項目だけ持たせる。"""
    base = {
        "employee_id": employee_id,
        "employee_display_name": kw.pop("name", "テスト太郎"),
        "fixed": False,
        "pay_calc_type": "monthly",
        "gross_payment_amount": "220000.0",
        "total_expense_amount": "0.0",
        "payments": [{"name": "基本給", "amount": "220000.0"}],
        "pay_date": "2026-09-25",
        "closing_date": "2026-08-31",
    }
    base.update(kw)
    return base


RULES = [
    {"type": "zero_pay", "employee_id": 1, "label": "育休さん", "until": "2027-04-17"},
    {"type": "require_payment", "employee_id": 2, "label": "成果報酬さん",
     "payment_name": "ナーチャリング成果報酬"},
    {"type": "require_expense", "employee_id": 3, "label": "経費さん", "applicant_id": 999},
    {"type": "hourly_zero", "employee_id": 4, "label": "時給さん"},
    {"type": "must_absent", "employee_id": 5, "label": "対象外さん"},
]


class InWindowTest(unittest.TestCase):
    """締め日翌日〜支払日の間だけ動く。それ以外の日は黙る。"""

    def test_締め日翌日から支払日までは動く(self):
        self.assertTrue(in_window(datetime.date(2026, 9, 1), 31, 25))
        self.assertTrue(in_window(datetime.date(2026, 9, 25), 31, 25))

    def test_支払日の翌日は動かない(self):
        self.assertFalse(in_window(datetime.date(2026, 9, 26), 31, 25))

    def test_月末締めの月末当日は動かない(self):
        self.assertFalse(in_window(datetime.date(2026, 8, 31), 31, 25))


class ZeroPayTest(unittest.TestCase):
    def test_育休中に支給があれば検知する(self):
        found = check_company(RULES, [stmt(1, gross_payment_amount="330000.0")], [], PAY_DATE)
        self.assertEqual(len(found), 1)
        self.assertIn("育休さん", found[0])
        self.assertIn("330,000", found[0])

    def test_育休中で0なら検知しない(self):
        found = check_company(RULES, [stmt(1, gross_payment_amount="0.0")], [], PAY_DATE)
        self.assertEqual(found, [])

    def test_育休期間が終わっていれば検知しない(self):
        rules = [{"type": "zero_pay", "employee_id": 1, "label": "復帰さん",
                  "until": "2026-08-31"}]
        found = check_company(rules, [stmt(1, gross_payment_amount="330000.0")], [], PAY_DATE)
        self.assertEqual(found, [])


class RequirePaymentTest(unittest.TestCase):
    def test_支給項目が無ければ検知する(self):
        found = check_company(RULES, [stmt(2)], [], PAY_DATE)
        self.assertEqual(len(found), 1)
        self.assertIn("ナーチャリング成果報酬", found[0])

    def test_支給項目があれば検知しない(self):
        s = stmt(2, payments=[{"name": "基本給", "amount": "270000.0"},
                              {"name": "ナーチャリング成果報酬", "amount": "45000.0"}])
        self.assertEqual(check_company(RULES, [s], [], PAY_DATE), [])

    def test_金額0の支給項目は未入力とみなす(self):
        # freeeは項目だけ用意して0円で出すことがある
        s = stmt(2, payments=[{"name": "ナーチャリング成果報酬", "amount": "0.0"}])
        self.assertEqual(len(check_company(RULES, [s], [], PAY_DATE)), 1)

    def test_開始月より前は検知しない(self):
        rules = [{"type": "require_payment", "employee_id": 2, "label": "後から開始さん",
                  "payment_name": "成果報酬", "since": "2026-10-01"}]
        self.assertEqual(check_company(rules, [stmt(2)], [], PAY_DATE), [])


class RequireExpenseTest(unittest.TestCase):
    def test_承認済みの経費があるのに給与に乗っていなければ検知する(self):
        expenses = [{"id": 19, "applicant_id": 999, "status": "approved",
                     "issue_date": "2026-09-01", "total_amount": 7488,
                     "title": "8月度_交通費"}]
        found = check_company(RULES, [stmt(3)], expenses, PAY_DATE)
        self.assertEqual(len(found), 1)
        self.assertIn("7,488", found[0])
        self.assertIn("8月度_交通費", found[0])

    def test_給与に乗っていれば検知しない(self):
        expenses = [{"id": 19, "applicant_id": 999, "status": "approved",
                     "issue_date": "2026-09-01", "total_amount": 7488, "title": "8月度_交通費"}]
        s = stmt(3, total_expense_amount="7488.0")
        self.assertEqual(check_company(RULES, [s], expenses, PAY_DATE), [])

    def test_未承認の経費は検知しない(self):
        expenses = [{"id": 20, "applicant_id": 999, "status": "in_progress",
                     "issue_date": "2026-09-01", "total_amount": 5000, "title": "申請中"}]
        self.assertEqual(check_company(RULES, [stmt(3)], expenses, PAY_DATE), [])

    def test_別の人の経費は対象にしない(self):
        expenses = [{"id": 21, "applicant_id": 111, "status": "approved",
                     "issue_date": "2026-09-01", "total_amount": 3000, "title": "他人の分"}]
        self.assertEqual(check_company(RULES, [stmt(3)], expenses, PAY_DATE), [])

    def test_対象月より前に申請された経費は検知しない(self):
        # 前月以前に申請済みで、既に前月の給与で精算されているもの
        expenses = [{"id": 18, "applicant_id": 999, "status": "approved",
                     "issue_date": "2026-08-05", "total_amount": 10368, "title": "7月度_交通費"}]
        self.assertEqual(check_company(RULES, [stmt(3)], expenses, PAY_DATE), [])


class HourlyZeroTest(unittest.TestCase):
    def test_時給者の支給が0なら検知する(self):
        s = stmt(4, pay_calc_type="hourly", gross_payment_amount="0.0", payments=[])
        found = check_company(RULES, [s], [], PAY_DATE)
        self.assertEqual(len(found), 1)
        self.assertIn("時給さん", found[0])

    def test_通勤手当だけでも基本給が無ければ検知する(self):
        # 山口さんの実例：勤怠未入力だと通勤手当¥5,000だけが乗る
        s = stmt(4, pay_calc_type="hourly", gross_payment_amount="5000.0",
                 payments=[{"name": "非課税通勤手当", "amount": "5000.0"}])
        self.assertEqual(len(check_company(RULES, [s], [], PAY_DATE)), 1)

    def test_基本給が入っていれば検知しない(self):
        s = stmt(4, pay_calc_type="hourly", gross_payment_amount="178204.0",
                 payments=[{"name": "基本給", "amount": "173204.0"},
                           {"name": "非課税通勤手当", "amount": "5000.0"}])
        self.assertEqual(check_company(RULES, [s], [], PAY_DATE), [])


class MustAbsentTest(unittest.TestCase):
    def test_出てくるべきでない人がいれば検知する(self):
        found = check_company(RULES, [stmt(5, name="対象外さん")], [], PAY_DATE)
        self.assertEqual(len(found), 1)
        self.assertIn("対象外さん", found[0])

    def test_いなければ検知しない(self):
        self.assertEqual(check_company(RULES, [], [], PAY_DATE), [])


class NotFixedTest(unittest.TestCase):
    """未確定は支払日が近いときだけ言う。毎日言うと通知が埋まる。"""

    def test_支払日の3日前から未確定を検知する(self):
        s = stmt(2, payments=[{"name": "ナーチャリング成果報酬", "amount": "45000.0"}])
        found = check_company(RULES, [s], [], PAY_DATE, datetime.date(2026, 9, 22))
        self.assertEqual(len(found), 1)
        self.assertIn("未確定", found[0])

    def test_支払日まで日があるうちは未確定を言わない(self):
        s = stmt(2, payments=[{"name": "ナーチャリング成果報酬", "amount": "45000.0"}])
        found = check_company(RULES, [s], [], PAY_DATE, datetime.date(2026, 9, 10))
        self.assertEqual(found, [])

    def test_確定済みなら言わない(self):
        s = stmt(2, fixed=True,
                 payments=[{"name": "ナーチャリング成果報酬", "amount": "45000.0"}])
        found = check_company(RULES, [s], [], PAY_DATE, datetime.date(2026, 9, 24))
        self.assertEqual(found, [])


class BuildMessageTest(unittest.TestCase):
    def test_未対応があれば会社名と支払日を添える(self):
        msg = build_message("サンプルA株式会社", 2026, 9, PAY_DATE,
                            ["・育休さん：育休中なのに¥330,000"], datetime.date(2026, 9, 10))
        self.assertIn("サンプルA株式会社", msg)
        self.assertIn("9/25", msg)
        self.assertIn("育休さん", msg)

    @mock.patch.object(payroll_todo, "MENTION", "<@UXXXXXXXX>")
    def test_支払日が近いとメンションを付ける(self):
        msg = build_message("サンプルA株式会社", 2026, 9, PAY_DATE,
                            ["・育休さん：育休中なのに¥330,000"], datetime.date(2026, 9, 23))
        self.assertIn("<@UXXXXXXXX>", msg)

    @mock.patch.object(payroll_todo, "MENTION", "<@UXXXXXXXX>")
    def test_まだ日があるうちはメンションを付けない(self):
        msg = build_message("サンプルA株式会社", 2026, 9, PAY_DATE,
                            ["・育休さん：育休中なのに¥330,000"], datetime.date(2026, 9, 5))
        self.assertNotIn("<@UXXXXXXXX>", msg)

    def test_未対応が無ければNoneを返す(self):
        # 0件の日は投稿しない
        self.assertIsNone(build_message("サンプルA株式会社", 2026, 9, PAY_DATE, [],
                                        datetime.date(2026, 9, 10)))


class DedupeTest(unittest.TestCase):
    """同じ内容を毎日投稿しない。"""

    def msg(self, today):
        return build_message("サンプルA株式会社", 2026, 9, PAY_DATE,
                             ["・育休さん：育休中なのに¥330,000"], today)

    def test_残り日数だけが違えば同じ鍵(self):
        self.assertEqual(dedupe_key(self.msg(datetime.date(2026, 9, 10))),
                         dedupe_key(self.msg(datetime.date(2026, 9, 12))))

    @mock.patch.object(payroll_todo, "MENTION", "<@UXXXXXXXX>")
    def test_メンションの有無は鍵に影響しない(self):
        self.assertEqual(dedupe_key(self.msg(datetime.date(2026, 9, 10))),
                         dedupe_key(self.msg(datetime.date(2026, 9, 23))))

    def test_項目が変われば別の鍵(self):
        other = build_message("サンプルA株式会社", 2026, 9, PAY_DATE,
                              ["・育休さん：育休中なのに¥330,000", "・成果報酬さん：未入力"],
                              datetime.date(2026, 9, 10))
        self.assertNotEqual(dedupe_key(self.msg(datetime.date(2026, 9, 10))), dedupe_key(other))

    def test_前回と同じなら投稿しない(self):
        key = dedupe_key(self.msg(datetime.date(2026, 9, 10)))
        state = {"last_posted": {"100001": {"hash": key}}}
        self.assertFalse(should_post(state, 100001, key, 10))
        self.assertTrue(should_post(state, 100001, "別", 10))
        self.assertTrue(should_post({}, 100001, key, 10))

    def test_支払日の3日前以内は同じでも投稿する(self):
        key = "k"
        state = {"last_posted": {"100001": {"hash": key}}}
        self.assertTrue(should_post(state, 100001, key, 3))
        self.assertTrue(should_post(state, 100001, key, 0))


class LoadRulesTest(unittest.TestCase):
    def test_設定ファイルか雛形を読める(self):
        companies = load_rules()
        self.assertGreaterEqual(len(companies), 1)
        c = companies[0]
        self.assertIn("pay_day", c)
        types = {r["type"] for r in c["rules"]}
        known = {"zero_pay", "require_payment", "require_expense", "hourly_zero",
                 "require_midnight", "must_absent"}
        self.assertTrue(types <= known)


if __name__ == "__main__":
    unittest.main()
