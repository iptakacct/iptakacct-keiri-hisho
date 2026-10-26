"""給与の「毎月やること」が未対応のまま残っていないかを検知して報告する。

freee人事労務は締め日の翌営業日ごろに全員分を自動計算するが、成果報酬の加算・
育休中の0処理・飲食部門の金額入力・経費精算の給与への連携は、人が入れないと
誤ったまま確定してしまう。それを毎月手で確認するのをやめるための仕組み。

**書き込みは一切しない。** freee人事労務APIには給与明細の金額を更新する口が無く
（`PUT .../employee_payroll_statements/{id}/remark` の備考欄のみ）、金額の入力は
freee画面での操作になる。このスクリプトは読み取りとSlack Webhookへの投稿だけを行う。

判定ルールは ../payroll-todo.json（雛形は ../payroll-todo.example.json）。人手向けの説明は
各インスタンスの context/company/ に置く給与マスタにある（食い違ったらマスタが正）。
投稿先は会社ブロックの `slack_webhook_env`（.env のキー名）、無ければ GENERAL_SLACK_WEBHOOK_URL。
メンション先は .env の SLACK_MENTION_USER_ID（未設定ならメンションなし）。
"""
from __future__ import annotations

import argparse
import calendar
import datetime
import hashlib
import json
import os
import re
import sys

import freee_api
import payroll_check

MENTION = ""  # main() が .env の SLACK_MENTION_USER_ID から設定する（未設定ならメンションなし）
MENTION_DAYS_BEFORE = 3   # 支払日のこの日数前からメンションを付ける
NOT_FIXED_DAYS_BEFORE = 3  # 支払日のこの日数前から「未確定」を言う
_SKILL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RULES_JSON = os.path.join(_SKILL_DIR, "payroll-todo.json")
if not os.path.exists(RULES_JSON):  # 実設定が無ければ雛形で動かす（テスト用）
    RULES_JSON = os.path.join(_SKILL_DIR, "payroll-todo.example.json")


def load_rules(path: str = RULES_JSON) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["companies"]


def to_int(s) -> int:
    if s is None or s == "":
        return 0
    return int(float(str(s).replace(",", "")))


def _fmt(n: int) -> str:
    return f"{n:,}"


def _date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def in_window(today: datetime.date, closing_day: int, pay_day: int,
              pay_month: str = "next_month") -> bool:
    """締め切った後から支払日までの間だけTrue。

    締める前は明細がまだ無く、支払日を過ぎてから言っても手遅れなので黙る。

    翌月払い（`next_month`、月末締め翌月25日払いなど）は、対象期間が
    前月末で閉じているので支払月の1日から窓が開く。当月払い（`same_month`）は
    締め日の翌日から。月末締めは月の最終日を締め日とみなす（30日の月に31を
    指定しても動くように）。
    """
    if pay_month == "next_month":
        start_day = 1
    else:
        last = calendar.monthrange(today.year, today.month)[1]
        start_day = min(closing_day, last) + 1
    return start_day <= today.day <= pay_day


def _find(statements: list[dict], employee_id: int) -> dict | None:
    for s in statements:
        if s.get("employee_id") == employee_id:
            return s
    return None


def _why(rule: dict) -> str:
    """理由が書いてあれば括弧で添える。無いときに空の括弧を出さない。"""
    reason = rule.get("reason")
    return f"（{reason}）" if reason else ""


def _payment_amount(stmt: dict, name: str) -> int:
    for p in stmt.get("payments") or []:
        if p.get("name") == name:
            return to_int(p.get("amount"))
    return 0


def _has_basic_pay(stmt: dict) -> bool:
    """時給者に実際の労働対価が入っているか。通勤手当だけの状態を弾く。"""
    return _payment_amount(stmt, "基本給") > 0


def _overtime_amount(stmt: dict, name: str) -> int:
    for p in stmt.get("overtime_pays") or []:
        if p.get("name") == name:
            return to_int(p.get("amount"))
    return 0


