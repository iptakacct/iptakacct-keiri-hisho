"""kessan：会計システムなしで帳簿を作るためのコマンド（段階1：取り込み・登録・検算・試算表／段階2：資料の読み取り・確認・承認）。

インスタンスフォルダで実行する例（<Y> は work/kessan/2026-03期 など）:
  python ../.claude/scripts/kessan/cli.py init --year-dir <Y> --start 2025-04-01 --end 2026-03-31
  python ../.claude/scripts/kessan/cli.py init --year-dir <Y> --start 2025-04-01 --end 2026-03-31 --company-name テスト商事株式会社   （既存の年度フォルダに会社名だけ足せる）
  python ../.claude/scripts/kessan/cli.py inbox-status --year-dir <Y>
  python ../.claude/scripts/kessan/cli.py import-bank --year-dir <Y> --account-id main --file <Y>/inbox/2025-04.csv
  python ../.claude/scripts/kessan/cli.py import-extracted --year-dir <Y> [--file <Y>/extracted/通帳-2025-04.pdf.json]
  python ../.claude/scripts/kessan/cli.py set-accounts --year-dir <Y> --file <Y>/output/set-accounts-20260917.json
  python ../.claude/scripts/kessan/cli.py set-tax --year-dir <Y> --file <Y>/output/set-tax-20260917.json [--prev-year-dir …]
  python ../.claude/scripts/kessan/cli.py split-tax --year-dir <Y> --id receipt:… --part 10:1080 --part 8:540
  python ../.claude/scripts/kessan/cli.py split-tax --year-dir <Y> --voucher 3 --line 1 --part 10:1080 --part 8:540   （登録済み伝票）
  python ../.claude/scripts/kessan/cli.py resolve-evidence --year-dir <Y> --evidence-id <証憑ID> (--match <取り込み元ID> | --as-payable | --as-new)
  python ../.claude/scripts/kessan/cli.py review --year-dir <Y>
  python ../.claude/scripts/kessan/cli.py apply-review --year-dir <Y> --file <Y>/output/review-20260917.xlsx
  python ../.claude/scripts/kessan/cli.py approve --year-dir <Y> --review-file <Y>/output/review-20260917.xlsx --numbers 3 5
  python ../.claude/scripts/kessan/cli.py approve --year-dir <Y> --ids bank:… receipt:…
  python ../.claude/scripts/kessan/cli.py unimport --year-dir <Y> --source inbox/通帳-2025-04.pdf [inbox/2025-04.csv]
  python ../.claude/scripts/kessan/cli.py discard --year-dir <Y> --ids bank:… --reason "二重取り込み（オーナー確認済み）"
  python ../.claude/scripts/kessan/cli.py post --year-dir <Y>
  python ../.claude/scripts/kessan/cli.py check --year-dir <Y> [--prev-year-dir work/kessan/2025-03期]
  python ../.claude/scripts/kessan/cli.py tb --year-dir <Y>
  python ../.claude/scripts/kessan/cli.py tax-summary --year-dir <Y> [--prev-year-dir work/kessan/2025-03期]
  python ../.claude/scripts/kessan/cli.py close-set-amount --year-dir <Y> --item 消費税|法人税等 --amount 123400
  python ../.claude/scripts/kessan/cli.py close-candidates --year-dir <Y> [--next-year-dir work/kessan/2027-03期] [--prev-year-dir …]
  python ../.claude/scripts/kessan/cli.py close-approve --year-dir <Y> (--ids close:消費税:2026-03-31 … | --all)
  python ../.claude/scripts/kessan/cli.py close-unapprove --year-dir <Y> --ids close:…   （承認を外す）
  python ../.claude/scripts/kessan/cli.py close-reject --year-dir <Y> --ids close:未払:… --reason "不要な理由"
  python ../.claude/scripts/kessan/cli.py close-unreject --year-dir <Y> --ids close:未払:…   （却下を取り消す）
  python ../.claude/scripts/kessan/cli.py close-post --year-dir <Y> [--prev-year-dir …]
  python ../.claude/scripts/kessan/cli.py close-unpost --year-dir <Y> --ids close:…   （登録済みの決算整理仕訳を取り消す）
  python ../.claude/scripts/kessan/cli.py statements --year-dir <Y> [--prev-year-dir …]   （決算書・個別注記表の雛形・内訳書と概況書の材料）
  python ../.claude/scripts/kessan/cli.py export-tatsujin --year-dir <Y> [--kind kessan uchiwake gaikyo shohizei] [--prev-year-dir …]   （達人へ渡すXML。shohizei は原則課税の年度だけ）

終了コード: 0 正常 / 1 エラー（何も書いていない） / 2 コマンドの使い方の誤り（argparse）
          / 3 post（set-tax・登録済み伝票の split-tax・close-post・close-unpost）で登録（修正）は完了したが検算NG / 4 import-extracted で形式エラーのファイルがあった（他のファイルは取り込み済み）
          （import-bank・import-extracted・set-accounts の反映後に自動承認だけが失敗したときは、反映が完了した旨を出して 1。
            import-extracted で形式エラーのファイルもあったときは 4）
"""
import argparse
import sys
from pathlib import Path

