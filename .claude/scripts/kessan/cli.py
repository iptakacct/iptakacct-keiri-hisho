"""kessan：会計システムなしで帳簿を作るためのコマンド（段階1：取り込み・登録・検算・試算表／段階2：資料の読み取り・確認・承認）。

インスタンスフォルダで実行する例（<Y> は work/kessan/2026-03期 など）:
  python ../.claude/scripts/kessan/cli.py init --year-dir <Y> --start 2025-04-01 --end 2026-03-31
  python ../.claude/scripts/kessan/cli.py inbox-status --year-dir <Y>
  python ../.claude/scripts/kessan/cli.py import-bank --year-dir <Y> --account-id main --file <Y>/inbox/2025-04.csv
  python ../.claude/scripts/kessan/cli.py import-extracted --year-dir <Y> [--file <Y>/extracted/通帳-2025-04.pdf.json]
  python ../.claude/scripts/kessan/cli.py set-accounts --year-dir <Y> --file <Y>/output/set-accounts-20260917.json
  python ../.claude/scripts/kessan/cli.py review --year-dir <Y>
  python ../.claude/scripts/kessan/cli.py apply-review --year-dir <Y> --file <Y>/output/review-20260917.xlsx
  python ../.claude/scripts/kessan/cli.py approve --year-dir <Y> --review-file <Y>/output/review-20260917.xlsx --numbers 3 5
  python ../.claude/scripts/kessan/cli.py approve --year-dir <Y> --ids bank:… receipt:…
  python ../.claude/scripts/kessan/cli.py unimport --year-dir <Y> --source inbox/通帳-2025-04.pdf
  python ../.claude/scripts/kessan/cli.py discard --year-dir <Y> --ids bank:… --reason "二重取り込み（オーナー確認済み）"
  python ../.claude/scripts/kessan/cli.py post --year-dir <Y>
  python ../.claude/scripts/kessan/cli.py check --year-dir <Y> [--prev-year-dir work/kessan/2025-03期]
  python ../.claude/scripts/kessan/cli.py tb --year-dir <Y>

終了コード: 0 正常 / 1 エラー（何も書いていない） / 2 コマンドの使い方の誤り（argparse）
          / 3 post で登録は完了したが検算NG / 4 import-extracted で形式エラーのファイルがあった（他のファイルは取り込み済み）
"""
import argparse
import sys
from pathlib import Path

from accounts import load_accounts
from accounts_update import apply_review, approve, approve_numbers, load_updates, set_accounts
from bank_import import import_bank, load_sources
from check import has_ng, run_checks, write_report
from common import KessanError, init_year_dir
from extracted import extracted_files
from extracted_import import import_extracted, inbox_status
from match import load_abbreviations, payment_accounts
from patterns import auto_approve, load_patterns
from post import post_approved
from review_xlsx import read_review, write_review
from trial_balance import write_trial_balance
from undo import discard, unimport

COMMANDS = ("init", "inbox-status", "import-bank", "import-extracted", "set-accounts", "review", "apply-review",
            "approve", "unimport", "discard", "post", "check", "tb")
EXIT_POSTED_WITH_NG = 3
EXIT_SOME_FILES_REJECTED = 4


def _parser():
    parser = argparse.ArgumentParser(prog="kessan")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        p = sub.add_parser(name)
        p.add_argument("--year-dir", required=True, type=Path)
        if name == "init":
            p.add_argument("--start", required=True, help="期首日 YYYY-MM-DD")
            p.add_argument("--end", required=True, help="期末日 YYYY-MM-DD")
        if name in ("import-bank", "import-extracted", "set-accounts"):
            p.add_argument("--sources", type=Path, help="省略時はインスタンスの context/company/kessan-sources.yaml")
            p.add_argument("--patterns", type=Path, help="省略時はインスタンスの context/company/kessan-patterns.yaml")
        if name == "import-bank":
            p.add_argument("--account-id", required=True)
            p.add_argument("--file", required=True, type=Path)
        if name == "import-extracted":
            p.add_argument("--file", type=Path, nargs="+", help="省略時は extracted/*.json をすべて")
        if name in ("set-accounts", "apply-review"):
            p.add_argument("--file", required=True, type=Path)
        if name == "approve":
            target = p.add_mutually_exclusive_group(required=True)
            target.add_argument("--ids", nargs="+", help="承認する取り込み元ID")
            target.add_argument("--review-file", type=Path, help="オーナーが番号で承認した確認用Excel（--numbers と使う）")
            p.add_argument("--numbers", nargs="+", help="確認用Excelの番号（--review-file と使う）")
        if name == "unimport":
            p.add_argument("--source", required=True, help="取り消す資料（inbox/…。銀行CSVはファイル名でも可）")
        if name == "discard":
            p.add_argument("--ids", required=True, nargs="+", help="破棄する取り込み元ID")
            p.add_argument("--reason", required=True, help="破棄の理由（discard-log.csv に記録）")
        if name in ("post", "check"):
            p.add_argument("--prev-year-dir", type=Path)
    return parser


