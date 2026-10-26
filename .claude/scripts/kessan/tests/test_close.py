"""決算整理の枠組み（候補・承認・登録）。項目のモジュールは架空のものを差し込む。"""
from types import SimpleNamespace

import pytest
import yaml

import cli
import close
from check import has_ng, run_checks
from common import EVIDENCE_COLUMNS, JOURNAL_COLUMNS, KessanError, read_rows, write_rows
from evidence import STATE_MULTIPLE
from helpers import evidence_row, line

STAGING = "adjustments-staging.csv"


def base_books(year_dir):
    """資本金で預金を作っただけの帳簿（検算NGなし）。"""
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, [
        line("1", "2025-04-01", debit=("普通預金", "", 1000000), credit=("資本金", "", 1000000)),
    ])


def fee(key, amount, reason="テストの理由"):
    """1行の候補：支払手数料／未払金。"""
    return close.Candidate(item="テスト", key=key, date="2026-03-31", reason=reason, lines=[
        {"借方科目": "支払手数料", "借方金額": str(amount), "貸方科目": "未払金", "貸方金額": str(amount), "摘要": f"手数料{key}"},
    ])


def split(key):
    """複数行の候補：租税公課 500 ／ 未払金 300・未払費用 200。"""
    return close.Candidate(item="テスト", key=key, date="2026-03-31", reason="複数行", lines=[
        {"借方科目": "租税公課", "借方金額": "500", "摘要": "印紙"},
        {"貸方科目": "未払金", "貸方金額": "300", "摘要": "印紙"},
        {"貸方科目": "未払費用", "貸方金額": "200", "摘要": "印紙"},
    ])


class FakeItem:
    def __init__(self, candidates, notes=()):
        self.candidates = list(candidates)
        self.notes = list(notes)
        self.posted = []

    def collect(self, ctx):
        assert isinstance(ctx, close.CloseContext)
        return list(self.candidates), list(self.notes)

    def after_post(self, year_dir, posted_source_ids):
        self.posted.append(list(posted_source_ids))


def staging(year_dir):
    return read_rows(year_dir / STAGING)


def test_refuses_when_check_ng(year_dir, accounts):
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, [
        line("1", "2025-04-01", debit=("普通預金", "", 1000), credit=("資本金", "", 900)),
    ])
    with pytest.raises(KessanError, match="NG.*check-result.md"):
        close.run_close(year_dir, accounts, items=[FakeItem([fee("k1", 100)])])
    assert staging(year_dir) == []


def test_refuses_when_evidence_unresolved(year_dir, accounts):
    base_books(year_dir)
    write_rows(year_dir / "evidence.csv", EVIDENCE_COLUMNS, [
        evidence_row(証憑ID="E1", 証憑ファイル="inbox/領収書-1.jpg", 日付="2025-05-01", 金額="1100", 取引先="テスト商店",
                     状態=STATE_MULTIPLE),
    ])
    with pytest.raises(KessanError, match="証憑"):
        close.run_close(year_dir, accounts, items=[FakeItem([fee("k1", 100)])])


def test_candidates_idempotent(year_dir, accounts):
    base_books(year_dir)
    item = FakeItem([fee("k1", 1000), split("k2")])
    r1 = close.run_close(year_dir, accounts, items=[item])
    rows1 = staging(year_dir)
    assert (r1.candidates_added, r1.candidates_kept) == (2, 0)
    assert len(rows1) == 4
    assert {r["伝票番号"] for r in rows1} == {"C1", "C2"}
    assert {r["取り込み元ID"] for r in rows1} == {"close:テスト:k1", "close:テスト:k2"}
    assert all(r["判定"] == "要確認" and r["承認"] == "" for r in rows1)
    assert rows1[0]["要確認理由"] == "テストの理由"
    assert r1.report_path.exists()

    r2 = close.run_close(year_dir, accounts, items=[item])
    assert len(staging(year_dir)) == 4
    assert r2.candidates_added == 2

    # 承認済みの候補は、内容が変わっても作り直さない（所見で知らせる）
    assert close.approve_candidates(year_dir, ["close:テスト:k1"]) == 1
    item.candidates = [fee("k1", 9999), split("k2")]
    r3 = close.run_close(year_dir, accounts, items=[item])
    rows3 = staging(year_dir)
    k1 = [r for r in rows3 if r["取り込み元ID"] == "close:テスト:k1"]
    assert [r["借方金額"] for r in k1] == ["1000"] and k1[0]["承認"] == "済"
    assert (r3.candidates_added, r3.candidates_kept) == (1, 1)
    assert any("close:テスト:k1" in n.message for n in r3.notes)

    # 登録済みの候補は staging に出さない
    close.post_candidates(year_dir, accounts)
    r4 = close.run_close(year_dir, accounts, items=[item])
    ids = {r["取り込み元ID"] for r in staging(year_dir)}
    assert ids == {"close:テスト:k2"}
    assert r4.candidates_kept == 1