import gaikyo
import uchiwake
from accounts import load_accounts
from accounts_update import apply_review, approve, approve_numbers, load_updates, set_accounts
from bank_import import import_bank, load_sources
from check import has_ng, run_checks, write_report
from close import (
    approve_all_ids, approve_candidates, post_candidates, reject_candidates, run_close, set_amount, unapprove_candidates,
    unpost_candidates, unreject_candidates,
)
from common import PERIOD_FILE, YEAR_FILES, KessanError, cannot_write, init_year_dir, upgrade_header
from evidence_resolve import resolve_evidence
from extracted import extracted_files
from extracted_import import import_extracted, inbox_status
from financials import build_statements, write_statements
from match import add_payee, load_abbreviations, load_payees, payment_accounts
from patterns import auto_approve, load_patterns
from post import post_approved
from review_xlsx import read_review, write_review
from tatsujin_xml import export as export_tatsujin
from tax import load_rates, load_tax_settings, load_transitions
from tax_edit import load_tax_updates, set_journal_tax, split_posted, split_tax
from tax_fill import fill_tax
from tax_summary import summarize, write_summary
from trial_balance import write_trial_balance
from undo import discard, unimport

COMMANDS = ("init", "add-payee", "inbox-status", "import-bank", "import-extracted", "set-accounts", "set-tax", "split-tax", "resolve-evidence", "review", "apply-review",
            "approve", "unimport", "discard", "post", "check", "tb", "tax-summary",
            "close-candidates", "close-approve", "close-unapprove", "close-post", "close-set-amount", "close-reject",
            "close-unreject", "close-unpost", "statements", "export-tatsujin")
