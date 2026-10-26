"""invoxの弥生形式仕訳データ（journal/exportの応答）を postJournals 用の形へ変換する処理のテスト。"""
import unittest

import yayoi_journal

# 実際の出力と同じ25列・cp932・CRLF。1行で借方貸方が揃う単一仕訳。
SINGLE_ROW = (
    "2111,,,2026/08/31,未確定勘定,,B部門,課仕 10%,4680,425,"
    "未払金,,,対象外,4680,0,,,2026/09/28,3,,invox:IR0000000099,0,0,0"
)

# invox部門名 → 会社名 の対応表（accounts.json の department_to_company と同じ形）
DEPT_MAP = {"A部門": "A社", "B部門": "B社"}


def _encode(*lines: str) -> bytes:
    return ("\r\n".join(lines) + "\r\n").encode("cp932")


class TestParseRows(unittest.TestCase):
    def test_decodes_cp932_and_returns_25_columns(self):
        rows = yayoi_journal.parse_rows(_encode(SINGLE_ROW))
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 25)
        self.assertEqual(rows[0][4], "未確定勘定")

    def test_keeps_quoted_remark_containing_comma_as_one_field(self):
        """摘要（17列目）はカンマを含むとダブルクォートで囲まれて出てくる。
        単純なsplit(',')では列がずれる。"""
        row = (
            '2111,,,2026/09/01,地代家賃,取引先D㈱,,課仕 10%,678035,61639,'
            '未払金,,,対象外,678035,0,'
            '"2026年9月分賃料・共益費（7月分水道光熱費　46,679）",,2026/08/25,3,,invox:IR0000000097,0,0,0'
        )
        rows = yayoi_journal.parse_rows(_encode(row))
        self.assertEqual(len(rows[0]), 25)
        self.assertEqual(rows[0][16], "2026年9月分賃料・共益費（7月分水道光熱費　46,679）")
        self.assertEqual(rows[0][18], "2026/08/25")

    def test_skips_blank_lines(self):
        rows = yayoi_journal.parse_rows(_encode(SINGLE_ROW, "", SINGLE_ROW))
        self.assertEqual(len(rows), 2)

    def test_raises_on_unexpected_column_count(self):
        with self.assertRaises(yayoi_journal.YayoiFormatError):
            yayoi_journal.parse_rows(_encode("2111,,,2026/08/31,未確定勘定"))


class TestGroupJournals(unittest.TestCase):
    def test_single_row_becomes_one_debit_and_one_credit(self):
        journals = yayoi_journal.group_journals(yayoi_journal.parse_rows(_encode(SINGLE_ROW)))
        self.assertEqual(len(journals), 1)
        j = journals[0]
        self.assertEqual(j["invoice_id"], "IR0000000099")
        self.assertEqual(j["date"], "2026/08/31")
        self.assertEqual(j["due"], "2026/09/28")
        self.assertEqual(j["debits"], [{"account": "未確定勘定", "sub": None, "dept": "B部門",
                                        "tax": "課仕 10%", "amount_taxin": 4680, "tax_amount": 425}])
        self.assertEqual(j["credits"], [{"account": "未払金", "sub": None, "dept": None,
                                         "tax": "対象外", "amount_taxin": 4680, "tax_amount": 0}])
        self.assertEqual(j["remarks"], [])

    def test_groups_compound_entry_by_invox_memo_not_by_flag(self):
        """識別フラグ（2110/2100/2101）はグループ化に使えない（規則性が読み取れない）。
        仕訳メモの invox:IRxxxx でまとめる。"""
        rows = _encode(
            "2110,,,2026/07/31,支払手数料,取引先C㈱,,課仕 10%,1320000,120000,"
            ",,,対象外,0,0,,,2026/08/31,3,,invox:IR0000000098,0,0,0",
            "2100,,,2026/07/31,,,,対象外,0,0,仮払金,取引先C㈱,,対象外,900000,0,"
            "8/4発生仮払金精算,,2026/08/31,3,,invox:IR0000000098,0,0,0",
            "2101,,,2026/07/31,,,,対象外,0,0,仮払金,取引先C㈱,,対象外,420000,0,"
            "8/7発生仮払金一部精算,,2026/08/31,3,,invox:IR0000000098,0,0,0",
        )
        journals = yayoi_journal.group_journals(yayoi_journal.parse_rows(rows))
        self.assertEqual(len(journals), 1)
        j = journals[0]
        self.assertEqual(len(j["debits"]), 1)
        self.assertEqual(len(j["credits"]), 2)
        self.assertEqual([c["amount_taxin"] for c in j["credits"]], [900000, 420000])
        self.assertEqual(j["remarks"], ["8/4発生仮払金精算", "8/7発生仮払金一部精算"])

    def test_keeps_first_occurrence_order_of_invoices(self):
        rows = _encode(SINGLE_ROW, SINGLE_ROW.replace("IR0000000099", "IR0000000001"), SINGLE_ROW)
        journals = yayoi_journal.group_journals(yayoi_journal.parse_rows(rows))
        self.assertEqual([j["invoice_id"] for j in journals], ["IR0000000099", "IR0000000001"])

    def test_raises_when_row_has_no_invox_memo(self):
        row = SINGLE_ROW.replace("invox:IR0000000099", "手入力メモ")
        with self.assertRaises(yayoi_journal.YayoiFormatError):
            yayoi_journal.group_journals(yayoi_journal.parse_rows(_encode(row)))


