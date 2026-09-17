from datetime import datetime

import pytest

from bank_import import UNSET_COUNTER_ACCOUNT
from common import STAGING_COLUMNS, KessanError, read_rows, write_rows
from helpers import staging_row
from match import load_abbreviations
from patterns import Pattern, auto_approve, load_patterns
from post import post_approved

NOW = datetime(2026, 9, 17, 10, 0, 0)
PAYMENT = {("普通預金", "サンプル銀行"), ("現金", "")}
PATTERNS_YAML = """\
patterns:
  - 名前: 振込手数料
    入出金: 出金
    摘要キーワード: テスウリヨウ
    科目: 支払手数料
    金額の範囲: [0, 1000]
    確認日: 2026-09-17
  - 名前: テスト商事からの売上
    入出金: 入金
    取引先: 株式会社テストシヨウジ
    科目: 売上高
    補助: ""
"""


@pytest.fixture(scope="module")
def abbreviations():
    return load_abbreviations()


def write_patterns(tmp_path, text=PATTERNS_YAML):
    path = tmp_path / "kessan-patterns.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def fee(source_id="bank:fee", amount="330", **extra):
    values = dict(日付="2025-04-05", 借方金額=amount, 貸方科目="普通預金", 貸方補助="サンプル銀行", 貸方金額=amount,
                  摘要="テスウリヨウ", 取り込み元ID=source_id, 読み取り信頼度="高", 判定="要確認", 要確認理由="相手科目未設定")
    values.update(extra)
    return staging_row(**values)


def deposit(source_id="bank:dep", **extra):
    values = dict(日付="2025-04-01", 借方科目="普通預金", 借方補助="サンプル銀行", 借方金額="100000", 貸方金額="100000",
                  摘要="フリコミ カ)テストシヨウジ", 取り込み元ID=source_id, 読み取り信頼度="高", 判定="要確認",
                  要確認理由="相手科目未設定")
    values.update(extra)
    return staging_row(**values)


def run(year_dir, tmp_path, accounts, abbreviations, rows, text=PATTERNS_YAML):
    write_rows(year_dir / "staging.csv", STAGING_COLUMNS, rows)
    patterns = load_patterns(write_patterns(tmp_path, text), accounts)
    return auto_approve(year_dir, accounts, patterns, PAYMENT, abbreviations)


def test_load_patterns_reads_file(tmp_path, accounts):
    assert load_patterns(write_patterns(tmp_path), accounts) == [
        Pattern("振込手数料", "出金", "テスウリヨウ", "", "支払手数料", "", (0, 1000)),
        Pattern("テスト商事からの売上", "入金", "", "株式会社テストシヨウジ", "売上高", "", ()),
    ]


def test_missing_file_means_no_patterns(tmp_path, accounts):
    assert load_patterns(tmp_path / "none.yaml", accounts) == []


@pytest.mark.parametrize("text, message", [
    ("patterns:\n  - 名前: A\n    摘要キーワード: X\n    科目: 雑費\n", "入出金は 入金／出金 のどちらかで書く"),
    ("patterns:\n  - 名前: A\n    入出金: 出金\n    科目: 雑費\n", "摘要キーワード・取引先のどちらかを書く"),
    ("patterns:\n  - 名前: A\n    入出金: 出金\n    摘要キーワード: X\n    科目: 謎の科目\n", "科目マスタに無い科目「謎の科目」"),
    ("patterns:\n  - 名前: A\n    入出金: 出金\n    摘要キーワード: X\n    科目: 雑費\n    金額の範囲: [1000, 0]\n",
     "金額の範囲は [下限, 上限] の整数で書く"),
    ("patterns: 振込手数料\n", "patterns は「- 名前: …」の並び（リスト）で書く"),
    ("patterns: [\n", "kessan-patterns.yaml を読めません"),
    ("patterns:\n  - 名前: A\n    入出金: 出金\n    摘要キーワード: X\n    科目: 雑費\n", "摘要キーワード「X」は短すぎます"),
    ("patterns:\n  - 名前: A\n    入出金: 入金\n    取引先: Y\n    科目: 売上高\n", "取引先「Y」は短すぎます"),
])
def test_invalid_patterns_raise(tmp_path, accounts, text, message):
    with pytest.raises(KessanError, match="kessan-patterns.yaml") as e:
        load_patterns(write_patterns(tmp_path, text), accounts)
    assert message in str(e.value)