def test_stale_unapproved_candidate_removed(year_dir, accounts):
    base_books(year_dir)
    item = FakeItem([fee("k1", 1000), fee("k2", 2000), fee("k3", 3000)])
    close.run_close(year_dir, accounts, items=[item])
    close.approve_candidates(year_dir, ["close:テスト:k3"])
    item.candidates = [fee("k1", 1000)]
    close.run_close(year_dir, accounts, items=[item])
    ids = [r["取り込み元ID"] for r in staging(year_dir)]
    assert "close:テスト:k2" not in ids
    assert "close:テスト:k3" in ids  # 承認済みは消さないが、承認を外す
    numbers = [r["伝票番号"] for r in staging(year_dir)]
    assert len(set(numbers)) == len(numbers)


def test_invalid_candidate_refused(year_dir, accounts):
    base_books(year_dir)
    bad = close.Candidate(item="テスト", key="x", date="2026-03-31", reason="", lines=[
        {"借方科目": "支払手数料", "借方金額": "100", "貸方科目": "未払金", "貸方金額": "90"},
    ])
    with pytest.raises(KessanError, match="借方100≠貸方90"):
        close.run_close(year_dir, accounts, items=[FakeItem([bad])])
    assert staging(year_dir) == []


def test_post_assigns_A_numbers(year_dir, accounts):
    base_books(year_dir)
    write_rows(year_dir / "adjustments.csv", JOURNAL_COLUMNS, [
        line("A1", "2026-03-31", debit=("支払手数料", "", 100), credit=("未払金", "", 100), 登録区分="決算整理",
             取り込み元ID="manual:a1"),
    ])
    item = FakeItem([fee("k1", 1000), split("k2")])
    close.run_close(year_dir, accounts, items=[item])
    assert close.approve_candidates(year_dir, ["close:テスト:k1", "close:テスト:k2"]) == 4
    numbers = close.post_candidates(year_dir, accounts, items=[item])
    assert numbers == ["A2", "A3"]
    adjustments = read_rows(year_dir / "adjustments.csv")
    assert [r["伝票番号"] for r in adjustments] == ["A1", "A2", "A3", "A3", "A3"]
    assert all(r["登録区分"] == "決算整理" and r["登録日時"] for r in adjustments[1:])
    assert adjustments[1]["取り込み元ID"] == "close:テスト:k1"
    assert staging(year_dir) == []
    assert not has_ng(run_checks(year_dir, accounts))
    assert item.posted == [["close:テスト:k1", "close:テスト:k2"]]


def test_post_refuses_when_check_ng(year_dir, accounts):
    base_books(year_dir)
    close.run_close(year_dir, accounts, items=[FakeItem([fee("k1", 1000)])])
    close.approve_candidates(year_dir, ["close:テスト:k1"])
    rows = read_rows(year_dir / "journal.csv")
    rows[0]["貸方金額"] = "1"
    write_rows(year_dir / "journal.csv", JOURNAL_COLUMNS, rows)
    with pytest.raises(KessanError, match="NG"):
        close.post_candidates(year_dir, accounts)
    assert read_rows(year_dir / "adjustments.csv") == []


def test_approve_unknown_id_refused(year_dir, accounts):
    base_books(year_dir)
    close.run_close(year_dir, accounts, items=[FakeItem([fee("k1", 1000)])])
    with pytest.raises(KessanError, match="close:テスト:zz"):
        close.approve_candidates(year_dir, ["close:テスト:k1", "close:テスト:zz"])
    assert all(r["承認"] == "" for r in staging(year_dir))


