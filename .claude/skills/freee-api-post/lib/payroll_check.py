"""freee人事労務の給与確定リマインド／事後チェックの判定・本文生成。
API呼び出しを含まない純関数部（このファイル上半分）はtest_payroll_check.pyで検証する。

使い方（スケジューラーから月2回）:
  python payroll_check.py remind   # 毎月20日ごろ：確定リマインド（未確定の明細があれば通知）
  python payroll_check.py check    # 毎月25日ごろ：事後チェック（未確定、または会計に給与仕訳が無ければ通知）
会社ごとの設定は companies.json の `payroll` ブロック（companies.example.json 参照）。
投稿先は `payroll.slack_webhook_env`（.env のキー名）、無ければ GENERAL_SLACK_WEBHOOK_URL。
メンション先は .env の SLACK_MENTION_USER_ID（未設定ならメンションなし）。
読み取り専用（freeeへは書き込まない）。"""
from __future__ import annotations

import argparse
import calendar
import datetime
import json
import os
import sys
import urllib.request
from pathlib import Path

import freee_api

REPO_ROOT = Path(__file__).resolve().parents[4]
ENV_FILE = REPO_ROOT / ".claude" / "scripts" / ".env"

SALARY_ACCOUNT_NAMES = ("役員報酬", "給料手当", "給与手当")
COMPANIES_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "companies.json")