def test_matching_bank_rows_are_approved(year_dir, tmp_path, accounts, abbreviations):
    result = run(year_dir, tmp_path, accounts, abbreviations, [fee(), deposit()])
    assert (result.approved, result.conflicts) == (2, 0)
    fee_row, deposit_row = read_rows(year_dir / "staging.csv")
    assert (fee_row["借方科目"], fee_row["判定"], fee_row["承認"], fee_row["要確認理由"]) == (
        "支払手数料", "確立済み", "済", "確立済みパターン「振込手数料」")
    assert (deposit_row["貸方科目"], deposit_row["判定"], deposit_row["承認"]) == ("売上高", "確立済み", "済")


def test_amount_outside_range_is_not_approved(year_dir, tmp_path, accounts, abbreviations):
    assert run(year_dir, tmp_path, accounts, abbreviations, [fee(amount="3300")]).approved == 0


def test_direction_must_match(year_dir, tmp_path, accounts, abbreviations):
    assert run(year_dir, tmp_path, accounts, abbreviations, [deposit(摘要="テスウリヨウ", 借方金額="330", 貸方金額="330")]).approved == 0


@pytest.mark.parametrize("extra", [
    {"読み取り信頼度": "低"},
    {"要確認理由": "取り込み済みの明細と日付・金額が一致（二重取り込みの疑い）"},
    {"要確認理由": "ページの残高が連続しない"},
    {"要確認理由": "未払候補の証憑と金額が一致（支払の可能性）"},
    {"要確認理由": "証憑から計上済みの仕訳と金額・日付が近い（二重計上の疑い）"},
    {"要確認理由": "証憑と一致"},
    {"要確認理由": "相手科目未設定／証憑の重複の疑い（証憑ID x）"},
    {"要確認理由": "謎の理由"},
])
def test_uncertain_rows_are_not_approved(year_dir, tmp_path, accounts, abbreviations, extra):
    assert run(year_dir, tmp_path, accounts, abbreviations, [fee(**extra)]).approved == 0
    assert read_rows(year_dir / "staging.csv")[0]["承認"] == ""


def test_receipt_rows_are_not_approved(year_dir, tmp_path, accounts, abbreviations):
    row = fee(source_id="receipt:abc", 貸方科目="現金", 貸方補助="")
    assert run(year_dir, tmp_path, accounts, abbreviations, [row]).approved == 0


def test_empty_reason_is_approved(year_dir, tmp_path, accounts, abbreviations):
    assert run(year_dir, tmp_path, accounts, abbreviations, [fee(要確認理由="")]).approved == 1


def test_unset_counter_account_reason_is_approved(year_dir, tmp_path, accounts, abbreviations):
    assert run(year_dir, tmp_path, accounts, abbreviations, [fee(要確認理由=UNSET_COUNTER_ACCOUNT)]).approved == 1


def test_unset_counter_account_with_space_chars_is_approved(year_dir, tmp_path, accounts, abbreviations):
    # Leading/trailing whitespace should not prevent matching
    result = run(year_dir, tmp_path, accounts, abbreviations, [fee(要確認理由="  " + UNSET_COUNTER_ACCOUNT + "  ")])
    assert result.approved == 1


