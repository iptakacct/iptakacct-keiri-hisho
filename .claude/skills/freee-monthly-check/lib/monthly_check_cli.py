"""freee-monthly-check CLIエントリポイント。"""
import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "freee-api-post", "lib")
)
import freee_api
import monthly_check

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


def default_target_month():
    """直近の完了月を"YYYY-MM"形式で返す（実行日の前月）。"""
    today = date.today()
    year, month = today.year, today.month - 1
    if month == 0:
        month = 12
        year -= 1
    return f"{year:04d}-{month:02d}"


def load_company(companies_json_path, company_key):
    with open(companies_json_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    for company in config["companies"]:
        if company["name"] == company_key or str(company["company_id"]) == company_key:
            return company
    raise ValueError(f"companies.jsonに見つからない会社キー: {company_key}")


def cmd_run(args):
    company = load_company(args.companies_json, args.company_key)
    credentials_path = os.path.join(args.repo_root, company["credentials_path"])
    token = freee_api.ensure_access_token(credentials_path)
    target_month = args.month or default_target_month()
    check_config = company.get("monthly_check", {})

    report = monthly_check.run_monthly_check(
        freee_api.api_call, token, company["company_id"], company["name"],
        target_month, check_config,
    )

    output_dir = os.path.join(args.repo_root, company["instance"], "work", "monthly-check")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{target_month}.md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"レポートを保存しました: {output_path}")
    print(report)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="freee-monthly-check CLI")
    parser.add_argument("--companies-json", required=True)
    parser.add_argument("--repo-root", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run")
    p_run.add_argument("company_key")
    p_run.add_argument("--month", default=None, help="対象月(YYYY-MM)。省略時は直近の完了月")
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
