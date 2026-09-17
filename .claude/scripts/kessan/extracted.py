"""AIが資料を読んで書いた読み取り結果ファイル（extracted/*.json）の形式チェックと、通帳のページ検算。

形式に1つでも誤りがあれば、そのファイルは取り込まない（誤りの一覧を返し、AIが直して再実行する）。
"""
import json
from pathlib import Path

from common import parse_date

KINDS = ("通帳", "領収書", "請求書", "出納帳", "読めない")
RECEIPT_KINDS = ("領収書", "請求書")
PAYMENT_METHODS = ("口座", "カード", "現金", "立替", "後払い", "不明")
CONFIDENCE_LEVELS = ("高", "低")


def extracted_files(year_dir):
    """年度フォルダの extracted/*.json（読み取り結果ファイル）をファイル名の順に返す。"""
    return sorted((Path(year_dir) / "extracted").glob("*.json"))


def load_document(path):
    """JSONを読む。(データ, 誤りの一覧) を返す。読めなければデータは None。"""
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8-sig")), []
    except UnicodeDecodeError:
        return None, ["UTF-8で読めません"]
    except json.JSONDecodeError as e:
        return None, [f"JSONとして読めません（{e.lineno}行目 {e.colno}文字目: {e.msg}）"]


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


class _Checker:
    def __init__(self, period):
        self.start, self.end = period
        self.errors = []

    def error(self, where, message):
        self.errors.append(f"{where}: {message}" if where else message)

    def text(self, obj, key, where, required=True, allow_empty=True):
        if key not in obj:
            if required:
                self.error(where, f"「{key}」がありません")
            return ""
        value = obj[key]
        if not isinstance(value, str):
            self.error(where, f"「{key}」は文字列で書く（値: {value!r}）")
            return ""
        if not allow_empty and not value.strip():
            self.error(where, f"「{key}」が空です")
        return value

    def date(self, obj, key, where):
        if key not in obj:
            self.error(where, f"「{key}」がありません")
            return None
        value = obj[key]
        date = parse_date(value) if isinstance(value, str) else None
        if date is None:
            self.error(where, f"「{key}」はYYYY-MM-DDの実在する日付で書く（値: {value!r}）")
        elif not self.start <= date <= self.end:
            self.error(where, f"期間外の日付「{date}」（{self.start}〜{self.end}）")
            return None
        return date

    def amount(self, obj, key, where, required=True, positive=False):
        if key not in obj or obj[key] is None:
            if required:
                self.error(where, f"「{key}」がありません")
            return None
        value = obj[key]
        if not _is_int(value):
            self.error(where, f"「{key}」は円の整数で書く（値: {value!r}）")
            return None
        if positive and value <= 0:
            self.error(where, f"「{key}」は1円以上で書く（値: {value}）")
        elif not positive and value < 0 and key in ("入金", "出金"):
            self.error(where, f"「{key}」にマイナスは書かない（取消・返金は反対側の欄に書く。値: {value}）")
        return value

    def choice(self, obj, key, choices, where):
        value = obj.get(key)
        if value not in choices:
            self.error(where, f"「{key}」は {'／'.join(choices)} のどれかで書く（値: {value!r}）")
        return value

    def account(self, obj, key, accounts, where, required=True, allow_empty=True):
        value = self.text(obj, key, where, required=required, allow_empty=allow_empty)
        if value and value not in accounts:
            self.error(where, f"「{key}」の科目「{value}」が科目マスタにありません")
        return value

    def statement_row(self, row, where, balance_required):
        if not isinstance(row, dict):
            self.error(where, "行の書き方が不正です")
            return
        self.date(row, "日付", where)
        self.text(row, "日付原文", where, required=False)
        deposit = self.amount(row, "入金", where)
        withdrawal = self.amount(row, "出金", where)
        if deposit and withdrawal:
            self.error(where, f"入金と出金の両方に金額があります（入金{deposit} 出金{withdrawal}）")
        elif deposit is not None and withdrawal is not None and deposit == 0 and withdrawal == 0:
            self.error(where, "入金・出金のどちらかに金額が必要です")
        self.text(row, "摘要", where)
        self.amount(row, "残高", where, required=balance_required)

    def rows(self, obj, where, balance_required):
        rows = obj.get("行")
        if not isinstance(rows, list):
            self.error(where, "「行」がありません（配列で書く）")
            return
        for i, row in enumerate(rows, start=1):
            self.statement_row(row, f"{where}行{i}".strip(), balance_required)