def test_ai_candidate_that_differs_is_flagged(year_dir, tmp_path, accounts, abbreviations):
    # set-accounts で相手科目を入れた後の行（相手科目未設定は外れている）
    result = run(year_dir, tmp_path, accounts, abbreviations, [fee(借方科目="雑費", 要確認理由="")])
    assert (result.approved, result.conflicts) == (0, 1)
    (row,) = read_rows(year_dir / "staging.csv")
    assert (row["借方科目"], row["承認"], row["要確認理由"]) == ("雑費", "", "確立済みパターン「振込手数料」（支払手数料）と科目候補が違う")


def test_ai_candidate_that_agrees_is_approved(year_dir, tmp_path, accounts, abbreviations):
    assert run(year_dir, tmp_path, accounts, abbreviations, [fee(借方科目="支払手数料")]).approved == 1


def test_two_patterns_with_different_accounts_conflict(year_dir, tmp_path, accounts, abbreviations):
    text = PATTERNS_YAML + "  - 名前: 雑費の手数料\n    入出金: 出金\n    摘要キーワード: テスウ\n    科目: 雑費\n"
    result = run(year_dir, tmp_path, accounts, abbreviations, [fee()], text=text)
    assert (result.approved, result.conflicts) == (0, 1)
    # 要確認理由は追記だけ（相手科目はまだ空なので「相手科目未設定」も残る）
    assert read_rows(year_dir / "staging.csv")[0]["要確認理由"] == "相手科目未設定／確立済みパターンが複数一致（振込手数料・雑費の手数料）"


def test_nothing_to_change_does_not_rewrite_file(year_dir, tmp_path, accounts, abbreviations, monkeypatch):
    import patterns
    calls = []
    monkeypatch.setattr(patterns, "replace_rows", lambda *args: calls.append(args))
    result = run(year_dir, tmp_path, accounts, abbreviations, [fee(摘要="ATM")])
    assert (result.approved, result.conflicts, calls) == (0, 0, [])


def test_auto_approved_rows_post_as_automatic(year_dir, tmp_path, accounts, abbreviations):
    run(year_dir, tmp_path, accounts, abbreviations, [fee()])
    post_approved(year_dir, accounts, now=NOW)
    (posted,) = read_rows(year_dir / "journal.csv")
    assert (posted["借方科目"], posted["登録区分"]) == ("支払手数料", "自動")


def test_template_patterns_example_parses_when_uncommented(accounts, tmp_path):
    from pathlib import Path
    template = Path(__file__).resolve().parents[4] / "テンプレート" / "context" / "company" / "kessan-patterns.yaml"
    assert load_patterns(template, accounts) == []
    lines = template.read_text(encoding="utf-8").split("\n")
    start = lines.index("# patterns:")
    block = []
    for text in lines[start:]:
        if not text.startswith("#"):
            break
        block.append(text[2:])
    uncommented = [t for t in lines[:start] if t != "patterns: []"] + block
    (pattern,) = load_patterns(write_patterns(tmp_path, "\n".join(uncommented)), accounts)
    assert (pattern.name, pattern.direction, pattern.keyword, pattern.subject, pattern.amount_range) == (
        "振込手数料", "出金", "テスウリヨウ", "支払手数料", (0, 1000))


# --- F3：明細の行では、パターンの取引先は摘要とだけ照合する（AIが書いた取引先欄では一致させない） ---

def test_partner_written_by_ai_does_not_trigger_pattern(year_dir, tmp_path, accounts, abbreviations):
    from accounts_update import set_accounts
    write_rows(year_dir / "staging.csv", STAGING_COLUMNS, [deposit(摘要="フリコミ サンプルシヨウテン")])
    set_accounts(year_dir, accounts, {"bank:dep": {"取引先": "株式会社テストシヨウジ"}}, PAYMENT)
    patterns = load_patterns(write_patterns(tmp_path), accounts)
    result = auto_approve(year_dir, accounts, patterns, PAYMENT, abbreviations)
    assert result.approved == 0
    (row,) = read_rows(year_dir / "staging.csv")
    assert (row["貸方科目"], row["承認"]) == ("", "")
