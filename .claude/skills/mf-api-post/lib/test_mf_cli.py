import argparse
import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import mf_api
import mf_cli
from mf_cli import load_company_config, ConfigError, cmd_healthcheck


def _write_companies_json(d, credentials_relpath="creds.json"):
    path = os.path.join(d, "companies.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "companies": [
                {
                    "name": "株式会社サンプルA",
                    "office_id": "1000-0001",
                    "instance": "sampleA",
                    "credentials_path": credentials_relpath,
                    "slack_channel": "CH-SAMPLE-A",
                }
            ]
        }, f, ensure_ascii=False)
    return path


class TestLoadCompanyConfig(unittest.TestCase):
    def test_finds_company_by_name(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_companies_json(d)
            company = load_company_config(path, "株式会社サンプルA")
        self.assertEqual(company["office_id"], "1000-0001")

    def test_finds_company_by_office_id(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_companies_json(d)
            company = load_company_config(path, "1000-0001")
        self.assertEqual(company["name"], "株式会社サンプルA")

    def test_raises_when_not_found(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_companies_json(d)
            with self.assertRaises(ConfigError):
                load_company_config(path, "存在しない会社")




class TestHealthcheck(unittest.TestCase):
    @mock.patch("mf_api.api_call")
    @mock.patch("mf_api.ensure_access_token")
    def test_success_logs_ok_and_returns_zero(self, mock_ensure, mock_api_call):
        mock_ensure.return_value = "token-1"
        mock_api_call.return_value = {"code": "1000-0001", "name": "株式会社サンプルA"}
        with tempfile.TemporaryDirectory() as repo_root:
            companies_json = _write_companies_json(repo_root)
            args = argparse.Namespace(companies_json=companies_json, repo_root=repo_root)
            exit_code = cmd_healthcheck(args)
            log_path = os.path.join(repo_root, "sampleA", "work", "mf-api-post.log")
            with open(log_path, "r", encoding="utf-8") as f:
                log_content = f.read()
        self.assertEqual(exit_code, 0)
        self.assertIn("healthcheck OK", log_content)

    @mock.patch("mf_api.ensure_access_token")
    def test_failure_logs_ng_and_returns_one(self, mock_ensure):
        mock_ensure.side_effect = mf_api.MfApiError("invalid_grant")
        with tempfile.TemporaryDirectory() as repo_root:
            companies_json = _write_companies_json(repo_root)
            args = argparse.Namespace(companies_json=companies_json, repo_root=repo_root)
            exit_code = cmd_healthcheck(args)
            log_path = os.path.join(repo_root, "sampleA", "work", "mf-api-post.log")
            with open(log_path, "r", encoding="utf-8") as f:
                log_content = f.read()
        self.assertEqual(exit_code, 1)
        self.assertIn("healthcheck NG", log_content)



class TestFetchAll(unittest.TestCase):
    def test_ページが複数でも全部取る(self):
        pages = {1: {"items": ["a", "b"]}, 2: {"items": ["c"]}}
        calls = []

        def fake_get(path):
            calls.append(path)
            page = int(path.split("page=")[1].split("&")[0])
            return pages[page]

        slept = []
        result = mf_cli.fetch_all(fake_get, "/foo?x=1", "items", per_page=2, sleep=slept.append)
        self.assertEqual(result, ["a", "b", "c"])
        self.assertEqual(len(calls), 2)
        self.assertIn("per_page=2", calls[0])
        # ページは2つ→境目は1つだけ。最初のページの前は待たない
        self.assertEqual(slept, [1.5])

    def test_空配列で止まる(self):
        slept = []

        def fake_get(path):
            return {"items": []}

        result = mf_cli.fetch_all(fake_get, "/foo", "items", sleep=slept.append)
        self.assertEqual(result, [])
        # 1ページで終わるので、間隔を空ける相手がいない
        self.assertEqual(slept, [])

    def test_1ページ目がちょうどper_pageでもmetadataのtotal_pagesが1なら2回目を呼ばない(self):
        # 実データで確認した境界バグ：件数がper_pageの倍数のとき、
        # 件数だけで判定すると存在しない次ページを要求してMFがHTTP 400を返す。
        # ページ1しか用意しないので、直さないままだと2回目の呼び出しでKeyErrorになり
        # 無限ループにはならず即座に失敗する（フェイルファスト）。
        pages = {1: {"items": ["a", "b"], "metadata": {"total_count": 2, "total_pages": 1}}}
        calls = []

        def fake_get(path):
            calls.append(path)
            page = int(path.split("page=")[1].split("&")[0])
            return pages[page]

        result = mf_cli.fetch_all(fake_get, "/foo", "items", per_page=2, sleep=lambda s: None)
        self.assertEqual(result, ["a", "b"])
        self.assertEqual(len(calls), 1)

    def test_metadataのtotal_pagesが3なら3回取って全件返す(self):
        pages = {
            1: {"items": ["a", "b"], "metadata": {"total_count": 6, "total_pages": 3}},
            2: {"items": ["c", "d"], "metadata": {"total_count": 6, "total_pages": 3}},
            3: {"items": ["e", "f"], "metadata": {"total_count": 6, "total_pages": 3}},
        }
        calls = []

        def fake_get(path):
            calls.append(path)
            page = int(path.split("page=")[1].split("&")[0])
            if page not in pages:
                raise AssertionError("total_pagesを超えたページを要求した: %s" % path)
            return pages[page]

        result = mf_cli.fetch_all(fake_get, "/foo", "items", per_page=2, sleep=lambda s: None)
        self.assertEqual(result, ["a", "b", "c", "d", "e", "f"])
        self.assertEqual(len(calls), 3)

    def test_metadataが無いレスポンスは従来どおり短いページで止まる(self):
        # フォールバック確認：metadataが無いAPI応答では、件数だけで判定する旧来の挙動を保つ
        pages = {1: {"items": ["a", "b"]}, 2: {"items": ["c"]}}
        calls = []

        def fake_get(path):
            calls.append(path)
            page = int(path.split("page=")[1].split("&")[0])
            return pages[page]

        result = mf_cli.fetch_all(fake_get, "/foo", "items", per_page=2, sleep=lambda s: None)
        self.assertEqual(result, ["a", "b", "c"])
        self.assertEqual(len(calls), 2)




_SJ_ACCOUNTS = {"accounts": [
    {"id": "a_kashi", "name": "短期貸付金", "sub_accounts": [{"id": "s_matsu", "name": "山田太郎"}]},
    {"id": "a_futsu", "name": "普通預金", "sub_accounts": [{"id": "s_kira", "name": "サンプル銀行"}]},
    {"id": "a_tesu", "name": "支払手数料", "sub_accounts": []},
]}
_SJ_TAXES = {"taxes": [{"id": "t_taisho", "name": "対象外"}, {"id": "t_ka10", "name": "課税仕入 10%"}]}


def _sj_journal(number, dr_value=2500000, dr_tax="t_taisho", dr_tax_value=0):
    return {"id": f"j{number}", "number": number, "transaction_date": "2026-09-10",
            "transaction_id": "tx229", "journal_type": "journal_entry",
            "branches": [{"remark": "出金 ヤマダ タロウ",
                          "debitor": {"account_id": "a_kashi", "sub_account_id": "s_matsu",
                                      "tax_id": dr_tax, "value": dr_value,
                                      "tax_value": dr_tax_value},
                          "creditor": {"account_id": "a_futsu", "sub_account_id": "s_kira",
                                       "tax_id": "t_taisho", "value": dr_value + dr_tax_value,
                                       "tax_value": 0}}]}


def _sj_route(path, journals, office_code="1000-0001"):
    if path == "/offices":
        return {"code": office_code}
    if path == "/accounts":
        return _SJ_ACCOUNTS
    if path == "/taxes":
        return _SJ_TAXES
    if path.startswith("/journals"):
        return {"journals": journals, "metadata": {"total_count": len(journals), "total_pages": 1}}
    raise AssertionError(path)


class TestShowJournal(unittest.TestCase):
    def _run(self, journals, number="229", office_code="1000-0001"):
        with tempfile.TemporaryDirectory() as d:
            companies = _write_companies_json(d)
            out = io.StringIO()
            with mock.patch("mf_api.ensure_access_token", return_value="tok"), \
                 mock.patch("mf_api.api_call",
                            side_effect=lambda token, method, path, body=None:
                            _sj_route(path, journals, office_code)) as api, \
                 mock.patch("time.sleep"), \
                 redirect_stdout(out):
                code = mf_cli.main(["--companies-json", companies, "--repo-root", d,
                                    "show-journal", "株式会社サンプルA", number,
                                    "--date", "2026-09-10"])
        for call in api.call_args_list:
            self.assertEqual(call.args[1], "GET")
        return code, out.getvalue()

    def test_科目名と補助科目名と税区分名で出す(self):
        code, out = self._run([_sj_journal(229)])
        self.assertEqual(code, 0)
        for s in ("#229", "2026-09-10", "短期貸付金", "山田太郎", "普通預金", "サンプル銀行",
                  "対象外", "2,500,000", "出金 ヤマダ タロウ", "tx229"):
            self.assertIn(s, out)

    def test_税込額を本体と税額の合計で出す(self):
        code, out = self._run([_sj_journal(229, dr_value=1000, dr_tax="t_ka10", dr_tax_value=100)])
        self.assertIn("1,100", out)

    def test_番号が無ければ1を返す(self):
        code, out = self._run([_sj_journal(230)])
        self.assertEqual(code, 1)
        self.assertIn("見つかりません", out)

    def test_接続先の事業者が違えば止める(self):
        with self.assertRaises(mf_api.MfApiError):
            self._run([_sj_journal(229)], office_code="9999-9999")




class TestReadCli(unittest.TestCase):
    """read：MF会計APIをGETだけで読む（MCPのget系の代わり）。"""

    def _run(self, path, extra_args=(), routes=None, office_code="1000-0001", out_path=None):
        # routesの値は固定のdict、または path を受けて動的に返す関数のどちらでもよい
        # （--all-pages でページごとに違う応答を返すテストのため）。
        # out_path を指定すると、既定の保存先（<repo>/sampleA/work/mf-read/...）の代わりに
        # そのパスから読み直す（--out で保存先を上書きするテスト用）。
        routes = dict(routes or {})
        routes.setdefault("/offices", {"code": office_code})

        def fake_get(token, method, p, body=None):
            assert method == "GET"
            bare = p.split("?")[0]
            match = routes.get(bare)
            if match is None:
                match = next((v for k, v in routes.items() if p.startswith(k)), None)
            if match is None:
                raise AssertionError(f"想定外のパス: {p}")
            return match(p) if callable(match) else match

        with tempfile.TemporaryDirectory() as d:
            companies = _write_companies_json(d)
            out = io.StringIO()
            with mock.patch("mf_api.ensure_access_token", return_value="tok"), \
                 mock.patch("mf_api.api_call", side_effect=fake_get) as api, \
                 mock.patch("time.sleep"), \
                 redirect_stdout(out):
                code = mf_cli.main(["--companies-json", companies, "--repo-root", d,
                                    "read", "株式会社サンプルA", path, *extra_args])
            saved = None
            candidate = out_path
            if candidate is None:
                read_dir = os.path.join(d, "sampleA", "work", "mf-read")
                if os.path.isdir(read_dir):
                    files = os.listdir(read_dir)
                    if files:
                        candidate = os.path.join(read_dir, files[0])
            if candidate and os.path.exists(candidate):
                with open(candidate, encoding="utf-8") as f:
                    saved = json.load(f)
        for call in api.call_args_list:
            self.assertEqual(call.args[1], "GET")
        return code, out.getvalue(), saved, api.call_count

    def test_許可リスト外は接続もせず止める(self):
        code, out, saved, api_calls = self._run("/users")
        self.assertEqual(code, 1)
        self.assertIn("読み取れるのは", out)
        self.assertIsNone(saved)
        self.assertEqual(api_calls, 0)

    def test_一件取得は保存件数1で出す(self):
        code, out, saved, _ = self._run(
            "/journals/j1", routes={"/journals/j1": {"journal": {"id": "j1"}}})
        self.assertEqual(code, 0)
        self.assertIn("1件", out)
        self.assertEqual(saved, {"journal": {"id": "j1"}})

    def test_all_pagesで一覧を最後まで取って保存する(self):
        def journals_page(p):
            # per_page= を page= と誤って拾わないよう、[?&]page=(\d+) で厳密に取る
            page = int(re.search(r"[?&]page=(\d+)", p).group(1))
            return {"journals": [{"id": page}], "metadata": {"total_pages": 2}}

        code, out, saved, _ = self._run(
            "/journals?start_date=2026-08-01", extra_args=["--all-pages"],
            routes={"/journals": journals_page})
        self.assertEqual(code, 0)
        self.assertIn("2件", out)
        self.assertEqual([j["id"] for j in saved["journals"]], [1, 2])

    def test_接続先の事業者が違えば止める(self):
        with self.assertRaises(mf_api.MfApiError):
            self._run("/accounts", office_code="9999-9999", routes={"/accounts": {"accounts": []}})

    def test_all_pages無しで残りのページがあれば警告する(self):
        code, out, saved, api_calls = self._run(
            "/journals?start_date=2026-08-01",
            routes={"/journals": {"journals": [{"id": 1}], "metadata": {"total_pages": 3}}})
        self.assertEqual(code, 0)
        self.assertIn("1ページ目だけ", out)
        self.assertIn("--all-pages", out)

    def test_1ページで終わる一覧は警告しない(self):
        code, out, _, _ = self._run(
            "/journals?start_date=2026-08-01",
            routes={"/journals": {"journals": [{"id": 1}], "metadata": {"total_pages": 1}}})
        self.assertEqual(code, 0)
        self.assertNotIn("1ページ目だけ", out)

    def test_取引先はall_pagesでも1回のGETでpageを付けない(self):
        seen = []

        def partners(p):
            seen.append(p)
            return {"trade_partners": [{"code": "A"}, {"code": "B"}]}

        code, out, saved, _ = self._run("/trade_partners", extra_args=["--all-pages"],
                                        routes={"/trade_partners": partners})
        self.assertEqual(code, 0)
        self.assertEqual(seen, ["/trade_partners"])
        self.assertIn("2件", out)
        self.assertEqual(len(saved["trade_partners"]), 2)

    def test_outにディレクトリ付きパスを指定すれば作って保存する(self):
        # レビュー指摘（fix round 1）：--out が存在しないディレクトリを含む場合、
        # そのディレクトリを作ってから保存できることを確かめる。
        with tempfile.TemporaryDirectory() as tmp_out:
            out_path = os.path.join(tmp_out, "sub", "dir", "out.json")
            code, out, saved, _ = self._run(
                "/journals/j1", extra_args=["--out", out_path],
                routes={"/journals/j1": {"journal": {"id": "j1"}}}, out_path=out_path)
        self.assertEqual(code, 0)
        self.assertIn(out_path, out)
        self.assertEqual(saved, {"journal": {"id": "j1"}})

    def test_outが裸のファイル名でも落ちない(self):
        # レビュー指摘（fix round 1）：--out に裸のファイル名（ディレクトリ部分が空）を渡すと
        # os.path.dirname(out) が "" になり、修正前は os.makedirs("") が FileNotFoundError で
        # 落ちていた。カレントディレクトリを一時ディレクトリに切り替えて確かめる。
        routes = {"/journals/j1": {"journal": {"id": "j1"}}, "/offices": {"code": "1000-0001"}}

        def fake_get(token, method, p, body=None):
            bare = p.split("?")[0]
            return routes.get(bare) or next(v for k, v in routes.items() if p.startswith(k))

        with tempfile.TemporaryDirectory() as d:
            companies = _write_companies_json(d)
            cwd_dir = os.path.join(d, "cwd")
            os.makedirs(cwd_dir)
            prev_cwd = os.getcwd()
            os.chdir(cwd_dir)
            try:
                out = io.StringIO()
                with mock.patch("mf_api.ensure_access_token", return_value="tok"), \
                     mock.patch("mf_api.api_call", side_effect=fake_get), \
                     mock.patch("time.sleep"), \
                     redirect_stdout(out):
                    code = mf_cli.main(["--companies-json", companies, "--repo-root", d,
                                        "read", "株式会社サンプルA", "/journals/j1",
                                        "--out", "out.json"])
            finally:
                os.chdir(prev_cwd)
            saved_path = os.path.join(cwd_dir, "out.json")
            self.assertEqual(code, 0)
            self.assertTrue(os.path.exists(saved_path))
            with open(saved_path, encoding="utf-8") as f:
                saved = json.load(f)
        self.assertEqual(saved, {"journal": {"id": "j1"}})



class TestWriteIsNotAvailable(unittest.TestCase):
    def test_書き込み系のサブコマンドは無い(self):
        parser = mf_cli.build_parser()
        for cmd in ("register", "register-one", "post-journal", "put-journal", "create-partner"):
            with self.assertRaises(SystemExit):
                with mock.patch("sys.stderr", io.StringIO()):
                    parser.parse_args(["--companies-json", "c.json", "--repo-root", ".", cmd, "x"])


class TestSettleSince(unittest.TestCase):
    def test_会計期間の期首より前には戻らない(self):
        periods = [{"start_date": "2026-04-01", "end_date": "2027-03-31"}]
        self.assertEqual(mf_cli._settle_since("2026-06-30", periods), "2026-04-01")

    def test_期間内なら基準日の183日前(self):
        periods = [{"start_date": "2025-01-01", "end_date": "2025-12-31"}]
        self.assertEqual(mf_cli._settle_since("2025-12-31", periods), "2025-07-01")


if __name__ == "__main__":
    unittest.main()