def test_set_amount_validates(year_dir):
    with pytest.raises(KessanError, match="消費税"):
        close.set_amount(year_dir, "住民税", 100)
    with pytest.raises(KessanError, match="0以上"):
        close.set_amount(year_dir, "消費税", -1)
    with pytest.raises(KessanError, match="0以上"):
        close.set_amount(year_dir, "消費税", True)
    close.set_amount(year_dir, "消費税", 12000)
    close.set_amount(year_dir, "法人税等", 70000)
    data = yaml.safe_load((year_dir / "close-overrides.yaml").read_text(encoding="utf-8"))
    assert data == {"消費税": 12000, "法人税等": 70000}
    assert close.load_overrides(year_dir) == {"消費税": 12000, "法人税等": 70000}


def test_report_lists_candidates_and_notes(year_dir, accounts):
    base_books(year_dir)
    item = FakeItem([split("k2")], [close.Note("テスト", "確かめてほしいこと")])
    r = close.run_close(year_dir, accounts, items=[item])
    text = r.report_path.read_text(encoding="utf-8")
    assert "## テスト" in text
    assert "| 日付 | 借方 | 貸方 | 金額 | 要確認理由 |" in text
    assert "未払費用" in text and "- 確かめてほしいこと" in text
    assert "---" not in text.replace("|---", "")


def test_cli_close_flow(year_dir, capsys):
    base_books(year_dir)
    assert cli.main(["close-set-amount", "--year-dir", str(year_dir), "--item", "法人税等", "--amount", "50000"]) == 0
    assert cli.main(["close-candidates", "--year-dir", str(year_dir)]) == 0
    ids = {r["取り込み元ID"] for r in staging(year_dir)}
    assert ids == {"close:法人税等:2026-03-31"}
    assert cli.main(["close-approve", "--year-dir", str(year_dir), "--all"]) == 0
    assert cli.main(["close-post", "--year-dir", str(year_dir)]) == 0
    adjustments = read_rows(year_dir / "adjustments.csv")
    assert [(r["伝票番号"], r["借方科目"] or r["貸方科目"]) for r in adjustments] == [("A1", "法人税等")]
    assert cli.main(["close-set-amount", "--year-dir", str(year_dir), "--item", "住民税", "--amount", "1"]) == 1
    assert cli.main(["close-set-amount", "--year-dir", str(year_dir), "--item", "消費税", "--amount", "-5"]) == 1


def test_stale_approved_candidate_unapproved(year_dir, accounts):
    base_books(year_dir)
    item = FakeItem([fee("k1", 1000), fee("k2", 2000)])
    close.run_close(year_dir, accounts, items=[item])
    close.approve_candidates(year_dir, ["close:テスト:k1", "close:テスト:k2"])
    item.candidates = [fee("k1", 1000)]
    r = close.run_close(year_dir, accounts, items=[item])
    k2 = [x for x in staging(year_dir) if x["取り込み元ID"] == "close:テスト:k2"]
    assert len(k2) == 1 and k2[0]["承認"] == "" and k2[0]["判定"] == "要確認"
    assert "今回の再計算では出ていない（不要なら削除、必要なら承認し直す）" in k2[0]["要確認理由"]
    assert any("close:テスト:k2" in n.message for n in r.notes)
    assert "close:テスト:k2" in r.report_path.read_text(encoding="utf-8")
    assert close.post_candidates(year_dir, accounts, items=[item]) == ["A1"]  # k2 は承認し直すまで登録しない
    assert [x["取り込み元ID"] for x in staging(year_dir)] == ["close:テスト:k2"]


class BrokenHook(FakeItem):
    def __init__(self):
        super().__init__([])
        self.broken = True

    def after_post(self, year_dir, posted_source_ids):
        if self.broken:
            raise RuntimeError("後処理の失敗（テスト）")
        super().after_post(year_dir, posted_source_ids)