def check_company_items(
    rules: list[dict],
    statements: list[dict],
    expenses: list[dict],
    pay_date: datetime.date,
    today: datetime.date | None = None,
) -> list[dict]:
    """未対応の項目を返す。1件＝{"type", "employee_id", "text", ("expense")}。"""
    today = today or datetime.date.today()
    found: list[dict] = []

    def add(rule: dict, text: str, **extra) -> None:
        found.append({"type": rule["type"], "employee_id": rule["employee_id"], "text": text, **extra})

    for rule in rules:
        kind = rule["type"]
        stmt = _find(statements, rule["employee_id"])
        name = rule["label"]

        if kind == "must_absent":
            if stmt is not None:
                add(rule, f"・{name}：給与が発生しないはずなのに明細があります")
            continue

        if stmt is None:
            # 退職・休職で対象外になった人。ここで騒ぐと毎月鳴り続ける
            continue

        if kind == "zero_pay":
            if rule.get("until") and today > _date(rule["until"]):
                continue
            gross = to_int(stmt.get("gross_payment_amount"))
            if gross != 0:
                add(rule, f"・{name}：0にするはずが¥{_fmt(gross)}のままです{_why(rule)}")

        elif kind == "require_payment":
            if rule.get("since") and pay_date < _date(rule["since"]):
                continue
            pname = rule["payment_name"]
            if _payment_amount(stmt, pname) <= 0:
                add(rule, f"・{name}：「{pname}」が入っていません")

        elif kind == "require_expense":
            if to_int(stmt.get("total_expense_amount")) > 0:
                continue
            for e in expenses:
                if e.get("applicant_id") != rule.get("applicant_id"):
                    continue
                if e.get("status") != "approved":
                    continue
                # 対象月に申請されたものだけを見る。前月以前の分は精算済み
                issue = _date(e["issue_date"])
                if (issue.year, issue.month) != (pay_date.year, pay_date.month):
                    continue
                add(rule,
                    f"・{name}：経費精算「{e.get('title', '')}」¥{_fmt(to_int(e.get('total_amount')))} "
                    f"が承認済みですが給与に連携されていません", expense=e)

        elif kind == "hourly_zero":
            if stmt.get("pay_calc_type") != "hourly":
                continue
            if not _has_basic_pay(stmt):
                add(rule, f"・{name}：時給の支給額が入っていません{_why(rule)}")

        elif kind == "require_midnight":
            # 月給者の深夜割増。深夜の勤務が無い月もあるので、確定済みの明細には言わない
            if stmt.get("fixed"):
                continue
            if _overtime_amount(stmt, "深夜労働") <= 0:
                add(rule, f"・{name}：深夜労働の割増が入っていません{_why(rule)}")

    # 未確定は支払日が近いときだけ言う。毎日言うと通知が埋まって本題が埋もれる
    if (pay_date - today).days <= NOT_FIXED_DAYS_BEFORE:
        unfixed = [s for s in statements if not s.get("fixed")]
        if unfixed:
            names = "、".join(
                (s.get("employee_display_name") or s.get("employee_name") or "?")
                for s in unfixed[:5]
            )
            more = f" ほか{len(unfixed) - 5}名" if len(unfixed) > 5 else ""
            found.append({"type": "not_fixed", "employee_id": None,
                          "text": f"・未確定が{len(unfixed)}名います（{names}{more}）"})

    return found


def check_company(
    rules: list[dict],
    statements: list[dict],
    expenses: list[dict],
    pay_date: datetime.date,
    today: datetime.date | None = None,
) -> list[str]:
    """未対応の項目を1件1行で返す。空リストなら全て対応済み。"""
    return [i["text"] for i in check_company_items(rules, statements, expenses, pay_date, today)]


def build_message(
    company_name: str,
    year: int,
    month: int,
    pay_date: datetime.date,
    found: list[str],
    today: datetime.date | None = None,
) -> str | None:
    """未対応が無ければNone（投稿しない）。"""
    if not found:
        return None
    today = today or datetime.date.today()
    days_left = (pay_date - today).days
    head = f"■ 給与の未対応（{company_name}／{year}年{month}月支払分）"
    if days_left <= MENTION_DAYS_BEFORE and MENTION:
        head = f"{MENTION} {head}"
    lines = [head, "", f"支払日 {pay_date.month}/{pay_date.day}まであと{days_left}日", ""]
    lines.extend(found)
    lines.append("")
    lines.append("金額の入力はfreee人事労務の画面で行ってください（APIでは変更できません）。")
    return "\n".join(lines)


def fetch_statements(token: str, company_id: int, year: int, month: int) -> list[dict]:
    return payroll_check.fetch_statements(token, company_id, year, month)


