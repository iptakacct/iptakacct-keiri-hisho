import unittest

import mf_settle

GROUP = ["株式会社サンプルA", "サンプルC株式会社", "株式会社サンプルB"]


def line(acct, sub, value, side):
    part = {"account_id": acct, "sub_account_id": sub, "value": value, "tax_id": "t_taisho"}
    other = {"account_id": "a_futsu", "sub_account_id": "s_bank", "value": value, "tax_id": "t_taisho"}
    return {"debitor": part, "creditor": other} if side == "debit" else {"debitor": other, "creditor": part}


def jr(number, date, *branches):
    return {"number": number, "transaction_date": date, "branches": list(branches)}


class TestNames(unittest.TestCase):
    def test_表記ゆれをそろえる(self):
        self.assertTrue(mf_settle.is_group("㈱サンプルA", GROUP))
        self.assertTrue(mf_settle.is_group("(株)サンプルB", GROUP))
        self.assertTrue(mf_settle.is_group("サンプルC㈱", GROUP))
        self.assertTrue(mf_settle.is_group("サンプルＣ 株式会社", GROUP))

    def test_一覧に無い名前は対象外(self):
        self.assertFalse(mf_settle.is_group("サンプルC有限責任事業組合", GROUP))
        self.assertFalse(mf_settle.is_group("月末振込", GROUP))
        self.assertFalse(mf_settle.is_group("", GROUP))
        self.assertFalse(mf_settle.is_group(None, GROUP))


class TestReducesBalance(unittest.TestCase):
    def test_資産は貸方で減る(self):
        self.assertTrue(mf_settle.reduces_balance("売掛金", "credit"))
        self.assertFalse(mf_settle.reduces_balance("未収入金", "debit"))

    def test_負債は借方で減る(self):
        self.assertTrue(mf_settle.reduces_balance("未払金", "debit"))
        self.assertFalse(mf_settle.reduces_balance("未払金", "credit"))

    def test_消込科目でなければ減らさない(self):
        self.assertFalse(mf_settle.reduces_balance("支払手数料", "debit"))


class TestOpenItems(unittest.TestCase):
    def test_同額の計上と消込を対にして残りを返す(self):
        journals = [
            jr(1, "2026-06-30", line("a_uri", "s_c", 1000, "debit")),   # 計上
            jr(2, "2026-07-31", line("a_uri", "s_c", 1000, "credit")),  # 消込（1と対）
            jr(3, "2026-08-31", line("a_uri", "s_c", 2500, "debit")),   # 未消込
        ]
        items = mf_settle.open_items(journals, "a_uri", "s_c", "売掛金", since="2026-03-01")
        self.assertEqual([(i["number"], i["value"]) for i in items], [(3, 2500)])

    def test_負債は貸方が計上(self):
        journals = [jr(5, "2026-08-31", line("a_mibarai", "s_c", 700, "credit"))]
        items = mf_settle.open_items(journals, "a_mibarai", "s_c", "未払金", since="2026-03-01")
        self.assertEqual([i["number"] for i in items], [5])

    def test_補助科目違いは混ぜない(self):
        journals = [jr(1, "2026-08-31", line("a_uri", "s_other", 1000, "debit"))]
        self.assertEqual(mf_settle.open_items(journals, "a_uri", "s_c", "売掛金", since="2026-03-01"), [])

    def test_期間より前の計上は見ない(self):
        journals = [jr(1, "2026-01-31", line("a_uri", "s_c", 1000, "debit"))]
        self.assertEqual(mf_settle.open_items(journals, "a_uri", "s_c", "売掛金", since="2026-03-01"), [])

    def test_残高を数える(self):
        journals = [
            jr(1, "2026-06-30", line("a_uri", "s_c", 1000, "debit")),
            jr(2, "2026-07-31", line("a_uri", "s_c", 400, "credit")),
        ]
        self.assertEqual(mf_settle.balance(journals, "a_uri", "s_c", "売掛金"), 600)


class TestMatch(unittest.TestCase):
    def test_同額がちょうど1件(self):
        items = [{"number": 3, "date": "2026-08-31", "value": 2500}, {"number": 4, "date": "2026-08-31", "value": 900}]
        self.assertEqual([i["number"] for i in mf_settle.matching(items, 2500)], [3])

    def test_同額が2件なら2件返す(self):
        items = [{"number": 3, "date": "2026-08-31", "value": 2500}, {"number": 4, "date": "2026-09-01", "value": 2500}]
        self.assertEqual(len(mf_settle.matching(items, 2500)), 2)


if __name__ == "__main__":
    unittest.main()


class TestOpening(unittest.TestCase):
    def _opening(self, rows):
        return {"number": 1, "transaction_date": "2026-05-01", "entered_by": "JOURNAL_TYPE_OPENING",
                "branches": [{side: {"account_id": "a_ar", "sub_account_id": "s", "value": v}}
                             for side, v in rows]}

    def _jr(self, n, d, side, v):
        return {"number": n, "transaction_date": d, "entered_by": "JOURNAL_TYPE_NORMAL",
                "branches": [{side: {"account_id": "a_ar", "sub_account_id": "s", "value": v}}]}

    def test_開始仕訳の貸方行を差し引く(self):
        op = self._opening([("debitor", 9478447), ("creditor", 5938620), ("creditor", 1448947)])
        items = mf_settle.open_items([op], "a_ar", "s", "売掛金", since="2026-05-01")
        self.assertEqual([i["value"] for i in items], [2090880])

    def test_差し引いた期首残高は同額の入金で消える(self):
        op = self._opening([("debitor", 9478447), ("creditor", 5938620), ("creditor", 1448947)])
        rec = self._jr(156, "2026-05-29", "creditor", 2090880)
        self.assertEqual(mf_settle.open_items([op, rec], "a_ar", "s", "売掛金", since="2026-05-01"), [])

    def test_貸方が無ければ期首の行は1件ずつ残す(self):
        op = self._opening([("debitor", 589600), ("debitor", 589600)])
        rec = self._jr(239, "2026-05-01", "creditor", 589600)
        items = mf_settle.open_items([op, rec], "a_ar", "s", "売掛金", since="2026-05-01")
        self.assertEqual([i["value"] for i in items], [589600])

    def test_同額の借方と貸方は打ち消す(self):
        op = self._opening([("debitor", 16908100), ("creditor", 16908100)])
        self.assertEqual(mf_settle.open_items([op], "a_ar", "s", "売掛金", since="2026-05-01"), [])
