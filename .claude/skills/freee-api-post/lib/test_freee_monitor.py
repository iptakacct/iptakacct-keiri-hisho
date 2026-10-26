"""freee_monitor.py（フェーズ3：未処理明細・未添付証憑の無人監視）のユニットテスト。
APIレスポンスと同じ構造のdict/listを渡す純粋関数のみを対象にする。"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
import freee_monitor  # noqa: E402


def _txn(txn_id, d, amount, description, status=1):
    return {"id": txn_id, "date": d, "amount": amount, "description": description,
            "status": status, "entry_side": "expense", "walletable_type": "bank_account"}


def _receipt(rid, created="2026-08-20", amount=None, issue_date=None, partner=None, status="unconfirmed"):
    meta = {}
    if amount is not None:
        meta["amount"] = amount
    if issue_date is not None:
        meta["issue_date"] = issue_date
    if partner is not None:
        meta["partner_name"] = partner
    return {"id": rid, "created_at": created + "T10:00:00+09:00", "status": status,
            "description": "", "receipt_metadatum": meta}


def _deal(deal_id, issue_date, amount, receipts=()):
    return {"id": deal_id, "issue_date": issue_date, "amount": amount,
            "receipts": [{"id": r} for r in receipts]}


class TestNormalizeDescription(unittest.TestCase):
    def test_strips_digits_and_spaces(self):
        self.assertEqual(freee_monitor.normalize_description("振込 カ）サンプル 2026/07 １２３"), "振込カ）サンプル")

    def test_empty(self):
        self.assertEqual(freee_monitor.normalize_description(None), "")


class TestFindStaleUntreated(unittest.TestCase):
    def test_returns_untreated_older_than_min_days(self):
        txns = [
            _txn(1, "2026-08-25", -1000, "A", status=1),   # 6日前 → 対象
            _txn(2, "2026-08-30", -1000, "B", status=1),   # 1日前 → まだ猶予
            _txn(3, "2026-08-01", -1000, "C", status=2),   # 消込済み → 対象外
            _txn(4, "2026-08-01", -1000, "D", status=3),   # 無視 → 対象外
        ]
        stale = freee_monitor.find_stale_untreated(txns, today=date(2026, 8, 31), min_days=3)
        self.assertEqual([t["id"] for t in stale], [1])

    def test_sorted_oldest_first(self):
        txns = [_txn(1, "2026-08-20", -1, "A"), _txn(2, "2026-08-10", -1, "B")]
        stale = freee_monitor.find_stale_untreated(txns, today=date(2026, 8, 31))
        self.assertEqual([t["id"] for t in stale], [2, 1])


class TestFindNewPatterns(unittest.TestCase):
    def test_returns_only_unknown_patterns_once(self):
        txns = [
            _txn(1, "2026-08-25", -1000, "振込 カ）サンプル 8月分"),
            _txn(2, "2026-08-26", -1000, "振込 カ）サンプル 9月分"),   # 同じパターン
            _txn(3, "2026-08-27", -500, "振込 カ）テスト"),
        ]
        known = {freee_monitor.normalize_description("振込 カ）テスト")}
        new = freee_monitor.find_new_patterns(txns, known)
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["pattern"], "振込カ）サンプル月分")
        self.assertEqual(new[0]["sample"]["id"], 1)
        self.assertEqual(new[0]["count"], 2)

    def test_skips_empty_description(self):
        self.assertEqual(freee_monitor.find_new_patterns([_txn(1, "2026-08-25", -1, "")], set()), [])


class TestUnattachedReceipts(unittest.TestCase):
    def test_excludes_receipts_attached_to_any_deal(self):
        receipts = [_receipt(10), _receipt(11), _receipt(12, status="deleted")]
        deals = [_deal(1, "2026-08-20", 1000, receipts=(11,))]
        un = freee_monitor.find_unattached_receipts(receipts, deals)
        self.assertEqual([r["id"] for r in un], [10])

    def test_candidates_match_amount_and_date_window_on_deals_without_receipts(self):
        receipts = [
            _receipt(10, amount=3300, issue_date="2026-08-20"),
            _receipt(11, amount=9999, issue_date="2026-08-20"),
            _receipt(12),  # メタデータなし → 候補なし（目視対象）
        ]
        deals = [
            _deal(1, "2026-08-21", 3300),                    # 1日ずれ → 候補
            _deal(2, "2026-08-20", 3300, receipts=(99,)),     # 既に証憑あり → 除外
            _deal(3, "2026-08-01", 9999),                     # 19日ずれ → 範囲外
        ]
        cands = freee_monitor.match_receipt_candidates(receipts, deals, window_days=3)
        self.assertEqual([(c["receipt"]["id"], [d["id"] for d in c["deals"]]) for c in cands],
                         [(10, [1])])


class TestWalletablesSync(unittest.TestCase):
    def test_reports_only_when_sync_status_present_and_not_synced(self):
        walletables = [
            {"id": 1, "name": "A銀行", "type": "bank_account", "sync_status": "synced"},
            {"id": 2, "name": "Bカード", "type": "credit_card", "sync_status": "error"},
            {"id": 3, "name": "現金", "type": "wallet"},   # 項目なし → 判定不能、対象外
        ]
        issues = freee_monitor.check_walletables_sync(walletables)
        self.assertEqual([w["id"] for w in issues], [2])
        self.assertEqual(issues[0]["reason"], "error")


class TestStalledWalletables(unittest.TestCase):
    def _w(self, wid, wtype="bank_account", bank_id=1, name="A銀行"):
        return {"id": wid, "name": name, "type": wtype, "bank_id": bank_id}

    def _t(self, wid, d, wtype="bank_account"):
        return {"id": wid * 100, "date": d, "walletable_type": wtype, "walletable_id": wid}

    def test_reports_connected_accounts_whose_latest_txn_is_older_than_max_days(self):
        today = date(2026, 9, 10)
        walletables = [self._w(1), self._w(2, name="Bカード", wtype="credit_card"),
                       self._w(3, wtype="wallet", bank_id=None, name="現金")]
        txns = [self._t(1, "2026-08-01"), self._t(1, "2026-09-09"),   # 1 は最新が昨日 → 正常
                self._t(2, "2026-07-20", wtype="credit_card")]        # 2 は52日前 → 報告
        issues = freee_monitor.find_stalled_walletables(walletables, txns, today=today, max_days=30)
        self.assertEqual([w["id"] for w in issues], [2])
        self.assertEqual(issues[0]["last_txn_date"], "2026-07-20")
        self.assertIn("52日", issues[0]["reason"])

    def test_skips_accounts_with_no_txn_in_window(self):
        # 使っていないだけか連携切れかを区別できないので報告しない（未使用カードの雑音対策）
        today = date(2026, 9, 10)
        walletables = [self._w(1), self._w(3, wtype="wallet", bank_id=None, name="現金")]
        issues = freee_monitor.find_stalled_walletables(walletables, [], today=today)
        self.assertEqual(issues, [])

    def test_report_text_uses_reason(self):
        result = freee_monitor.build_company_report(
            company_name="テスト社", stale=[], new_patterns=[], unattached=[], candidates=[],
            sync_issues=[{"id": 1, "name": "A銀行", "reason": "最新明細 2026-07-20、52日間更新なし"}],
            state={"reported_txn_ids": [], "reported_receipt_ids": [], "known_patterns": []},
        )
        self.assertIn("口座連携の異常", result["text"])
        self.assertIn("52日間更新なし", result["text"])


class TestReportAndState(unittest.TestCase):
    def test_no_report_when_nothing_new(self):
        result = freee_monitor.build_company_report(
            company_name="テスト社", stale=[], new_patterns=[], unattached=[], candidates=[],
            sync_issues=[], state={"reported_txn_ids": [], "reported_receipt_ids": [], "known_patterns": []},
        )
        self.assertIsNone(result["text"])

    def test_reports_new_items_and_remaining_count_and_updates_state(self):
        stale = [_txn(1, "2026-08-20", -1000, "A"), _txn(2, "2026-08-21", -2000, "B")]
        state = {"reported_txn_ids": [1], "reported_receipt_ids": [], "known_patterns": []}
        new_patterns = [{"pattern": "B", "sample": stale[1], "count": 1}]
        unattached = [_receipt(10, amount=3300, issue_date="2026-08-20")]
        candidates = [{"receipt": unattached[0], "deals": [_deal(5, "2026-08-20", 3300)]}]
        result = freee_monitor.build_company_report(
            company_name="テスト社", stale=stale, new_patterns=new_patterns, unattached=unattached,
            candidates=candidates, sync_issues=[{"id": 2, "name": "Bカード", "sync_status": "error"}],
            state=state,
        )
        text = result["text"]
        self.assertIn("テスト社", text)
        self.assertIn("未処理明細", text)
        self.assertIn("新規1件", text)          # txn 2 のみ新規
        self.assertIn("残2件", text)            # 未処理は合計2件
        self.assertIn("初めて見る摘要", text)
        self.assertIn("未添付の証憑", text)
        self.assertIn("取引ID 5", text)
        self.assertIn("Bカード", text)
        new_state = result["state"]
        self.assertEqual(sorted(new_state["reported_txn_ids"]), [1, 2])
        self.assertEqual(new_state["reported_receipt_ids"], [10])
        self.assertIn("B", new_state["known_patterns"])

    def test_previously_reported_receipt_is_not_reported_again(self):
        unattached = [_receipt(10)]
        state = {"reported_txn_ids": [], "reported_receipt_ids": [10], "known_patterns": []}
        result = freee_monitor.build_company_report(
            company_name="テスト社", stale=[], new_patterns=[], unattached=unattached, candidates=[],
            sync_issues=[], state=state,
        )
        self.assertIsNone(result["text"])


if __name__ == "__main__":
    unittest.main()