# 達人XMLの種類（決算書・内訳書・概況書・消費税）。未実装の種類は export が「未対応」で止める
TATSUJIN_KINDS = ("kessan", "uchiwake", "gaikyo", "shohizei")
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
            p.add_argument("--company-name", help="会社名（決算書・達人XMLに載る。省略時は period.yaml を変えない）")
            p.add_argument("--consumption-tax", choices=["免税", "課税"], help="消費税の区分（省略時は period.yaml を変えない）")
            p.add_argument("--tax-method", choices=["原則", "簡易", "2割特例"])
            p.add_argument("--tax-calc", choices=["割戻し", "積上げ"])
            p.add_argument("--tax-allocation", choices=["全額控除", "個別対応", "一括比例配分"], help="方式=原則のとき必須")
            p.add_argument("--small-exemption", action="store_true", help="少額特例（1万円未満の課税仕入）を使う会社")
        if name in ("import-bank", "import-extracted", "set-accounts", "split-tax", "resolve-evidence"):
            p.add_argument("--sources", type=Path, help="省略時はインスタンスの context/company/kessan-sources.yaml")
            p.add_argument("--patterns", type=Path, help="省略時はインスタンスの context/company/kessan-patterns.yaml")
        if name == "add-payee":
            p.add_argument("--name", required=True, help="取引先（領収書・請求書の名前）")
            p.add_argument("--kana", required=True, help="通帳・カードの摘要に出る名義")
            p.add_argument("--payees", type=Path, help="省略時はインスタンスの context/company/kessan-payees.yaml")
        if name == "import-bank":
            p.add_argument("--account-id", required=True)
            p.add_argument("--file", required=True, type=Path)
        if name == "import-extracted":
            p.add_argument("--file", type=Path, nargs="+", help="省略時は extracted/*.json をすべて")
        if name in ("set-accounts", "set-tax", "apply-review"):
            p.add_argument("--file", required=True, type=Path)
        if name == "split-tax":
            p.add_argument("--id", help="分ける未承認の明細（staging.csv）の取り込み元ID")
            p.add_argument("--voucher", help="分ける登録済み伝票（journal.csv）の伝票番号（--line と使う。--id とは同時に使えない）")
            p.add_argument("--line", type=int, help="--voucher の伝票内の行番号（1から）")
            p.add_argument("--prev-year-dir", type=Path)
            p.add_argument("--part", required=True, action="append", metavar="税率:税込金額",
                           help="税率ごとの金額（例：--part 10:1080 --part 8:540。2つ以上）")
        if name == "resolve-evidence":
            p.add_argument("--evidence-id", required=True, help="証憑ID（evidence.csv）")
            how = p.add_mutually_exclusive_group(required=True)
            how.add_argument("--match", help="複数候補のうち、この取り込み元IDの明細行に決める")
            how.add_argument("--as-payable", action="store_true", help="決算整理で未払として扱う")
            how.add_argument("--as-new", action="store_true", help="証憑から新しい仕訳の候補を作る")
        if name == "approve":
            target = p.add_mutually_exclusive_group(required=True)
            target.add_argument("--ids", nargs="+", help="承認する取り込み元ID")
            target.add_argument("--review-file", type=Path, help="オーナーが番号で承認した確認用Excel（--numbers と使う）")
            p.add_argument("--numbers", nargs="+", help="確認用Excelの番号（--review-file と使う）")
        if name == "unimport":
            p.add_argument("--source", required=True, nargs="+",
                           help="取り消す資料（記録どおりの inbox/…。同じ口座で期間が重なる資料はまとめて並べる）")
        if name == "discard":
            p.add_argument("--ids", required=True, nargs="+", help="破棄する取り込み元ID")
            p.add_argument("--reason", required=True, help="破棄の理由（discard-log.csv に記録）")
        if name in ("post", "check", "set-tax", "tax-summary", "close-candidates", "close-post", "close-unpost",
                    "statements", "export-tatsujin"):
            p.add_argument("--prev-year-dir", type=Path)
        if name == "close-candidates":
            p.add_argument("--next-year-dir", type=Path, help="翌期の年度フォルダ（未払・未収の照合に使う）")
            p.add_argument("--sources", type=Path, help="省略時はインスタンスの context/company/kessan-sources.yaml（無ければ使わない）")
            p.add_argument("--payees", type=Path, help="省略時はインスタンスの context/company/kessan-payees.yaml")
        if name == "close-approve":
            target = p.add_mutually_exclusive_group(required=True)
            target.add_argument("--ids", nargs="+", help="承認する候補の取り込み元ID（close:…）")
            target.add_argument("--all", action="store_true", help="adjustments-staging.csv の候補をすべて承認する")
        if name == "close-unapprove":
            p.add_argument("--ids", required=True, nargs="+", help="承認を外す候補の取り込み元ID（close:…）")
        if name == "close-reject":
            p.add_argument("--ids", required=True, nargs="+", help="却下する候補の取り込み元ID（close:…）")
            p.add_argument("--reason", required=True, help="却下の理由（close-report.md に載る）")
        if name == "close-unreject":
            p.add_argument("--ids", required=True, nargs="+", help="却下を取り消す候補の取り込み元ID（close:…）")
        if name == "close-unpost":
            p.add_argument("--ids", required=True, nargs="+", help="登録を取り消す決算整理仕訳の取り込み元ID（close:…）")
        if name == "export-tatsujin":
            p.add_argument("--kind", nargs="+", choices=TATSUJIN_KINDS,
                           help="出す種類（省略時はこの年度に該当する全部）")
        if name == "close-set-amount":
            p.add_argument("--item", required=True, help="消費税 か 法人税等")
            p.add_argument("--amount", required=True, type=int, help="達人で計算した額（円。還付はマイナス）")
    return parser


