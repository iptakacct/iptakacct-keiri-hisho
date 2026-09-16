"""テスト用の架空データ。実在のクライアント情報は入れない。"""
from common import JOURNAL_COLUMNS, STAGING_COLUMNS

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
"""

BANK_CSV = (
    "入出金明細,\n"
    "取引日,お引出し,お預入れ,お取引内容,残高\n"
    '2025/04/01,,"100,000",フリコミ カ）テストシヨウジ,"1,100,000"\n'
    '2025/04/05,3300,,テスウリヨウ,"1,096,700"\n'
    '2025/04/05,0,0,ザンダカシヨウカイ,"1,096,700"\n'
)


def write_sources(directory):
    path = directory / "kessan-sources.yaml"
    path.write_text(SOURCES_YAML, encoding="utf-8")
    return path


def write_bank_csv(directory, name="2025-04.csv", text=BANK_CSV):
    path = directory / name
    path.write_text(text, encoding="cp932")
    return path