def validate_document(data, accounts, account_ids, period, year_dir, payment_accounts):
    """読み取り結果の形式チェック。誤りの一覧（空なら取り込める）を返す。

    accounts: 科目マスタ（dict）、account_ids: kessan-sources.yaml の口座IDの集合、period: (期首日, 期末日)、
    payment_accounts: kessan-sources.yaml の口座・カード・現金の (科目, 補助)。出納帳の科目・補助はこのどれかに限る
    （登録が無いと、出納帳の行が証憑の突き合わせ・二重計上の検知の対象にならず、二重計上に気づけない）
    """
    c = _Checker(period)
    if not isinstance(data, dict):
        return ["JSONの一番外側は {…}（オブジェクト）で書く"]
    source = c.text(data, "資料", "", allow_empty=False)
    if source:
        source_path = Path(source)
        if source_path.is_absolute():
            c.error("", f"「資料」は inbox/ の下に置く相対パスで書く（値: {source}）")
        else:
            resolved_source = (Path(year_dir) / source).resolve()
            inbox_dir = (Path(year_dir) / "inbox").resolve()
            try:
                resolved_source.relative_to(inbox_dir)
                if not resolved_source.is_file():
                    c.error("", f"資料 {source} が年度フォルダにありません")
            except ValueError:
                c.error("", f"「資料」は inbox/ の下に置く相対パスで書く（値: {source}）")
    kind = c.choice(data, "種類", KINDS, "")

    if kind == "読めない":
        c.text(data, "理由", "", allow_empty=False)
    elif kind == "通帳":
        account_id = c.text(data, "口座ID", "", allow_empty=False)
        if account_id and account_id not in account_ids:
            c.error("", f"口座ID「{account_id}」が kessan-sources.yaml にありません（登録済み: {sorted(account_ids)}）")
        pages = data.get("ページ")
        if not isinstance(pages, list) or not pages:
            c.error("", "「ページ」がありません（1ページ以上を配列で書く）")
        else:
            previous = 0
            for i, page in enumerate(pages, start=1):
                if not isinstance(page, dict):
                    c.error(f"{i}番目のページ", "ページの書き方が不正です")
                    continue
                number = page.get("ページ番号")
                if not _is_int(number) or number <= previous:
                    c.error(f"{i}番目のページ", f"「ページ番号」は前のページより大きい整数で書く（値: {number!r}）")
                else:
                    previous = number
                c.amount(page, "繰越残高", f"ページ{number}", required=False)
                c.rows(page, f"ページ{number} ", balance_required=True)
    elif kind == "出納帳":
        subject = c.account(data, "科目", accounts, "", allow_empty=False)
        sub = c.text(data, "補助", "", required=False)
        if subject and (subject.strip(), sub.strip()) not in payment_accounts:
            label = f"{subject}（{sub}）" if sub else subject
            c.error("", f"出納帳の科目・補助「{label}」が kessan-sources.yaml の accounts に登録されていません"
                        "（現金などの口座として登録してから取り込む）")
        c.rows(data, "", balance_required=False)
    elif kind in RECEIPT_KINDS:
        c.date(data, "日付", "")
        c.text(data, "日付原文", "", required=False)
        c.amount(data, "金額", "", positive=True)
        c.text(data, "取引先", "", allow_empty=False)
        c.text(data, "内容", "", allow_empty=False)
        c.choice(data, "支払方法の推定", PAYMENT_METHODS, "")
        c.account(data, "科目候補", accounts, "", allow_empty=False)
        c.text(data, "補助候補", "", required=False)
        c.choice(data, "自信度", CONFIDENCE_LEVELS, "")
        c.text(data, "メモ", "", required=False)
    return c.errors


def check_passbook_pages(pages):
    """通帳のページ検算。残高が連続しないページの「ページ番号」を、ページの順に返す。

    - ページ内：繰越残高（無ければ最初の行から逆算した直前残高）＋入金−出金＝各行の残高
    - ページ間：前ページの最後の残高＝次ページの繰越残高（無ければ最初の行から逆算した直前残高）
    """
    broken = []
    previous_last = None
    for page in pages:
        rows = page["行"]
        opening = page.get("繰越残高")
        if opening is None and rows:
            opening = rows[0]["残高"] - rows[0]["入金"] + rows[0]["出金"]
        ok = opening is None or previous_last is None or opening == previous_last
        balance = opening
        for row in rows:
            if balance + row["入金"] - row["出金"] != row["残高"]:
                ok = False
            balance = row["残高"]
        if not ok:
            broken.append(page["ページ番号"])
        if balance is not None:
            previous_last = balance
    return broken