def _upgrade_year_files(year_dir):
    """段階3a より前に作った年度フォルダ（税の列が無いCSV）を、今の列構成に直す（値と行の順序はそのまま）。"""
    if not (year_dir / PERIOD_FILE).exists():
        return
    for name, columns in YEAR_FILES.items():
        try:
            upgrade_header(year_dir / name, columns)
        except OSError:
            raise cannot_write(name) from None


def _company_file(year_dir, given, name):
    return given or year_dir.resolve().parents[2] / "context" / "company" / name


def _load_accounts(year_dir):
    """科目マスタに、インスタンスの context/company/kessan-accounts.yaml（会社別の追加科目）があれば足して読む。"""
    try:
        extra = _company_file(year_dir, None, "kessan-accounts.yaml")
    except IndexError:  # インスタンスの外に作った年度フォルダ
        extra = None
    return load_accounts(extra_path=extra)


def _run_check(year_dir, accounts, prev_year_dir):
    findings = run_checks(year_dir, accounts, prev_year_dir)
    path = write_report(year_dir, findings)
    ng = sum(f.level == "NG" for f in findings)
    print(f"検算: NG {ng}件 / 警告 {len(findings) - ng}件（{path}）")
    return findings


def _refuse_on_ng(year_dir, accounts, prev_year_dir, what):
    """検算を流し、NGが1件でもあれば what を出さずに止める（spec §7）。"""
    findings = _run_check(year_dir, accounts, prev_year_dir)
    if has_ng(findings):
        ng = sum(f.level == "NG" for f in findings)
        raise KessanError(f"検算にNGが{ng}件あるため{what}を出しません（output/check-result.md を確認）")


def _company_dir(year_dir):
    """インスタンスの context/company。年度フォルダがインスタンスの外なら None。"""
    try:
        return year_dir.resolve().parents[2] / "context" / "company"
    except IndexError:
        return None


def _close_sources(year_dir, args):
    """決算整理で使う口座の設定。--sources を指定したら必須、省略時はインスタンスのファイルがあれば読む（無ければ空）。"""
    if args.sources:
        return load_sources(args.sources)
    company_dir = _company_dir(year_dir)
    path = company_dir / "kessan-sources.yaml" if company_dir else None
    return load_sources(path) if path and path.exists() else {}


def _payees_path(year_dir, args):
    return _company_file(year_dir, getattr(args, "payees", None), "kessan-payees.yaml")


def _load_payees(year_dir, args):
    """支払先の対応表（kessan-payees.yaml）。無ければ空。"""
    try:
        return load_payees(_payees_path(year_dir, args))
    except IndexError:  # インスタンスの外に作った年度フォルダ
        return {}


def _auto_approve(year_dir, accounts, args):
    """確立済みパターンに一致した明細行を自動承認する（import-bank・import-extracted・set-accounts の後）。"""
    patterns = load_patterns(_company_file(year_dir, args.patterns, "kessan-patterns.yaml"), accounts)
    if not patterns:
        return
    sources = load_sources(_company_file(year_dir, args.sources, "kessan-sources.yaml"))
    r = auto_approve(year_dir, accounts, patterns, payment_accounts(sources), load_abbreviations(),
                     tax_settings=load_tax_settings(year_dir), payees=_load_payees(year_dir, args))
    print(f"確立済みパターン: 自動承認 {r.approved}件 / パターンと科目候補が合わず要確認 {r.conflicts}件")


