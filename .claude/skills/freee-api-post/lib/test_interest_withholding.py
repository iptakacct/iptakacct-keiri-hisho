"""interest_withholding.py（預金利息の源泉振替）のユニットテスト。
APIレスポンスと同じ構造のdict/listを渡す純粋関数のみを対象にする。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import interest_withholding as iw  # noqa: E402

INTEREST = 900001
TAX = 900002
OTHER = 900003

# 純額入金型の口座の実績。(入金額, 実際に登録されていた総額)
NET_TYPE_HISTORY = [(15, 17), (32, 37), (13, 15), (25, 29), (64, 75), (72, 85),
                    (40, 47), (28, 33), (20, 23), (28, 33), (33, 38)]


def _deal(deal_id, issue_date, details, payments=(), partner_id=None):
    return {"id": deal_id, "issue_date": issue_date, "type": "income",
            "partner_id": partner_id, "details": list(details), "payments": list(payments)}


def _detail(account_item_id, entry_side, amount, tax_code=23, partner_id=None, description=""):
    return {"account_item_id": account_item_id, "entry_side": entry_side, "amount": amount,
            "tax_code": tax_code, "partner_id": partner_id, "description": description}


def _payment(walletable_id, amount):
    return {"from_walletable_type": "bank_account", "from_walletable_id": walletable_id, "amount": amount}


def _journal(journal_id, issue_date, details):
    return {"id": journal_id, "issue_date": issue_date, "details": list(details)}


def _txn(date, walletable_id, amount, description, entry_side="expense"):
    return {"id": 1, "date": date, "walletable_id": walletable_id, "amount": amount,
            "description": description, "entry_side": entry_side}


class GrossFromNetTest(unittest.TestCase):
    def test_matches_net_type_history(self):
        """実績11件すべてで、逆算した総額が実際の登録額と一致する。"""
        for net, gross in NET_TYPE_HISTORY:
            self.assertEqual(iw.gross_from_net(net), gross, f"入金{net}円")

    def test_withholding_is_difference(self):
        for net, gross in NET_TYPE_HISTORY:
            self.assertEqual(iw.withholding_from_net(net), gross - net)

    def test_september_case(self):
        """2026-09-01 入金39円 → 総額46円・源泉7円。"""
        self.assertEqual(iw.gross_from_net(39), 46)
        self.assertEqual(iw.withholding_from_net(39), 7)

    def test_zero_and_negative(self):
        self.assertEqual(iw.gross_from_net(0), 0)
        self.assertEqual(iw.gross_from_net(-5), 0)
        self.assertEqual(iw.withholding_from_net(0), 0)

    def test_large_amount_uses_integer_math(self):
        """大きい金額でも整数演算で計算する（浮動小数点の丸めに引きずられない）。"""
        self.assertEqual(iw.gross_from_net(1_000_000), 1_180_846)
        self.assertTrue(iw.is_consistent(1_000_000))

    def test_consistency_holds_for_history(self):
        for net, _ in NET_TYPE_HISTORY:
            self.assertTrue(iw.is_consistent(net), f"入金{net}円")

    def test_consistency_holds_for_all_realistic_amounts(self):
        """1〜20,000円の全額で逆算と順算が整合する（この式の妥当性の確認）。
        整合しない金額は実際には現れないが、is_consistent は将来の率変更時の
        安全網として残してある。"""
        self.assertEqual([n for n in range(1, 20001) if not iw.is_consistent(n)], [])

    def test_consistency_false_for_zero(self):
        self.assertFalse(iw.is_consistent(0))

    def test_one_yen_has_no_withholding(self):
        """入金1〜2円は総額＝入金額（源泉0円）。find_targets では too_small で除外される。"""
        self.assertEqual(iw.withholding_from_net(1), 0)
        self.assertEqual(iw.withholding_from_net(2), 0)


class ResolveTest(unittest.TestCase):
    def test_find_account_id_prefers_first_name(self):
        accounts = [{"id": 1, "name": "法人税等"}, {"id": 2, "name": "法人税・住民税及び事業税"}]
        self.assertEqual(iw.find_account_id(accounts, iw.TAX_ACCOUNT_NAMES), (2, "法人税・住民税及び事業税"))

    def test_find_account_id_falls_back(self):
        accounts = [{"id": 1, "name": "法人税等"}]
        self.assertEqual(iw.find_account_id(accounts, iw.TAX_ACCOUNT_NAMES), (1, "法人税等"))

    def test_find_account_id_missing(self):
        self.assertEqual(iw.find_account_id([{"id": 1, "name": "雑収入"}], iw.TAX_ACCOUNT_NAMES), (None, None))

    def test_find_item_id_variants(self):
        self.assertEqual(iw.find_item_id([{"id": 9, "name": "利息源泉税"}], iw.WITHHOLDING_ITEM_NAMES),
                         (9, "利息源泉税"))
        self.assertEqual(iw.find_item_id([{"id": 9, "name": "住民税"}], iw.WITHHOLDING_ITEM_NAMES), (None, None))

    def test_find_tax_code(self):
        codes = [{"code": 2, "name_ja": "対象外"}, {"code": 23, "name_ja": "非課売上"}]
        self.assertEqual(iw.find_tax_code(codes, "対象外"), 2)
        self.assertIsNone(iw.find_tax_code(codes, "課対仕入10%"))


class InferItemTest(unittest.TestCase):
    def test_infers_item_from_past_interest_deals(self):
        """過去の利息仕訳で使われている品目を引き継ぐ（会社ごとに品目名が違うため）。"""
        deals = [
            _deal(1, "2026-08-01", [_detail(INTEREST, "credit", 17),
                                    dict(_detail(TAX, "debit", 2, tax_code=2), item_id=500001)]),
            _deal(2, "2026-07-01", [_detail(INTEREST, "credit", 37),
                                    dict(_detail(TAX, "debit", 5, tax_code=2), item_id=500001)]),
        ]
        self.assertEqual(iw.infer_item_id_from_history(deals, {INTEREST}, {TAX}), 500001)

    def test_ignores_items_on_unrelated_deals(self):
        deals = [_deal(3, "2026-08-01", [dict(_detail(OTHER, "debit", 100), item_id=999)])]
        self.assertIsNone(iw.infer_item_id_from_history(deals, {INTEREST}, {TAX}))

    def test_returns_none_without_history(self):
        self.assertIsNone(iw.infer_item_id_from_history([], {INTEREST}, {TAX}))

    def test_picks_the_most_used_item(self):
        deals = [
            _deal(4, "2026-06-01", [_detail(INTEREST, "credit", 15),
                                    dict(_detail(TAX, "debit", 2, tax_code=2), item_id=111)]),
            _deal(5, "2026-07-01", [_detail(INTEREST, "credit", 15),
                                    dict(_detail(TAX, "debit", 2, tax_code=2), item_id=222)]),
            _deal(6, "2026-08-01", [_detail(INTEREST, "credit", 15),
                                    dict(_detail(TAX, "debit", 2, tax_code=2), item_id=222)]),
        ]
        self.assertEqual(iw.infer_item_id_from_history(deals, {INTEREST}, {TAX}), 222)


class FindTargetsTest(unittest.TestCase):
    def setUp(self):
        self.net_deal = _deal(100, "2026-09-01", [_detail(INTEREST, "credit", 39, partner_id=7)],
                              payments=[_payment(4000001, 39)])

    def test_picks_net_type_deal(self):
        result = iw.find_targets([self.net_deal], [], [], {INTEREST}, {TAX})
        self.assertEqual(len(result["targets"]), 1)
        target = result["targets"][0]
        self.assertEqual((target["net"], target["gross"], target["withholding"]), (39, 46, 7))
        self.assertEqual(target["tax_code"], 23)
        self.assertEqual(target["partner_id"], 7)
        self.assertEqual(target["walletable_id"], 4000001)

    def test_skips_deal_already_booked_gross(self):
        """従来型（1取引・2明細）で総額計上済みの取引は対象外。"""
        deal = _deal(101, "2026-08-01",
                     [_detail(INTEREST, "credit", 17), _detail(TAX, "debit", 2, tax_code=2)])
        result = iw.find_targets([deal], [], [], {INTEREST}, {TAX})
        self.assertEqual(result["targets"], [])
        self.assertEqual(result["excluded"][0]["reason"], "already_gross")

    def test_skips_gross_type_account(self):
        """同日・同口座に「国税」の出金明細があれば総額入金型なので対象外。"""
        txns = [_txn("2026-09-01", 4000001, 7, "国税")]
        result = iw.find_targets([self.net_deal], [], txns, {INTEREST}, {TAX})
        self.assertEqual(result["targets"], [])
        self.assertEqual(result["excluded"][0]["reason"], "gross_type")

    def test_gross_type_marker_requires_same_account(self):
        """別口座の納税明細は根拠にしない。"""
        txns = [_txn("2026-09-01", 9999999, 7, "国税")]
        result = iw.find_targets([self.net_deal], [], txns, {INTEREST}, {TAX})
        self.assertEqual(len(result["targets"]), 1)

    def test_gross_type_marker_ignores_income_side(self):
        txns = [_txn("2026-09-01", 4000001, 7, "国税", entry_side="income")]
        self.assertEqual(len(iw.find_targets([self.net_deal], [], txns, {INTEREST}, {TAX})["targets"]), 1)

    def test_skips_gross_type_by_matching_tax_amount_on_nearby_date(self):
        """税金の出金が別日に立つ銀行でも、金額が一致すれば総額入金型と判定する。

        住信SBIのように「利息の入金」と「税金の出金」が2本の明細で出てくる型。
        入金39円 × 15.315% = 5円（切捨）が同口座で数日以内に出金されていれば、
        入金39円は既に総額なので割り戻さない。"""
        txns = [_txn("2026-09-03", 4000001, 5, "ｿｼﾞｮｳ", entry_side="expense")]
        result = iw.find_targets([self.net_deal], [], txns, {INTEREST}, {TAX})
        self.assertEqual(result["targets"], [])
        self.assertEqual(result["excluded"][0]["reason"], "gross_type")

    def test_unrelated_tax_payment_does_not_block(self):
        """同じ口座の無関係な納税明細（金額が源泉税額と合わない）では除外しない。

        純額入金型の口座には「PE 地方税共同機構」等の納税明細が立つが、
        GMOは純額入金型なので割戻しは必要。金額一致を主判定にしているので誤爆しない。"""
        txns = [_txn("2026-09-03", 4000001, 13100, "PE 地方税共同機構", entry_side="expense")]
        result = iw.find_targets([self.net_deal], [], txns, {INTEREST}, {TAX})
        self.assertEqual(len(result["targets"]), 1)

    def test_matching_amount_outside_tolerance_does_not_block(self):
        txns = [_txn("2026-09-30", 4000001, 5, "国税", entry_side="expense")]
        self.assertEqual(len(iw.find_targets([self.net_deal], [], txns, {INTEREST}, {TAX})["targets"]), 1)

    def test_withholding_on_gross(self):
        """住信SBI（総額入金型）の実績5件：国税＝利息×15.315%（1円未満切り捨て）。"""
        for gross, tax in [(69963, 10714), (63271, 9689), (39413, 6036), (18529, 2837), (44892, 6875)]:
            self.assertEqual(iw.withholding_on_gross(gross), tax, f"総額{gross}円")

    def test_skips_when_journal_has_marker(self):
        journal = _journal(1, "2026-09-01", [
            _detail(TAX, "debit", 7, tax_code=2, description=f"源泉（{iw.deal_marker(100)}）"),
            _detail(INTEREST, "credit", 7)])
        result = iw.find_targets([self.net_deal], [journal], [], {INTEREST}, {TAX})
        self.assertEqual(result["targets"], [])
        self.assertEqual(result["excluded"][0]["reason"], "already_journaled")

    def test_skips_when_manual_journal_matches_date_and_amount(self):
        """目印の無い手起票の伝票（金額・日付が一致）も二重起票しない。"""
        journal = _journal(2, "2026-09-01", [
            _detail(TAX, "debit", 7, tax_code=2, description="普通預金利息 源泉所得税（総額46円）"),
            _detail(INTEREST, "credit", 7, description="普通預金利息 源泉税相当分（総額46円）")])
        result = iw.find_targets([self.net_deal], [journal], [], {INTEREST}, {TAX})
        self.assertEqual(result["targets"], [])
        self.assertEqual(result["excluded"][0]["reason"], "already_journaled")

    def test_different_amount_journal_does_not_block(self):
        journal = _journal(3, "2026-09-01", [
            _detail(TAX, "debit", 99, tax_code=2), _detail(INTEREST, "credit", 99)])
        self.assertEqual(len(iw.find_targets([self.net_deal], [journal], [], {INTEREST}, {TAX})["targets"]), 1)

    def test_skips_too_small(self):
        deal = _deal(104, "2026-09-01", [_detail(INTEREST, "credit", 1)])
        result = iw.find_targets([deal], [], [], {INTEREST}, {TAX})
        self.assertEqual(result["targets"], [])
        self.assertEqual(result["excluded"][0]["reason"], "too_small")

    def test_ignores_unrelated_deals(self):
        deal = _deal(105, "2026-09-01", [_detail(OTHER, "credit", 50000)])
        result = iw.find_targets([deal], [], [], {INTEREST}, {TAX})
        self.assertEqual(result["targets"], [])
        self.assertEqual(result["excluded"], [])

    def test_sums_multiple_interest_lines(self):
        deal = _deal(106, "2026-09-01",
                     [_detail(INTEREST, "credit", 20), _detail(INTEREST, "credit", 19)])
        target = iw.find_targets([deal], [], [], {INTEREST}, {TAX})["targets"][0]
        self.assertEqual((target["net"], target["withholding"]), (39, 7))

    def test_results_are_sorted_by_date(self):
        older = _deal(107, "2026-07-01", [_detail(INTEREST, "credit", 32)])
        result = iw.find_targets([self.net_deal, older], [], [], {INTEREST}, {TAX})
        self.assertEqual([t["issue_date"] for t in result["targets"]], ["2026-07-01", "2026-09-01"])


class BuildPayloadTest(unittest.TestCase):
    def setUp(self):
        self.target = {"deal_id": 100, "issue_date": "2026-09-01", "net": 39, "gross": 46,
                       "withholding": 7, "tax_code": 23, "partner_id": 7, "walletable_id": 4000001}

    def test_payload_shape(self):
        payload = iw.build_journal_payload(100001, self.target, INTEREST, TAX, 2, item_id=500001)
        self.assertEqual(payload["company_id"], 100001)
        self.assertEqual(payload["issue_date"], "2026-09-01")
        self.assertFalse(payload["adjustment"])
        debit, credit = payload["details"]
        self.assertEqual((debit["entry_side"], debit["account_item_id"], debit["amount"]), ("debit", TAX, 7))
        self.assertEqual(debit["tax_code"], 2)
        self.assertEqual(debit["item_id"], 500001)
        self.assertEqual((credit["entry_side"], credit["account_item_id"], credit["amount"]),
                         ("credit", INTEREST, 7))
        self.assertEqual(credit["tax_code"], 23)
        self.assertEqual(debit["partner_id"], 7)

    def test_description_carries_marker(self):
        payload = iw.build_journal_payload(1, self.target, INTEREST, TAX, 2)
        for detail in payload["details"]:
            self.assertIn(iw.deal_marker(100), detail["description"])
            self.assertIn("総額46円", detail["description"])

    def test_item_and_partner_omitted_when_absent(self):
        target = dict(self.target, partner_id=None)
        payload = iw.build_journal_payload(1, target, INTEREST, TAX, 2)
        for detail in payload["details"]:
            self.assertNotIn("item_id", detail)
            self.assertNotIn("partner_id", detail)

    def test_payload_balances(self):
        payload = iw.build_journal_payload(1, self.target, INTEREST, TAX, 2)
        debit = sum(d["amount"] for d in payload["details"] if d["entry_side"] == "debit")
        credit = sum(d["amount"] for d in payload["details"] if d["entry_side"] == "credit")
        self.assertEqual(debit, credit)


class BuildReportTest(unittest.TestCase):
    def test_empty_when_nothing_to_say(self):
        self.assertEqual(iw.build_report("サンプルA株式会社", [], []), "")

    def test_lists_registered_and_attention(self):
        text = iw.build_report(
            "サンプルA株式会社",
            [{"issue_date": "2026-09-01", "net": 39, "gross": 46, "withholding": 7, "journal_id": 999}],
            [{"issue_date": "2026-08-01", "net": 2, "reason": "inconsistent"}])
        self.assertIn("サンプルA株式会社", text)
        self.assertIn("入金39円", text)
        self.assertIn("総額46円", text)
        self.assertIn("伝票id 999", text)
        self.assertIn("逆算が不整合", text)


if __name__ == "__main__":
    unittest.main()