def _company_file(year_dir, given, name):
    return given or year_dir.resolve().parents[2] / "context" / "company" / name


def _run_check(year_dir, accounts, prev_year_dir):
    findings = run_checks(year_dir, accounts, prev_year_dir)
    path = write_report(year_dir, findings)
    ng = sum(f.level == "NG" for f in findings)
    print(f"検算: NG {ng}件 / 警告 {len(findings) - ng}件（{path}）")
    return findings


def _auto_approve(year_dir, accounts, args):
    """確立済みパターンに一致した明細行を自動承認する（import-bank・import-extracted・set-accounts の後）。"""
    patterns = load_patterns(_company_file(year_dir, args.patterns, "kessan-patterns.yaml"), accounts)
    if not patterns:
        return
    sources = load_sources(_company_file(year_dir, args.sources, "kessan-sources.yaml"))
    r = auto_approve(year_dir, accounts, patterns, payment_accounts(sources), load_abbreviations())
    print(f"確立済みパターン: 自動承認 {r.approved}件 / パターンと科目候補が合わず要確認 {r.conflicts}件")


def _print_extracted_result(r):
    print(f"読み取り結果の取り込み: ファイル {len(r.imported)}件 / 追加 {r.added}件"
          f" / 取り込み済みの明細のためスキップ {r.duplicates}件 / 金額0のためスキップ {r.zero_amount}件")
    if r.evidence or r.receipt_duplicates:
        states = "、".join(f"{k} {v}件" for k, v in sorted(r.evidence.items()))
        print(f"証憑: {states or 'なし'} / 証憑の重複の疑い {len(r.receipt_duplicates)}件")
    if r.receipt_duplicates:
        print("重複の疑いがある証憑（取り込みは止めていません）: "
              + "、".join(f"{name}（既存の証憑ID {existing_id}）" for name, existing_id in r.receipt_duplicates))
    if r.receipt_same_date_amount:
        print("日付・金額が同じ既存の証憑がある証憑（証憑の重複の疑い。要確認理由を書ける行が無いため、ここで確認）: "
              + "、".join(f"{name}（既存の証憑ID {ids}）" for name, ids in r.receipt_same_date_amount))
    if r.resumed:
        print("前回 import-log.csv の書き込みに失敗した後の再実行で、記録だけ埋めた資料: " + "、".join(r.resumed))
    if r.already_imported:
        print("資料が取り込み済みのため件数0: " + "、".join(r.already_imported))
    if r.low_confidence_pages:
        print("警告: 残高が連続しないページ（読み取り信頼度=低で取り込み）: " + "、".join(r.low_confidence_pages))
    if r.overlapping_imports:
        print("警告: 同じ口座で期間が重なる取り込み済みファイルがあります: " + "、".join(r.overlapping_imports))
    if r.unreadable:
        print("読めなかった資料: " + "、".join(r.unreadable))
    for name, errors in r.rejected.items():
        print(f"形式エラーのため取り込んでいません: {name}")
        for e in errors:
            print(f"  - {e}")