def _fill_tax(year_dir, accounts):
    """課税の年度で、未承認の行の税区分・インボイス・個別対応を補う。免税の年度では何もしない。"""
    settings = load_tax_settings(year_dir)
    if not settings.taxable:
        return
    r = fill_tax(year_dir, accounts, settings, load_transitions())
    if r.filled or r.flagged or r.unapproved:
        line = f"税区分を補完: {r.filled}件／要確認: {r.flagged}件"
        if r.unapproved:
            line += f"／証憑の税率と合わず承認を外した行: {r.unapproved}件（要確認理由を見て承認し直す）"
        print(line)


def _auto_approve_after(year_dir, accounts, args, done):
    """反映（取り込み・科目候補）の後に自動承認し、税区分を補う。失敗したら、反映は完了していることを伝えて 1 を返す（成功なら 0）。"""
    code = 0
    try:
        _auto_approve(year_dir, accounts, args)
    except KessanError as e:
        print(f"エラー: 確立済みパターンの自動承認で止まりました: {e}", file=sys.stderr)
        print(f"{done}（自動承認だけが止まりました。原因を直してから同じコマンドを再実行すると、二重には反映せず自動承認をやり直します）",
              file=sys.stderr)
        code = 1
    try:
        _fill_tax(year_dir, accounts)
    except KessanError as e:
        print(f"エラー: 税区分の補完で止まりました: {e}", file=sys.stderr)
        print(f"{done}（税区分の補完だけが止まりました。原因を直してから同じコマンドを再実行してください）", file=sys.stderr)
        code = 1
    return code


def _print_extracted_result(r):
    print(f"読み取り結果の取り込み: ファイル {len(r.imported)}件 / 追加 {r.added}件"
          f" / 取り込み済みの明細のためスキップ {r.duplicates}件 / 金額0のためスキップ {r.zero_amount}件")
    discarded = r.discarded + len(r.discarded_receipts)
    if discarded:
        print(f"破棄済みのため入れなかった行：{discarded}件（discard-log.csv に記録あり）")
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


def _init_tax(args):
    """init の消費税引数を period.yaml 用の dict にする。--consumption-tax が無ければ None（変更しない）。"""
    if args.consumption_tax is None:
        if args.tax_method or args.tax_calc or args.tax_allocation or args.small_exemption:
            raise KessanError("--tax-method などは --consumption-tax 課税 と一緒に指定してください")
        return None
    if args.consumption_tax == "免税":
        return {"区分": "免税"}
    if not args.tax_method:
        raise KessanError("--consumption-tax 課税 のときは --tax-method（原則／簡易／2割特例）が必要です")
    if not args.tax_calc:
        raise KessanError("--consumption-tax 課税 のときは --tax-calc（割戻し／積上げ）が必要です")
    if args.tax_method == "原則" and not args.tax_allocation:
        raise KessanError("--tax-method 原則 のときは --tax-allocation（全額控除／個別対応／一括比例配分）が必要です")
    return {"区分": "課税", "方式": args.tax_method, "計算": args.tax_calc,
            "控除": args.tax_allocation or "", "少額特例": args.small_exemption}


def _parse_parts(texts):
    """--part 10:1080 の形を (税率, 金額) にする。"""
    parts = []
    for text in texts:
        rate, sep, amount = text.partition(":")
        if not sep or not rate.strip().isdigit() or not amount.strip().isdigit():
            raise KessanError(f"--part は 税率:税込金額 の形で書く（例：10:1080）。値: {text}")
        parts.append((int(rate), int(amount)))
    return parts


