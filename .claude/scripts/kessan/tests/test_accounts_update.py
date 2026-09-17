from datetime import datetime

import pytest

from accounts_update import approve, load_updates, set_accounts
from common import STAGING_COLUMNS, KessanError, read_rows, write_rows
from helpers import staging_row
from post import post_approved

NOW = datetime(2026, 9, 17, 10, 0, 0)
PAYMENT = {("普通預金", "サンプル銀行")}


def fee(source_id="bank:a", **extra):
    values = dict(日付="2025-04-05", 借方金額="330", 貸方科目="普通預金", 貸方補助="サンプル銀行", 貸方金額="330",
                  摘要="テスウリヨウ", 取り込み元ID=source_id, 読み取り信頼度="高", 判定="要確認", 要確認理由="相手科目未設定")
    values.update(extra)
    return staging_row(**values)


def write_staging(year_dir, rows):
    write_rows(year_dir / "staging.csv", STAGING_COLUMNS, rows)
    return (year_dir / "staging.csv").read_bytes()


def test_set_accounts_fills_candidates(year_dir, accounts):
    write_staging(year_dir, [fee(), fee("bank:b")])
    count = set_accounts(year_dir, accounts, {
        "bank:a": {"借方科目": "支払手数料", "取引先": "サンプル銀行", "要確認理由": "新規の摘要"},
    }, PAYMENT)
    assert count == 1
    first, second = read_rows(year_dir / "staging.csv")
    assert (first["借方科目"], first["取引先"], first["判定"], first["要確認理由"], first["承認"]) == (
        "支払手数料", "サンプル銀行", "要確認", "新規の摘要", "")
    assert second["借方科目"] == ""


@pytest.mark.parametrize("updates, message", [
    ({"bank:a": {"借方科目": "支払手数料", "判定": "確立済み"}}, "判定=確立済み はAIからは指定できません"),
    ({"bank:a": {"判定": "OK"}}, "判定に指定できるのは「要確認」だけです"),
    ({"bank:none": {"借方科目": "支払手数料"}}, "取り込み元ID bank:none: staging.csv にありません"),
    ({"": {"借方科目": "支払手数料"}}, "取り込み元ID : staging.csv にありません"),
    ({"bank:a": {"借方科目": "謎の科目"}}, "科目マスタに無い借方科目「謎の科目」"),
    ({"bank:ok": {"借方科目": "支払手数料"}}, "承認済みの行は上書きできません"),
    ({"bank:a": {"承認": "済"}}, "書き込めない列 承認"),
    ({"bank:a": {"借方科目": 100}}, "借方科目 は文字列で書く"),
    ({"bank:a": {"貸方科目": "現金", "貸方補助": ""}}, "明細側の貸方（普通預金 サンプル銀行）は変更できません"),
    ({"bank:a": ["支払手数料"]}, "値は {列名: 値} の形で書く"),
])
def test_set_accounts_rejects_invalid_updates(year_dir, accounts, updates, message):
    before = write_staging(year_dir, [fee(), fee("bank:ok", 借方科目="支払手数料", 承認="済"), staging_row(日付="2025-04-06")])
    with pytest.raises(KessanError, match="何も変更していません") as e:
        set_accounts(year_dir, accounts, updates, PAYMENT)
    assert message in str(e.value)
    assert (year_dir / "staging.csv").read_bytes() == before


def test_one_invalid_update_blocks_all(year_dir, accounts):
    before = write_staging(year_dir, [fee(), fee("bank:b")])
    with pytest.raises(KessanError):
        set_accounts(year_dir, accounts, {
            "bank:a": {"借方科目": "支払手数料"},
            "bank:b": {"借方科目": "謎の科目"},
        }, PAYMENT)
    assert (year_dir / "staging.csv").read_bytes() == before


def test_load_updates_reads_json_file(tmp_path):
    path = tmp_path / "set-accounts.json"
    path.write_text('{"bank:a": {"借方科目": "支払手数料"}}', encoding="utf-8")
    assert load_updates(path) == {"bank:a": {"借方科目": "支払手数料"}}


def test_load_updates_rejects_broken_json(tmp_path):
    path = tmp_path / "set-accounts.json"
    path.write_text('{"bank:a": ', encoding="utf-8")
    with pytest.raises(KessanError, match="JSONとして読めません"):
        load_updates(path)