class TestBalanceCheck(unittest.TestCase):
    def test_reports_unbalanced_journal(self):
        rows = _encode(SINGLE_ROW.replace(",未払金,,,対象外,4680,0", ",未払金,,,対象外,4000,0"))
        journals = yayoi_journal.group_journals(yayoi_journal.parse_rows(rows))
        self.assertEqual(yayoi_journal.unbalanced(journals),
                         [{"invoice_id": "IR0000000099", "debit_total": 4680, "credit_total": 4000}])

    def test_balanced_journal_is_not_reported(self):
        journals = yayoi_journal.group_journals(yayoi_journal.parse_rows(_encode(SINGLE_ROW)))
        self.assertEqual(yayoi_journal.unbalanced(journals), [])


class TestSplitByCompany(unittest.TestCase):
    def test_maps_department_name_to_company(self):
        journals = yayoi_journal.group_journals(yayoi_journal.parse_rows(
            _encode(SINGLE_ROW, SINGLE_ROW.replace("IR0000000099", "IR0000000002"))))
        by_company, unknown = yayoi_journal.split_by_company(
            journals, {"IR0000000099": "B部門", "IR0000000002": "A部門"}, DEPT_MAP)
        self.assertEqual(sorted(by_company), ["A社", "B社"])
        self.assertEqual(by_company["B社"][0]["invoice_id"], "IR0000000099")
        self.assertEqual(unknown, [])

    def test_unknown_department_is_reported_not_guessed(self):
        """部門対応表に無い部門は会社を推測せず、そのまま報告する。"""
        journals = yayoi_journal.group_journals(yayoi_journal.parse_rows(_encode(SINGLE_ROW)))
        by_company, unknown = yayoi_journal.split_by_company(
            journals, {"IR0000000099": "新設部門"}, DEPT_MAP)
        self.assertEqual(by_company, {})
        self.assertEqual(unknown, [{"invoice_id": "IR0000000099", "department_name": "新設部門"}])

    def test_invoice_missing_from_department_map_is_reported(self):
        journals = yayoi_journal.group_journals(yayoi_journal.parse_rows(_encode(SINGLE_ROW)))
        by_company, unknown = yayoi_journal.split_by_company(journals, {}, DEPT_MAP)
        self.assertEqual(by_company, {})
        self.assertEqual(unknown, [{"invoice_id": "IR0000000099", "department_name": None}])


class TestBuildPrepared(unittest.TestCase):
    def test_includes_count_and_total(self):
        journals = yayoi_journal.group_journals(yayoi_journal.parse_rows(
            _encode(SINGLE_ROW, SINGLE_ROW.replace("IR0000000099", "IR0000000003"))))
        prepared = yayoi_journal.build_prepared("B社", "export-01.txt", journals)
        self.assertEqual(prepared["company"], "B社")
        self.assertEqual(prepared["source_file"], "export-01.txt")
        self.assertEqual(prepared["count"], 2)
        self.assertEqual(prepared["total_taxin"], 9360)
        self.assertEqual(len(prepared["journals"]), 2)


if __name__ == "__main__":
    unittest.main()