def main(argv=None):
    args = _parser().parse_args(argv)
    year_dir = args.year_dir
    try:
        if args.command == "init":
            init_year_dir(year_dir, args.start, args.end, tax=_init_tax(args), company_name=args.company_name)
            print(f"年度フォルダを用意しました: {year_dir}")
            return 0
        if not (year_dir / "journal.csv").exists():
            raise KessanError(f"年度フォルダが初期化されていません（先に init を実行）: {year_dir}")
        _upgrade_year_files(year_dir)
        accounts = _load_accounts(year_dir)

        if args.command == "add-payee":
            try:
                path = _payees_path(year_dir, args)
            except IndexError:
                raise KessanError("年度フォルダがインスタンスの work/kessan/<期>/ の下にありません（--payees で対応表のファイルを指定してください）") from None
            if add_payee(path, args.name, args.kana):
                print(f"支払先を追加しました: {args.name} ← {args.kana}（{path}）")
            else:
                print(f"すでに登録済みです: {args.name} ← {args.kana}")
            return 0
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
            if r.discarded:
                print(f"破棄済みのため入れなかった行：{r.discarded}件（discard-log.csv に記録あり）")
            if r.overlapping_imports:
                print("警告: 同じ口座で期間が重なる取り込み済みファイルがあります: "
                      + "、".join(r.overlapping_imports)
                      + "（別名で保存し直した同じ明細でないか、staging.csv の要確認理由を確認）")
            return _auto_approve_after(year_dir, accounts, args, "取り込みは完了しています")
        if args.command == "import-extracted":
            sources = _company_file(year_dir, args.sources, "kessan-sources.yaml")
            r = import_extracted(year_dir, sources, accounts, args.file or extracted_files(year_dir),
                                 payees=_load_payees(year_dir, args))
            _print_extracted_result(r)
            code = _auto_approve_after(year_dir, accounts, args, "取り込みは完了しています")
            return EXIT_SOME_FILES_REJECTED if r.rejected else code
        if args.command == "set-accounts":
            sources = load_sources(_company_file(year_dir, args.sources, "kessan-sources.yaml"))
            count = set_accounts(year_dir, accounts, load_updates(args.file), payment_accounts(sources))
            print(f"科目候補: {count}件を反映しました")
            return _auto_approve_after(year_dir, accounts, args, "科目候補の反映は完了しています")
        if args.command == "set-tax":
            count = set_journal_tax(year_dir, load_tax_updates(args.file))
            print(f"税の列: {count}行を直しました")
            if has_ng(_run_check(year_dir, accounts, args.prev_year_dir)):
                print("修正は反映済みですが、検算でNGがあります（check-result.md を確認）")
                return EXIT_POSTED_WITH_NG
            return 0
        if args.command == "split-tax":
            if args.voucher is not None or args.line is not None:
                if args.id is not None:
                    raise KessanError("--id と --voucher・--line は同時に使えません")
                if args.voucher is None or args.line is None:
                    raise KessanError("登録済み伝票を分けるには --voucher と --line の両方が要ります")
                count = split_posted(year_dir, args.voucher, args.line, _parse_parts(args.part), accounts)
                print(f"税率で分けました: 伝票{args.voucher} 行{args.line}（{count}行になりました）")
                if has_ng(_run_check(year_dir, accounts, args.prev_year_dir)):
                    print("分割は反映済みですが、検算でNGがあります（check-result.md を確認）")
                    return EXIT_POSTED_WITH_NG
                return 0
            if args.id is None:
                raise KessanError("--id（未承認の明細）か --voucher と --line（登録済み伝票）を指定してください")
            sources = load_sources(_company_file(year_dir, args.sources, "kessan-sources.yaml"))
            number = split_tax(year_dir, args.id, _parse_parts(args.part), payment_accounts(sources), accounts)
            print(f"税率で分けました: 取り込み元ID {args.id}（staging.csv 内の伝票番号 {number}。承認はまだです）")
            return 0
        if args.command == "resolve-evidence":
            sources = load_sources(_company_file(year_dir, args.sources, "kessan-sources.yaml"))
            result = resolve_evidence(year_dir, args.evidence_id, match=args.match, as_payable=args.as_payable,
                                     as_new=args.as_new, sources=sources, accounts=accounts,
                                     payees=_load_payees(year_dir, args))
            print(f"証憑 {args.evidence_id} を「{result.message}」にしました")
            _fill_tax(year_dir, accounts)
            return 0
        if args.command == "review":
            _fill_tax(year_dir, accounts)
            s = write_review(year_dir, payees=_load_payees(year_dir, args))
            print(f"確認用Excel: {s.path}")
            print(f"行 {s.rows}件（要確認 {s.needs_review}件 / 承認済み {s.approved}件）"
                  f" / 読み取り信頼度が低い {s.low_confidence}件 / 明細に該当なし {s.no_match}件")
            print(f"突き合わせ先が複数 {s.multiple}件 / 未払候補 {s.unpaid}件 / 読めなかった資料 {s.unreadable}件")
            if s.without_id:
                print(f"取り込み元IDの無い行 {s.without_id}件は Excel に載せていません（staging.csv で確認）")
            return 0
        if args.command == "apply-review":
            r = apply_review(year_dir, accounts, read_review(args.file))
            _fill_tax(year_dir, accounts)
            print(f"承認を反映: {r.approved}件 / 修正メモのため承認を外した行 {r.unapproved}件")
            for source_id, memo in r.memos:
                print(f"修正メモ: {source_id}\t{memo}")
            if r.changed:
                print("Excel出力後に内容が変わったため承認していない行：" + "、".join(r.changed)
                      + "（review を出し直して確認）")
            if r.missing:
                print("staging.csv に無い取り込み元ID（登録済み・削除済み）: " + "、".join(r.missing))
            if r.not_done_marks:
                print(f"承認欄に『済』以外が書かれた行：{r.not_done_marks}件（承認していません）")
            return 0
        if args.command == "approve":
            if args.ids:
                if args.numbers:
                    raise KessanError("--numbers は --review-file と一緒に使います（--ids とは一緒に使えません）")
                print(f"承認: {approve(year_dir, accounts, args.ids)}件")
                _fill_tax(year_dir, accounts)
                return 0
            if not args.numbers:
                raise KessanError("--review-file には、承認する番号を --numbers で指定してください")
            r = approve_numbers(year_dir, accounts, read_review(args.review_file), args.numbers)
            _fill_tax(year_dir, accounts)
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
            _fill_tax(year_dir, accounts)
            r = post_approved(year_dir, accounts)
            print(f"登録: 伝票 {len(r.vouchers)}件（{', '.join(r.vouchers) or 'なし'}） / 未承認で残った行 {r.remaining}件")
            findings = _run_check(year_dir, accounts, args.prev_year_dir)
            if has_ng(findings):
                print("登録は完了済みですが、検算でNGがあります（check-result.md を確認）")
                return EXIT_POSTED_WITH_NG  # 1（エラーで何も登録していない）・2（コマンドの使い方の誤り。argparse）と区別する
            return 0
        if args.command == "check":
            return 1 if has_ng(_run_check(year_dir, accounts, args.prev_year_dir)) else 0
        if args.command == "tax-summary":
            settings = load_tax_settings(year_dir)
            if not settings.taxable:
                raise KessanError("免税の年度です（period.yaml の消費税）")
            findings = _run_check(year_dir, accounts, args.prev_year_dir)
            if has_ng(findings):
                ng = sum(f.level == "NG" for f in findings)
                raise KessanError(f"検算にNGが{ng}件あるため集計表を出しません（output/check-result.md を確認）")
            summary = summarize(year_dir, accounts, settings, load_transitions(), load_rates())
            csv_path, xlsx_path = write_summary(year_dir, summary)
            est = "概算しない" if summary.estimate is None else f"{summary.estimate:,}円"
            print(f"消費税の集計表: {csv_path} / {xlsx_path}（課税売上割合 {summary.taxable_sales_ratio * 100:.2f}% / 概算納付税額 {est}）")
            for n in summary.notes:
                print(f"注記: {n}")
            return 0
        if args.command == "close-set-amount":
            set_amount(year_dir, args.item, args.amount)
            print(f"{args.item}の額を {args.amount:,}円 にしました（close-overrides.yaml。close-candidates で候補に反映）")
            return 0
        if args.command == "close-candidates":
            r = run_close(year_dir, accounts, next_year_dir=args.next_year_dir, prev_year_dir=args.prev_year_dir,
                          sources=_close_sources(year_dir, args), payees=_load_payees(year_dir, args),
                          company_dir=_company_dir(year_dir))
            print(f"決算整理の候補: 要確認 {r.candidates_added}件 / 承認済み・登録済みのため据え置き {r.candidates_kept}件"
                  f" / 出なくなったため消した候補 {r.candidates_removed}件 / 却下済み {r.candidates_rejected}件 / 所見 {len(r.notes)}件")
            for n in r.notes:
                print(f"所見［{n.item}］: {n.message}")
            print(f"報告: {r.report_path}")
            return 0
        if args.command == "close-approve":
            held = []
            if args.all:
                ids, held = approve_all_ids(year_dir)
            else:
                ids = args.ids
            print(f"承認: {approve_candidates(year_dir, ids) if ids or not held else 0}行")
            for sid in held:
                print(f"承認せずに残しました: {sid}（消費税の額が概算のため。達人の額を close-set-amount --item 消費税 で入れて"
                      f" close-candidates を流し直してから承認する。概算のまま登録するなら --ids {sid} で承認する）")
            return 0
        if args.command == "close-unapprove":
            print(f"承認を外しました: {unapprove_candidates(year_dir, args.ids)}行")
            return 0
        if args.command == "close-reject":
            reject_candidates(year_dir, args.ids, args.reason)
            print(f"却下: {len(args.ids)}件（以後の close-candidates では出しません）")
            return 0
        if args.command == "close-unreject":
            unreject_candidates(year_dir, args.ids)
            print(f"却下を取り消しました: {len(args.ids)}件（close-candidates でまた候補に出ます）")
            return 0
        if args.command == "close-unpost":
            numbers = unpost_candidates(year_dir, args.ids)
            print(f"決算整理仕訳の登録を取り消しました: {len(numbers)}件（{', '.join(numbers)}。close-candidates で候補に出直します）")
            if has_ng(_run_check(year_dir, accounts, args.prev_year_dir)):
                print("取り消しは完了済みですが、検算でNGがあります（check-result.md を確認）")
                return EXIT_POSTED_WITH_NG
            return 0
        if args.command == "close-post":
            numbers = post_candidates(year_dir, accounts, prev_year_dir=args.prev_year_dir)
            print(f"決算整理仕訳の登録: {len(numbers)}件（{', '.join(numbers) or 'なし'}）")
            if has_ng(_run_check(year_dir, accounts, args.prev_year_dir)):
                print("登録は完了済みですが、検算でNGがあります（check-result.md を確認）")
                return EXIT_POSTED_WITH_NG
            return 0
        if args.command == "statements":
            _refuse_on_ng(year_dir, accounts, args.prev_year_dir, "決算書")
            st = build_statements(year_dir, accounts)
            materials = {
                uchiwake.OUTPUT_XLSX: uchiwake.material_workbook(uchiwake.build(year_dir, accounts)),
                gaikyo.OUTPUT_XLSX: gaikyo.material_workbook(gaikyo.build(year_dir, accounts, st)),
            }
            paths = write_statements(year_dir, st, company_dir=_company_dir(year_dir), workbooks=materials)
            for w in st.warnings:
                print(f"警告: {w}")
            print(f"当期純利益 {st.profits['当期純利益']:,}円")
            for path in paths:
                print(f"出力: {path}")
            return 0
        if args.command == "export-tatsujin":
            _refuse_on_ng(year_dir, accounts, args.prev_year_dir, "達人XML")
            st = build_statements(year_dir, accounts)
            notes = []
            paths = export_tatsujin(year_dir, accounts, args.kind, st=st, notes=notes)
            for w in st.warnings + notes:
                print(f"警告: {w}")
            for path in paths:
                print(f"出力: {path}")
            return 0
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
