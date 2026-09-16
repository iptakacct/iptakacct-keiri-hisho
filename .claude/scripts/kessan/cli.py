"""kessan：会計システムなしで帳簿を作るためのコマンド（段階1：取り込み・登録・検算・試算表）。

インスタンスフォルダで実行する例:
  python ../.claude/scripts/kessan/cli.py init --year-dir work/kessan/2026-03期 --start 2025-04-01 --end 2026-03-31
  python ../.claude/scripts/kessan/cli.py import-bank --year-dir work/kessan/2026-03期 --account-id main --file work/kessan/2026-03期/inbox/2025-04.csv
  python ../.claude/scripts/kessan/cli.py post --year-dir work/kessan/2026-03期
  python ../.claude/scripts/kessan/cli.py check --year-dir work/kessan/2026-03期 [--prev-year-dir work/kessan/2025-03期]
  python ../.claude/scripts/kessan/cli.py tb --year-dir work/kessan/2026-03期
"""
import argparse
import sys
from pathlib import Path

from accounts import load_accounts
from bank_import import import_bank
from check import has_ng, run_checks, write_report
from common import KessanError, init_year_dir
from post import post_approved
from trial_balance import write_trial_balance


def _parser():
    parser = argparse.ArgumentParser(prog="kessan")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "import-bank", "post", "check", "tb"):
        p = sub.add_parser(name)
        p.add_argument("--year-dir", required=True, type=Path)
        if name == "init":
            p.add_argument("--start", required=True, help="期首日 YYYY-MM-DD")
            p.add_argument("--end", required=True, help="期末日 YYYY-MM-DD")
        if name == "import-bank":
            p.add_argument("--account-id", required=True)
            p.add_argument("--file", required=True, type=Path)
            p.add_argument("--sources", type=Path)
        if name in ("post", "check"):
            p.add_argument("--prev-year-dir", type=Path)
    return parser


def _run_check(year_dir, accounts, prev_year_dir):
    findings = run_checks(year_dir, accounts, prev_year_dir)
    path = write_report(year_dir, findings)
    ng = sum(f.level == "NG" for f in findings)
    print(f"検算: NG {ng}件 / 警告 {len(findings) - ng}件（{path}）")
    return findings


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

        if args.command == "import-bank":
            sources = args.sources or year_dir.resolve().parents[2] / "context" / "company" / "kessan-sources.yaml"
            r = import_bank(year_dir, sources, args.account_id, args.file)
            print(f"取り込み: 追加 {r.added}件 / 取り込み済みのためスキップ {r.duplicates}件 / 金額0のためスキップ {r.zero_amount}件"
                  f" / 期間外のためスキップ {r.out_of_period}件")
            if r.overlapping_imports:
                print("警告: 同じ口座で期間が重なる取り込み済みファイルがあります: "
                      + "、".join(r.overlapping_imports)
                      + "（別名で保存し直した同じ明細でないか、staging.csv の要確認理由を確認）")
            return 0
        if args.command == "post":
            r = post_approved(year_dir, accounts)
            print(f"登録: 伝票 {len(r.vouchers)}件（{', '.join(r.vouchers) or 'なし'}） / 未承認で残った行 {r.remaining}件")
            findings = _run_check(year_dir, accounts, args.prev_year_dir)
            if has_ng(findings):
                print("登録は完了済みですが、検算でNGがあります（check-result.md を確認）")
                return 2  # 1（エラーで何も登録していない）と区別する
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