def test_after_post_failure_does_not_block_others_and_is_repaired(year_dir, accounts):
    base_books(year_dir)
    broken = BrokenHook()
    item = FakeItem([fee("k1", 1000)])
    items = [broken, item]
    close.run_close(year_dir, accounts, items=items)
    close.approve_candidates(year_dir, ["close:テスト:k1"])
    with pytest.raises(KessanError, match="登録は完了済み.*\n.*後処理の失敗"):
        close.post_candidates(year_dir, accounts, items=items)
    assert [r["伝票番号"] for r in read_rows(year_dir / "adjustments.csv")] == ["A1"]
    assert staging(year_dir) == []
    assert item.posted == [["close:テスト:k1"]]  # 失敗した後処理があっても他は呼ぶ
    broken.broken = False
    close.run_close(year_dir, accounts, items=items)  # 次の close-candidates で後処理をやり直す
    assert broken.posted == [["close:テスト:k1"]]


def test_after_post_runs_even_if_staging_write_fails(year_dir, accounts, monkeypatch):
    base_books(year_dir)
    item = FakeItem([fee("k1", 1000)])
    close.run_close(year_dir, accounts, items=[item])
    close.approve_candidates(year_dir, ["close:テスト:k1"])
    original = close.replace_rows

    def fail_staging(path, columns, rows):
        if path.name == STAGING:
            raise OSError("locked")
        return original(path, columns, rows)

    monkeypatch.setattr(close, "replace_rows", fail_staging)
    with pytest.raises(KessanError, match="登録は完了済み"):
        close.post_candidates(year_dir, accounts, items=[item])
    assert item.posted == [["close:テスト:k1"]]
    monkeypatch.setattr(close, "replace_rows", original)
    close.run_close(year_dir, accounts, items=[item])
    assert staging(year_dir) == []  # 登録済みの候補は staging から消える


def test_reject_drops_candidate_and_reports_reason(year_dir, accounts):
    base_books(year_dir)
    item = FakeItem([fee("k1", 1000), fee("k2", 2000)])
    close.run_close(year_dir, accounts, items=[item])
    assert {r["取り込み元ID"] for r in staging(year_dir)} == {"close:テスト:k1", "close:テスト:k2"}
    close.approve_candidates(year_dir, ["close:テスト:k1"])
    close.reject_candidates(year_dir, ["close:テスト:k1"], "二重のため")  # 承認済みでも staging から消える
    assert {r["取り込み元ID"] for r in staging(year_dir)} == {"close:テスト:k2"}
    r = close.run_close(year_dir, accounts, items=[item])
    assert r.candidates_rejected == 1
    assert {x["取り込み元ID"] for x in staging(year_dir)} == {"close:テスト:k2"}
    text = r.report_path.read_text(encoding="utf-8")
    assert "却下した候補" in text and "close:テスト:k1：二重のため" in text
    assert close.load_rejected(year_dir) == {"close:テスト:k1": "二重のため"}


def test_reject_keeps_amounts_and_validates(year_dir, accounts):
    base_books(year_dir)
    close.run_close(year_dir, accounts, items=[FakeItem([fee("k1", 1000)])])
    close.set_amount(year_dir, "消費税", 100)
    close.reject_candidates(year_dir, ["close:テスト:k1"], "不要")
    close.set_amount(year_dir, "法人税等", 200)
    assert close.load_overrides(year_dir) == {"消費税": 100, "法人税等": 200}
    assert close.load_rejected(year_dir) == {"close:テスト:k1": "不要"}
    with pytest.raises(KessanError, match="理由"):
        close.reject_candidates(year_dir, ["close:テスト:k2"], " ")
    with pytest.raises(KessanError, match="close:"):
        close.reject_candidates(year_dir, ["bank:x"], "理由")
    item = FakeItem([fee("k1", 1000)])
    close.run_close(year_dir, accounts, items=[item])
    assert staging(year_dir) == []


def test_reject_posted_candidate_is_error(year_dir, accounts):
    base_books(year_dir)
    item = FakeItem([fee("k1", 1000)])
    close.run_close(year_dir, accounts, items=[item])
    close.approve_candidates(year_dir, ["close:テスト:k1"])
    close.post_candidates(year_dir, accounts, items=[item])
    with pytest.raises(KessanError, match="登録済み"):
        close.reject_candidates(year_dir, ["close:テスト:k1"], "やはり不要")
    assert close.load_rejected(year_dir) == {}