def test_empty_inputs_raise(year_dir, accounts):
    with pytest.raises(KessanError, match="1件以上"):
        set_accounts(year_dir, accounts, {}, PAYMENT)
    with pytest.raises(KessanError, match="承認する取り込み元IDがありません"):
        approve(year_dir, accounts, [" "])


def test_approve_marks_rows(year_dir, accounts):
    write_staging(year_dir, [fee(借方科目="支払手数料"), fee("bank:b", 借方科目="支払手数料"), fee("bank:c")])
    assert approve(year_dir, accounts, ["bank:a", "bank:b", "bank:a"]) == 2
    assert [r["承認"] for r in read_rows(year_dir / "staging.csv")] == ["済", "済", ""]


def test_approve_rejects_incomplete_row(year_dir, accounts):
    before = write_staging(year_dir, [fee(借方科目="支払手数料"), fee("bank:b")])
    with pytest.raises(KessanError, match="取り込み元ID bank:b: 借方科目が未設定"):
        approve(year_dir, accounts, ["bank:a", "bank:b"])
    assert (year_dir / "staging.csv").read_bytes() == before


def test_approve_unknown_id(year_dir, accounts):
    write_staging(year_dir, [fee(借方科目="支払手数料")])
    with pytest.raises(KessanError, match="取り込み元ID bank:none: staging.csv にありません"):
        approve(year_dir, accounts, ["bank:none"])


def test_approved_rows_can_be_posted(year_dir, accounts):
    write_staging(year_dir, [fee()])
    set_accounts(year_dir, accounts, {"bank:a": {"借方科目": "支払手数料"}}, PAYMENT)
    approve(year_dir, accounts, ["bank:a"])
    result = post_approved(year_dir, accounts, now=NOW)
    assert (result.vouchers, result.remaining) == (["1"], 0)
    (posted,) = read_rows(year_dir / "journal.csv")
    assert (posted["借方科目"], posted["貸方科目"], posted["登録区分"]) == ("支払手数料", "普通預金", "確認済")


# --- F1：スクリプトが付けた要確認理由は set-accounts で消えない ---

DOUBLE_BOOKED = "証憑から計上済みの仕訳と金額・日付が近い（二重計上の疑い）"


def test_set_accounts_cannot_erase_script_reasons_and_auto_approve_skips_row(year_dir, accounts, tmp_path):
    from match import load_abbreviations
    from patterns import auto_approve, load_patterns
    write_staging(year_dir, [fee(要確認理由=f"相手科目未設定／{DOUBLE_BOOKED}")])
    set_accounts(year_dir, accounts, {"bank:a": {"借方科目": "支払手数料", "要確認理由": ""}}, PAYMENT)
    (row,) = read_rows(year_dir / "staging.csv")
    assert row["要確認理由"] == DOUBLE_BOOKED
    path = tmp_path / "kessan-patterns.yaml"
    path.write_text("patterns:\n  - 名前: 振込手数料\n    入出金: 出金\n    摘要キーワード: テスウリヨウ\n    科目: 支払手数料\n",
                    encoding="utf-8")
    result = auto_approve(year_dir, accounts, load_patterns(path, accounts), PAYMENT, load_abbreviations())
    assert result.approved == 0
    assert read_rows(year_dir / "staging.csv")[0]["承認"] == ""


def test_set_accounts_appends_ai_reason_to_existing_reasons(year_dir, accounts):
    write_staging(year_dir, [fee(要確認理由="相手科目未設定／ページの残高が連続しない")])
    set_accounts(year_dir, accounts, {"bank:a": {"借方科目": "支払手数料", "要確認理由": "新規の摘要"}}, PAYMENT)
    assert read_rows(year_dir / "staging.csv")[0]["要確認理由"] == "ページの残高が連続しない／新規の摘要"


def test_set_accounts_keeps_unset_reason_while_counter_account_is_empty(year_dir, accounts):
    write_staging(year_dir, [fee()])
    set_accounts(year_dir, accounts, {"bank:a": {"取引先": "サンプル銀行", "要確認理由": "摘要が読めない"}}, PAYMENT)
    assert read_rows(year_dir / "staging.csv")[0]["要確認理由"] == "相手科目未設定／摘要が読めない"