def main(argv=None):
    args = _parser().parse_args(argv)
    year_dir = args.year_dir
    try:
        if args.command == "init":
            init_year_dir(year_dir, args.start, args.end)
            print(f"年度フォルダを用意しました: {year_dir}")
            return 0
        if not (year_dir / "journal.csv").exists():
            raise KessanError(f"年度フォルダが初期化されていません（先に init を実行）: {year_dir}")
        accounts = load_accounts()

        if args.command == "inbox-status":
            statuses = inbox_status(year_dir)
            for path, status in statuses:
                print(f"{status}\t{path}")
            pending = sum(status in ("未処理", "読み取り済み・未取り込み") for _, status in statuses)
            print(f"資料 {len(statuses)}件 / 未処理・未取り込み {pending}件")
            return 0
        if args.command == "import-bank":
            sources = _company_file(year_dir, args.sources, "kessan-sources.yaml")
            r = import_bank(year_dir, sources, args.account_id, args.file)
            print(f"取り込み: 追加 {r.added}件 / 取り込み済みのためスキップ {r.duplicates}件 / 金額0のためスキップ {r.zero_amount}件"
                  f" / 期間外のためスキップ {r.out_of_period}件")
            if r.overlapping_imports:
                print("警告: 同じ口座で期間が重なる取り込み済みファイルがあります: "
                      + "、".join(r.overlapping_imports)
                      + "（別名で保存し直した同じ明細でないか、staging.csv の要確認理由を確認）")
            _auto_approve(year_dir, accounts, args)
            return 0
        if args.command == "import-extracted":
            sources = _company_file(year_dir, args.sources, "kessan-sources.yaml")
            r = import_extracted(year_dir, sources, accounts, args.file or extracted_files(year_dir))
            _print_extracted_result(r)
            _auto_approve(year_dir, accounts, args)
            return EXIT_SOME_FILES_REJECTED if r.rejected else 0
        if args.command == "set-accounts":
            sources = load_sources(_company_file(year_dir, args.sources, "kessan-sources.yaml"))
            count = set_accounts(year_dir, accounts, load_updates(args.file), payment_accounts(sources))
            print(f"科目候補: {count}件を反映しました")
            _auto_approve(year_dir, accounts, args)
            return 0
        if args.command == "review":
            s = write_review(year_dir)
            print(f"確認用Excel: {s.path}")
            print(f"行 {s.rows}件（要確認 {s.needs_review}件 / 承認済み {s.approved}件）"
                  f" / 読み取り信頼度が低い {s.low_confidence}件 / 明細に該当なし {s.no_match}件")
            print(f"突き合わせ先が複数 {s.multiple}件 / 未払候補 {s.unpaid}件 / 読めなかった資料 {s.unreadable}件")
            if s.without_id:
                print(f"取り込み元IDの無い行 {s.without_id}件は Excel に載せていません（staging.csv で確認）")
            return 0
        if args.command == "apply-review":
            r = apply_review(year_dir, accounts, read_review(args.file))
            print(f"承認を反映: {r.approved}件 / 修正メモのため承認を外した行 {r.unapproved}件")
            for source_id, memo in r.memos:
                print(f"修正メモ: {source_id}\t{memo}")
            if r.changed:
                print("Excel出力後に内容が変わったため承認していない行：" + "、".join(r.changed)
                      + "（review を出し直して確認）")
            if r.missing:
                print("staging.csv に無い取り込み元ID（登録済み・削除済み）: " + "、".join(r.missing))
            return 0
        if args.command == "approve":
            if args.ids:
                if args.numbers:
                    raise KessanError("--numbers は --review-file と一緒に使います（--ids とは一緒に使えません）")
                print(f"承認: {approve(year_dir, accounts, args.ids)}件")
                return 0
            if not args.numbers:
                raise KessanError("--review-file には、承認する番号を --numbers で指定してください")
            r = approve_numbers(year_dir, accounts, read_review(args.review_file), args.numbers)
            print(f"承認: {r.approved}件")
            if r.changed:
                print("Excel出力後に内容が変わったため承認していない行："
                      + "、".join(f"番号 {n}（{source_id}）" for n, source_id in r.changed) + "（review を出し直して確認）")
            if r.missing:
                print("staging.csv に無い行（登録済み・取り消し済み）："
                      + "、".join(f"番号 {n}（{source_id}）" for n, source_id in r.missing))
            return 0
        if args.command == "unimport":
            r = unimport(year_dir, args.source)
            print(f"取り込みを取り消しました: {'、'.join(r.sources)} / staging.csv {r.staging}行"
                  f" / 残高の記録 {r.balances}行 / 証憑 {r.evidence}件 / 取り込み記録 {r.import_log}件")
            if r.removed_ids:
                print("staging.csv から消した行: " + "、".join(r.removed_ids))
            if r.restored:
                print("証憑の科目候補を外して元に戻した明細行: " + "、".join(r.restored))
            print("読み取り結果を直してから import-extracted で取り込み直せます")
            return 0
        if args.command == "discard":
            count = discard(year_dir, args.ids, args.reason)
            print(f"破棄: {count}行（discard-log.csv に記録）")
            return 0
        if args.command == "post":
            r = post_approved(year_dir, accounts)
            print(f"登録: 伝票 {len(r.vouchers)}件（{', '.join(r.vouchers) or 'なし'}） / 未承認で残った行 {r.remaining}件")
            findings = _run_check(year_dir, accounts, args.prev_year_dir)
            if has_ng(findings):
                print("登録は完了済みですが、検算でNGがあります（check-result.md を確認）")
                return EXIT_POSTED_WITH_NG  # 1（エラーで何も登録していない）・2（コマンドの使い方の誤り。argparse）と区別する
            return 0
        if args.command == "check":
            return 1 if has_ng(_run_check(year_dir, accounts, args.prev_year_dir)) else 0
        if args.command == "tb":
            print(f"試算表: {write_trial_balance(year_dir, accounts)}")
            return 0
    except KessanError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