def fetch_expenses(token: str, company_id: int) -> list[dict]:
    return payroll_check.fetch_all_pages(
        token, company_id, "/api/1/expense_applications", "expense_applications", 100)


_DAYS_LEFT_LINE = re.compile(r"^支払日 \d+/\d+まであと-?\d+日$")
STATE_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "work",
                          "payroll-todo-state.json")


def dedupe_key(message: str) -> str:
    """前回と同じ内容かを比べるための鍵。日付で変わる行（残り日数）とメンションは除く。"""
    text = message.replace(MENTION, "") if MENTION else message
    lines = [ln for ln in text.splitlines() if not _DAYS_LEFT_LINE.match(ln.strip())]
    body = "\n".join(ln.rstrip() for ln in lines).strip()
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def should_post(state: dict, company_id: int, key: str, days_left: int) -> bool:
    """前回投稿した内容と同じなら投稿しない。支払日の3日前以内は毎日出す。"""
    if days_left <= MENTION_DAYS_BEFORE:
        return True
    last = (state.get("last_posted") or {}).get(str(company_id)) or {}
    return last.get("hash") != key


def load_state(path: str = STATE_JSON) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict, path: str = STATE_JSON) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def main(argv=None) -> int:
    global MENTION
    parser = argparse.ArgumentParser(description="給与の毎月やることの未対応を検知する")
    parser.add_argument("--dry-run", action="store_true", help="Slackへ投稿せず結果を表示する")
    parser.add_argument("--date", help="基準日（YYYY-MM-DD）。省略時は今日")
    parser.add_argument("--force", action="store_true",
                        help="締め日〜支払日の期間外でも実行する（確認用）")
    args = parser.parse_args(argv)

    payroll_check.load_env()
    MENTION = payroll_check.mention_text()
    today = _date(args.date) if args.date else datetime.date.today()
    repo_root = str(payroll_check.REPO_ROOT)
    companies_json = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "companies.json")
    with open(companies_json, "r", encoding="utf-8") as f:
        creds = {c["company_id"]: c for c in json.load(f)["companies"]}

    exit_code = 0
    state = load_state()
    for company in load_rules():
        cid = company["company_id"]
        name = company["name"]
        if not (args.force or in_window(today, company["closing_day"], company["pay_day"],
                                        company.get("pay_month", "next_month"))):
            print(f"{name}: 対象期間外（締め{company['closing_day']}日〜支払{company['pay_day']}日）")
            continue

        entry = creds.get(cid)
        if entry is None:
            print(f"{name}: companies.json に認証情報がありません", file=sys.stderr)
            exit_code = 1
            continue

        pay_date = datetime.date(today.year, today.month, company["pay_day"])
        try:
            path_key = next(k for k in entry if k.endswith("_path"))
            token = freee_api.ensure_access_token(os.path.join(repo_root, entry[path_key]))
            statements = fetch_statements(token, cid, today.year, today.month)
            expenses = fetch_expenses(token, cid)
        except Exception as e:  # noqa: BLE001  1社の失敗で他社を止めない
            print(f"{name}: 取得に失敗しました: {str(e)[:200]}", file=sys.stderr)
            exit_code = 1
            continue

        items = check_company_items(company["rules"], statements, expenses, pay_date, today)
        found = [i["text"] for i in items]
        message = build_message(name, today.year, today.month, pay_date, found, today)
        print(f"{name}: 未対応 {len(found)}件")
        if message is None:
            continue
        if args.dry_run:
            print(message)
            continue
        key = dedupe_key(message)
        if not should_post(state, cid, key, (pay_date - today).days):
            print(f"{name}: 前回と同じ内容のため投稿しません")
            continue
        try:
            url = (os.environ.get(company.get("slack_webhook_env", ""), "")
                   or os.environ.get("GENERAL_SLACK_WEBHOOK_URL", ""))
            if not url:
                raise RuntimeError("投稿先のWebhookが未設定です")
            payroll_check.post_webhook(url, message)
            state.setdefault("last_posted", {})[str(cid)] = {"hash": key, "date": today.isoformat()}
            save_state(state)
            print(f"{name}: 投稿しました")
        except Exception as e:  # noqa: BLE001
            print(f"{name}: 投稿できませんでした: {str(e)[:200]}", file=sys.stderr)
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
