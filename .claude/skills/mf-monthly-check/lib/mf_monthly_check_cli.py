"""mf-monthly-check CLIエントリポイント。HTTPやMCP呼び出しは一切行わない
——Claudeが mf_cli read の出力（形はMCPと同じ）から組み立てたJSONファイルを
読み込み、分析してレポートをファイルに書き出すだけ。"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import mf_monthly_check

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


def run(args):
    with open(args.input, "r", encoding="utf-8") as f:
        raw_input_data = json.load(f)

    input_data = {
        "target_month": raw_input_data["target_month"],
        "company_name": raw_input_data["company_name"],
        "journals": raw_input_data.get("journals"),
        "trial_pl_by_month": {
            month: mf_monthly_check.extract_account_leaves(rows)
            for month, rows in (raw_input_data.get("trial_pl_by_month") or {}).items()
        },
        "trial_pl_totals_by_month": {
            month: mf_monthly_check.extract_pl_totals(rows)
            for month, rows in (raw_input_data.get("trial_pl_by_month") or {}).items()
        },
        "trial_bs": mf_monthly_check.extract_account_leaves(raw_input_data.get("trial_bs")),
        "fetch_errors": raw_input_data.get("fetch_errors") or {},
        "fiscal_year_start": raw_input_data.get("fiscal_year_start"),
        "fiscal_year_end": raw_input_data.get("fiscal_year_end"),
        "trial_pl_ytd": raw_input_data.get("trial_pl_ytd"),
        # 繰越のずれチェック用。SKILL.mdの形（科目名→値の配列）で渡されるのでそのまま引き継ぐ
        "trial_bs_prior_closing": raw_input_data.get("trial_bs_prior_closing"),
        "trial_bs_current_opening": raw_input_data.get("trial_bs_current_opening"),
    }

    config = raw_input_data.get("config") or {}
    report = mf_monthly_check.run_monthly_check(input_data, config)

    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"レポートを保存しました: {args.output}")
    print(report)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="mf-monthly-check CLI")
    parser.add_argument("--input", required=True, help="Claudeが組み立てた入力JSONファイルのパス")
    parser.add_argument("--output", required=True, help="レポートMarkdownの出力先パス")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
