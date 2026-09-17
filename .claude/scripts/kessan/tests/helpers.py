"""テスト用の架空データ。実在のクライアント情報は入れない。"""
from common import EVIDENCE_COLUMNS, JOURNAL_COLUMNS, STAGING_COLUMNS

SOURCES_YAML = """\
formats:
  sample-bank:
    encoding: cp932
    header_row: 2
    date_format: "%Y/%m/%d"
    columns:
      日付: 取引日
      出金: お引出し
      入金: お預入れ
      摘要: お取引内容
      残高: 残高
accounts:
  - id: main
    format: sample-bank
    科目: 普通預金
    補助: サンプル銀行
  - id: cash
    科目: 現金
"""

BANK_CSV = (
    "入出金明細,\n"
    "取引日,お引出し,お預入れ,お取引内容,残高\n"
    '2025/04/01,,"100,000",フリコミ カ）テストシヨウジ,"1,100,000"\n'
    '2025/04/05,3300,,テスウリヨウ,"1,096,700"\n'
    '2025/04/05,0,0,ザンダカシヨウカイ,"1,096,700"\n'
)


def write_sources(directory, extra=""):
    """extra：SOURCES_YAML の末尾に足すYAML（口座の追加・receipt_default など）。"""
    path = directory / "kessan-sources.yaml"
    path.write_text(SOURCES_YAML + extra, encoding="utf-8")
    return path


def write_bank_csv(directory, name="2025-04.csv", text=BANK_CSV):
    path = directory / name
    path.write_text(text, encoding="cp932")
    return path


def staging_row(**values):
    row = dict.fromkeys(STAGING_COLUMNS, "")
    row.update(values)
    return row


def evidence_row(**values):
    row = dict.fromkeys(EVIDENCE_COLUMNS, "")
    row.update(values)
    return row


def line(no, date, debit=None, credit=None, **extra):
    row = dict.fromkeys(JOURNAL_COLUMNS, "")
    row.update({"伝票番号": no, "日付": date})
    row.update(extra)
    if debit:
        row.update({"借方科目": debit[0], "借方補助": debit[1], "借方金額": str(debit[2])})
    if credit:
        row.update({"貸方科目": credit[0], "貸方補助": credit[1], "貸方金額": str(credit[2])})
    return row


# --- 読み取り結果ファイル（extracted/*.json）の架空データ ---

def passbook():
    """BANK_CSV と同じ取引（1〜2行目）を含む、2ページの通帳。"""
    return {
        "資料": "inbox/通帳-2025-04.pdf",
        "種類": "通帳",
        "口座ID": "main",
        "ページ": [
            {"ページ番号": 1, "繰越残高": 1000000, "行": [
                {"日付": "2025-04-01", "日付原文": "07-04-01", "入金": 100000, "出金": 0,
                 "摘要": "フリコミ カ）テストシヨウジ", "残高": 1100000},
                {"日付": "2025-04-05", "日付原文": "07-04-05", "入金": 0, "出金": 3300, "摘要": "テスウリヨウ", "残高": 1096700},
            ]},
            {"ページ番号": 2, "繰越残高": 1096700, "行": [
                {"日付": "2025-04-10", "日付原文": "07-04-10", "入金": 0, "出金": 5500, "摘要": "カード テストブングテン", "残高": 1091200},
            ]},
        ],
    }


def receipt(**overrides):
    data = {
        "資料": "inbox/領収書-0001.jpg",
        "種類": "領収書",
        "日付": "2025-04-10", "日付原文": "2025年4月10日",
        "金額": 5500, "取引先": "テスト文具店", "内容": "文房具",
        "支払方法の推定": "不明",
        "科目候補": "消耗品費", "補助候補": "",
        "自信度": "高", "メモ": "",
    }
    data.update(overrides)
    return data


def cash_book():
    return {
        "資料": "inbox/出納帳.xlsx", "種類": "出納帳", "科目": "現金", "補助": "",
        "行": [
            {"日付": "2025-04-03", "入金": 50000, "出金": 0, "摘要": "預金から引出", "残高": 50000},
            {"日付": "2025-04-04", "入金": 0, "出金": 1200, "摘要": "切手", "残高": 48800},
        ],
    }


def write_document(year_dir, data, name=None):
    """資料の原本（中身は空）と読み取り結果JSONを年度フォルダに置き、JSONのパスを返す。"""
    import json
    from pathlib import Path
    source = year_dir / data["資料"]
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"")
    path = year_dir / "extracted" / (name or Path(data["資料"]).name + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