def load_env(path: Path = ENV_FILE) -> None:
    """.env の KEY=VALUE を、未設定のものだけ環境変数に取り込む（認証情報は値を表示しない）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def mention_text() -> str:
    uid = os.environ.get("SLACK_MENTION_USER_ID", "")
    return f"<@{uid}>" if uid else ""


def pay_month(today: datetime.date, offset: int) -> tuple[int, int]:
    """実行日の属する月にoffsetを足した「支払月」(year, month)を返す。"""
    y, m = today.year, today.month + offset
    while m > 12:
        y, m = y + 1, m - 12
    while m < 1:
        y, m = y - 1, m + 12
    return (y, m)


def to_int(s) -> int:
    if s is None or s == "":
        return 0
    return int(float(str(s).replace(",", "")))


def _fmt(n: int) -> str:
    return f"{n:,}"


def summarize(stmt: dict) -> dict:
    return {
        "employee": stmt.get("employee_display_name") or stmt.get("employee_name") or "?",
        "fixed": bool(stmt.get("fixed")),
        "gross": to_int(stmt.get("gross_payment_amount")),
        "net": to_int(stmt.get("net_payment_amount")),
        "deductions": {d.get("name", "?"): to_int(d.get("amount"))
                       for d in (stmt.get("deductions") or [])},
        "hourly_zero": stmt.get("pay_calc_type") == "hourly"
                       and to_int(stmt.get("gross_payment_amount")) == 0,
    }


def diff_statements(current: list[dict], previous: list[dict]) -> list[str]:
    prev_by_name = {p["employee"]: p for p in previous}
    out = []
    for c in current:
        p = prev_by_name.get(c["employee"])
        if p is None:
            out.append(f"{c['employee']}: （新規）前月データなし")
            continue
        for label, key in (("総支給", "gross"), ("手取り", "net")):
            if c[key] != p[key]:
                out.append(f"{c['employee']}: {label} {_fmt(p[key])}→{_fmt(c[key])}")
        names = set(c["deductions"]) | set(p["deductions"])
        for name in sorted(names):
            cv, pv = c["deductions"].get(name, 0), p["deductions"].get(name, 0)
            if cv != pv:
                out.append(f"{c['employee']}: {name} {_fmt(pv)}→{_fmt(cv)}")
    return out


def build_remind_message(company_name, year, month, summaries, diffs,
                         fix_url, mention) -> str:
    unfixed = [s for s in summaries if not s["fixed"]]
    lines = [f"{mention} 【給与確定リマインド】{company_name} {year}年{month}月分"]
    if not summaries:
        lines.append("■ 給与明細が未作成です。freee人事労務で給与計算が走っているか確認してください")
    elif unfixed:
        lines.append(f"■ 未確定の明細が{len(unfixed)}名分あります。確定をお願いします")
    else:
        lines.append("■ すべて確定済みです（対応不要）")
    for s in summaries:
        state = "確定済み" if s["fixed"] else "未確定"
        lines.append(f"・{s['employee']}: 総支給 {_fmt(s['gross'])} ／ 手取り {_fmt(s['net'])}（{state}）")
        if s["hourly_zero"]:
            lines.append("　⚠ 時給者の支給が0円です。勤怠の反映漏れの可能性")
    if diffs:
        lines.append("■ 前月からの変化（定期振込の金額も見直してください）")
        lines += [f"・{d}" for d in diffs]
    else:
        lines.append("■ 前月から金額の変化はありません")
    lines.append(f"確定画面: {fix_url}")
    return "\n".join(lines)


def build_check_message(company_name, year, month, summaries, journal_found,
                        mention) -> str:
    unfixed = [s for s in summaries if not s["fixed"]]
    if summaries and not unfixed:
        lines = [f"【給与】{company_name} {year}年{month}月分: 全員確定済み"]
        lines.append("freee会計への仕訳連携: あり" if journal_found
                     else f"{mention} ⚠ freee会計に給与仕訳が見当たりません。人事労務→会計の連携を確認してください")
    else:
        lines = [f"{mention} 【給与・再リマインド】{company_name} {year}年{month}月分が未確定です"
                 f"（未確定{len(unfixed)}名／全{len(summaries)}名）。確定をお願いします"]
    return "\n".join(lines)


def should_notify(mode: str, summaries: list[dict], journal_found: bool = False) -> bool:
    """確定済みなら出さない。

    remind：全員確定済みなら出さない。check：全員確定済みで会計への仕訳もあれば出さない
    （仕訳が見当たらないときは確認が要るので出す）。明細が未作成のときは出す。
    """
    if not summaries or not all(s["fixed"] for s in summaries):
        return True
    return mode == "check" and not journal_found


# ---- ここからAPI呼び出し・CLI（実環境でのみ動く部分） ----

def load_companies() -> dict:
    with open(COMPANIES_JSON, encoding="utf-8") as f:
        return json.load(f)


def fetch_statements(token, company_id, year, month) -> list[dict]:
    return fetch_all_pages(
        token, company_id,
        f"/hr/api/v1/salaries/employee_payroll_statements?year={year}&month={month}",
        "employee_payroll_statements", 100)


def fetch_all_pages(token, company_id, path: str, key: str, limit: int) -> list[dict]:
    """limit/offset で最後のページまで取得する。1回で打ち切ると既定件数を超えた分が黙って欠ける"""
    items: list[dict] = []
    offset = 0
    sep = "&" if "?" in path else "?"
    while True:
        r = freee_api.api_call(token, company_id, "GET",
                               f"{path}{sep}limit={limit}&offset={offset}")
        page = r.get(key, [])
        items.extend(page)
        if len(page) < limit:
            return items
        offset += limit


def _month_range(year, month):
    last = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}"


def has_salary_journal(token, company_id, year, month) -> bool:
    accounts = freee_api.api_call(token, company_id, "GET",
                                  "/api/1/account_items")["account_items"]
    ids = {a["id"] for a in accounts if a["name"] in SALARY_ACCOUNT_NAMES}
    if not ids:
        return False
    start, end = _month_range(year, month)
    journals = freee_api.api_call(
        token, company_id, "GET",
        f"/api/1/manual_journals?start_issue_date={start}&end_issue_date={end}&limit=100"
    ).get("manual_journals", [])
    deals = freee_api.api_call(
        token, company_id, "GET",
        f"/api/1/deals?start_issue_date={start}&end_issue_date={end}&limit=100"
    ).get("deals", [])
    for j in journals:
        if any(d.get("account_item_id") in ids for d in j.get("details", [])):
            return True
    for d in deals:
        if any(x.get("account_item_id") in ids for x in d.get("details", [])):
            return True
    return False


def post_webhook(url: str, text: str) -> None:
    req = urllib.request.Request(
        url, data=json.dumps({"text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as resp:
        resp.read()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="給与確定リマインド／事後チェック")
    parser.add_argument("mode", choices=["remind", "check"])
    parser.add_argument("--company")
    parser.add_argument("--date", help="実行日を上書き（YYYY-MM-DD、テスト用）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    load_env()
    mention = mention_text()
    today = (datetime.date.fromisoformat(args.date) if args.date
             else datetime.date.today())
    failures = 0
    for co in load_companies()["companies"]:
        pr = co.get("payroll") or {}
        if not pr.get("enabled"):
            continue
        if args.company and co["name"] != args.company:
            continue
        try:
            year, month = pay_month(today, pr.get("pay_month_offset", 0))
            token = freee_api.ensure_access_token(
                os.path.join(str(REPO_ROOT), co["credentials_path"]))
            cur = [summarize(s) for s in
                   fetch_statements(token, co["company_id"], year, month)]
            if args.mode == "remind":
                py, pm = pay_month(datetime.date(year, month, 1), -1)
                prev = [summarize(s) for s in
                        fetch_statements(token, co["company_id"], py, pm)]
                diffs = diff_statements(cur, prev) if prev else []
                fix_url = pr.get("fix_url", "").format(year=year, month=month)
                msg = build_remind_message(co["name"], year, month, cur, diffs,
                                           fix_url, mention)
                journal = False
            else:
                journal = (bool(cur) and all(s["fixed"] for s in cur)
                           and has_salary_journal(token, co["company_id"], year, month))
                msg = build_check_message(co["name"], year, month, cur, journal, mention)
            if not should_notify(args.mode, cur, journal):
                print(f"{co['name']}: 全員確定済みのため通知しません")
                continue
            if args.dry_run:
                print(f"----- {co['name']} -----")
                print(msg)
            else:
                url = (os.environ.get(pr.get("slack_webhook_env", ""), "")
                       or os.environ.get("GENERAL_SLACK_WEBHOOK_URL", ""))
                if not url:
                    raise RuntimeError("投稿先のWebhookが未設定です（payroll.slack_webhook_env か GENERAL_SLACK_WEBHOOK_URL）")
                post_webhook(url, msg)
        except Exception as e:  # noqa: BLE001 失敗を握りつぶさず集計して通知する
            failures += 1
            print(f"ERROR {co['name']}: {e}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
