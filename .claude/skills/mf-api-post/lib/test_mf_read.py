import re
import unittest

import mf_read as mr


class TestPath(unittest.TestCase):
    def test_許可リスト(self):
        self.assertEqual(mr.check_path("/journals?start_date=2026-08-01"), "/journals?start_date=2026-08-01")
        self.assertEqual(mr.check_path("/journals/abc%3D"), "/journals/abc%3D")
        for bad in ("/journals/abc/x", "/users", "https://x/journals", "/transactions/journalize"):
            with self.assertRaises(ValueError):
                mr.check_path(bad)

    def test_一覧の配列名(self):
        self.assertEqual(mr.list_key("/journals?x=1"), "journals")
        self.assertIsNone(mr.list_key("/journals/abc"))
        self.assertIsNone(mr.list_key("/reports/trial_balance_pl?x=1"))
        # 件数を数えるための配列名は、ページ送りしない一覧にもある
        self.assertEqual(mr.list_key("/trade_partners"), "trade_partners")
        self.assertEqual(mr.list_key("/departments"), "departments")
        self.assertEqual(mr.list_key("/sub_accounts"), "sub_accounts")
        self.assertEqual(mr.list_key("/accounts"), "accounts")
        self.assertEqual(mr.list_key("/taxes"), "taxes")


class TestRead(unittest.TestCase):
    def test_ページを最後まで取りpageを二重にしない(self):
        calls = []

        def get(path):
            calls.append(path)
            m = re.search(r"[?&]page=(\d+)", path)
            page = int(m.group(1))
            return {"journals": [{"id": page}], "metadata": {"total_pages": 2}}

        out = mr.read(get, "/journals?start_date=2026-08-01&page=5", True, sleep=lambda s: None)
        self.assertEqual([j["id"] for j in out["journals"]], [1, 2])
        # page= の出現が1回だけ（per_page= を page= と誤認しないよう、同じ正規表現の出現回数で数える）
        self.assertTrue(all(len(re.findall(r"[?&]page=(\d+)", c)) == 1 for c in calls))

    def test_クエリ無しのパスにpageを足すときは疑問符から始める(self):
        calls = []

        def get(path):
            calls.append(path)
            m = re.search(r"[?&]page=(\d+)", path)
            page = int(m.group(1))
            return {"journals": [{"id": page}], "metadata": {"total_pages": 1}}

        mr.read(get, "/journals", True, sleep=lambda s: None)
        self.assertEqual(len(calls), 1)
        self.assertIn("?page=", calls[0])
        self.assertNotIn("&page=", calls[0].split("?page=")[0])

    def test_1件取得は1回だけ(self):
        calls = []
        out = mr.read(lambda p: calls.append(p) or {"journal": {"id": "a"}}, "/journals/a", True,
                      sleep=lambda s: None)
        self.assertEqual((len(calls), out["journal"]["id"]), (1, "a"))

    def test_all_pagesがFalseなら1回だけ取る(self):
        calls = []

        def get(path):
            calls.append(path)
            return {"journals": [{"id": 1}], "metadata": {"total_pages": 2}}

        out = mr.read(get, "/journals?start_date=2026-08-01", False, sleep=lambda s: None)
        self.assertEqual(len(calls), 1)
        self.assertEqual([j["id"] for j in out["journals"]], [1])

    def test_一覧の配列名が無い場合はそのまま返す(self):
        # レポート系（/reports/...）はページ送りの配列名を持たないので、all_pages指定でも
        # get の応答をそのまま返す
        calls = []
        out = mr.read(lambda p: calls.append(p) or {"data": {"x": 1}},
                      "/reports/trial_balance_pl?start_date=2026-08-01&end_date=2026-08-31", True,
                      sleep=lambda s: None)
        self.assertEqual((len(calls), out), (1, {"data": {"x": 1}}))

    def test_取引先と部門と補助科目はall_pagesでもページ送りしない(self):
        # 実APIは /trade_partners・/departments・/sub_accounts に page/per_page を付けると
        # HTTP 400（unsupported_query_parameter）を返す（R5）。クエリそのままの1回のGETにする
        for path in ("/trade_partners", "/departments", "/sub_accounts?account_id=a1"):
            with self.subTest(path=path):
                calls = []
                name = path.split("?")[0].lstrip("/")
                out = mr.read(lambda p: calls.append(p) or {name: [{"id": 1}]}, path, True,
                              sleep=lambda s: None)
                self.assertEqual(calls, [path])
                self.assertEqual(out, {name: [{"id": 1}]})

    def test_許可リスト外は読まない(self):
        with self.assertRaises(ValueError):
            mr.read(lambda p: {}, "/users", True, sleep=lambda s: None)


class TestDefaultOut(unittest.TestCase):
    def test_インスタンス配下のwork配下に事業者コードと時刻で保存する(self):
        import datetime
        import os
        company = {"instance": "サンプルA", "office_id": "1000-0001"}
        now = datetime.datetime(2026, 9, 23, 10, 30, 5)
        out = mr.default_out(os.sep + "repo", company, now)
        self.assertEqual(out, os.path.join(os.sep + "repo", "サンプルA", "work", "mf-read",
                                           "1000-0001_20260923-103005.json"))


if __name__ == "__main__":
    unittest.main()