def test_cli_close_reject(year_dir, capsys):
    base_books(year_dir)
    assert cli.main(["close-set-amount", "--year-dir", str(year_dir), "--item", "法人税等", "--amount", "50000"]) == 0
    assert cli.main(["close-candidates", "--year-dir", str(year_dir)]) == 0
    ids = "close:法人税等:2026-03-31"
    assert cli.main(["close-reject", "--year-dir", str(year_dir), "--ids", ids, "--reason", "別途計上"]) == 0
    assert staging(year_dir) == []
    assert cli.main(["close-candidates", "--year-dir", str(year_dir)]) == 0
    assert staging(year_dir) == []
    assert cli.main(["close-reject", "--year-dir", str(year_dir), "--ids", ids, "--reason", " "]) == 1


def test_tax_candidate_posts_with_only_corporate_tax_left(year_dir, accounts):
    """法人税等の候補は消費税の額を変えないので、未承認のまま消費税だけ登録できる。他の項目が残っていれば拒否のまま。"""
    base_books(year_dir)
    tax = close.Candidate(item="消費税", key="2026-03-31", date="2026-03-31", reason="t", lines=[
        {"借方科目": "租税公課", "借方金額": "100", "貸方科目": "未払消費税等", "貸方金額": "100"}])
    corp = close.Candidate(item="法人税等", key="2026-03-31", date="2026-03-31", reason="c", lines=[
        {"借方科目": "法人税等", "借方金額": "200", "貸方科目": "未払法人税等", "貸方金額": "200"}])
    items = [FakeItem([tax, corp])]
    close.run_close(year_dir, accounts, items=items)
    close.approve_candidates(year_dir, ["close:消費税:2026-03-31"])
    assert close.post_candidates(year_dir, accounts, items=items) == ["A1"]
    assert {r["取り込み元ID"] for r in staging(year_dir)} == {"close:法人税等:2026-03-31"}
    # 他の項目（テスト）が未承認で残っていれば、消費税は登録できない
    other = [FakeItem([tax, corp, fee("k1", 1000)])]
    year2 = year_dir.parent / "2026-03期b"
    from common import init_year_dir
    init_year_dir(year2, "2025-04-01", "2026-03-31")
    base_books(year2)
    close.run_close(year2, accounts, items=other)
    close.approve_candidates(year2, ["close:消費税:2026-03-31"])
    with pytest.raises(KessanError, match="消費税の候補"):
        close.post_candidates(year2, accounts, items=other)


def test_reject_unknown_id_is_error(year_dir, accounts):
    base_books(year_dir)
    close.run_close(year_dir, accounts, items=[FakeItem([fee("k1", 1000)])])
    with pytest.raises(KessanError, match="候補にないID.*k9"):
        close.reject_candidates(year_dir, ["close:テスト:k1", "close:テスト:k9"], "誤字")
    assert close.load_rejected(year_dir) == {}
    assert [r["取り込み元ID"] for r in staging(year_dir)] == ["close:テスト:k1"]


def test_reject_other_candidate_unapproves_tax(year_dir, accounts):
    base_books(year_dir)
    tax = close.Candidate(item="消費税", key="2026-03-31", date="2026-03-31", reason="t", lines=[
        {"借方科目": "租税公課", "借方金額": "100", "貸方科目": "未払消費税等", "貸方金額": "100"}])
    corp = close.Candidate(item="法人税等", key="2026-03-31", date="2026-03-31", reason="c", lines=[
        {"借方科目": "法人税等", "借方金額": "200", "貸方科目": "未払法人税等", "貸方金額": "200"}])
    items = [FakeItem([fee("k1", 1000), tax, corp])]
    close.run_close(year_dir, accounts, items=items)
    close.approve_candidates(year_dir, ["close:消費税:2026-03-31"])
    close.reject_candidates(year_dir, ["close:法人税等:2026-03-31"], "別途")  # 法人税等は消費税に影響しない
    assert [r["承認"] for r in staging(year_dir) if r["取り込み元ID"].startswith("close:消費税")] == ["済"]
    close.reject_candidates(year_dir, ["close:テスト:k1"], "不要")
    (r,) = staging(year_dir)
    assert (r["取り込み元ID"], r["承認"], r["判定"]) == ("close:消費税:2026-03-31", "", "要確認")
    assert "他の候補の却下で額が変わる" in r["要確認理由"]
