"""invoxの弥生形式仕訳データ（`POST invoice_receive/invoice/journal/export` の応答）を、
マネーフォワードの `postJournals` に渡せる形へ変換する。

invoxの出力形式は弥生会計のまま使う方針。
科目・補助科目・税区分の判断はinvoxの仕訳辞書が済ませており、この変換は
「行を仕訳単位にまとめ直し、会社ごとに振り分ける」だけを行う。勘定科目名からMFのIDへの
引き当ては、自前のMF会計API（mf-api-post）で会社名を指定してClaudeが行う（このモジュールは名称のまま出す）。

弥生形式の列（25列・ヘッダー行なし・cp932・CRLF。invox公式「会計ソフトごとの仕訳データの出力内容」）:
  1識別フラグ 2伝票No. 3決算 4取引日付
  5借方勘定科目 6借方補助科目 7借方部門 8借方税区分 9借方金額 10借方税額
  11貸方勘定科目 12貸方補助科目 13貸方部門 14貸方税区分 15貸方金額 16貸方税額
  17摘要 18番号 19期日 20タイプ 21生成元 22仕訳メモ 23付箋1 24付箋2 25調整
"""
from __future__ import annotations

import csv
import io
import json
import os

# 弥生形式の列数。invox側の仕様変更を早期に検知するため、想定と違えばエラーにする。
YAYOI_COLUMN_COUNT = 25
ENCODING = "cp932"
MEMO_PREFIX = "invox:"

# 列インデックス（0始まり）
COL_DATE = 3
COL_DEBIT_ACCOUNT, COL_DEBIT_SUB, COL_DEBIT_DEPT = 4, 5, 6
COL_DEBIT_TAX, COL_DEBIT_AMOUNT, COL_DEBIT_TAX_AMOUNT = 7, 8, 9
COL_CREDIT_ACCOUNT, COL_CREDIT_SUB, COL_CREDIT_DEPT = 10, 11, 12
COL_CREDIT_TAX, COL_CREDIT_AMOUNT, COL_CREDIT_TAX_AMOUNT = 13, 14, 15
COL_REMARK, COL_DUE, COL_MEMO = 16, 18, 21

class YayoiFormatError(Exception):
    """弥生形式データが想定と違う（列数・仕訳メモの欠落など）。"""


def _text(value: str) -> str | None:
    """空欄はNone（prepared-*.jsonの既存表記に合わせる）。前後の空白は落とす。"""
    value = (value or "").strip()
    return value or None


def _amount(value: str) -> int:
    """金額列を整数にする。空欄は0。金額に桁区切りは入らない（実データで確認済み）。"""
    value = (value or "").strip()
    if not value:
        return 0
    try:
        return int(value)
    except ValueError as e:
        raise YayoiFormatError(f"金額として解釈できない値です: {value!r}") from e


def parse_rows(data: bytes) -> list[list[str]]:
    """弥生形式のバイト列を行のリストにする。

    摘要（17列目）はカンマを含むとダブルクォートで囲まれて出力される。
    `split(",")`で分割すると列がずれるため、必ずCSVとして解釈する
    （踏むと摘要の途中切れと期日の欠落を起こす）。
    """
    text = data.decode(ENCODING) if isinstance(data, bytes) else data
    rows = []
    for row in csv.reader(io.StringIO(text, newline="")):
        if not row or all(not cell.strip() for cell in row):
            continue
        if len(row) != YAYOI_COLUMN_COUNT:
            raise YayoiFormatError(
                f"弥生形式は{YAYOI_COLUMN_COUNT}列の想定ですが{len(row)}列でした: {row[:6]}")
        rows.append(row)
    return rows


def _invoice_id(row: list[str]) -> str:
    memo = (row[COL_MEMO] or "").strip()
    if not memo.startswith(MEMO_PREFIX):
        raise YayoiFormatError(
            f"仕訳メモが「{MEMO_PREFIX}<請求書ID>」ではありません（グループ化できない）: {memo!r}")
    return memo[len(MEMO_PREFIX):]


def group_journals(rows: list[list[str]]) -> list[dict]:
    """行を仕訳単位（請求書単位）にまとめる。

    識別フラグ（2100/2101/2110/2111）は複合仕訳で規則性が読み取れずグループ化に使えないため、
    仕訳メモの`invox:IRxxxx`をキーにする。
    借方・貸方は勘定科目が入っている側だけを行として拾う（複合仕訳では片側だけの行が出る）。
    """
    journals: dict[str, dict] = {}
    for row in rows:
        invoice_id = _invoice_id(row)
        journal = journals.get(invoice_id)
        if journal is None:
            journal = journals[invoice_id] = {
                "invoice_id": invoice_id,
                "date": row[COL_DATE].strip(),
                "due": row[COL_DUE].strip(),
                "debits": [],
                "credits": [],
                "remarks": [],
            }
        if _text(row[COL_DEBIT_ACCOUNT]):
            journal["debits"].append({
                "account": _text(row[COL_DEBIT_ACCOUNT]),
                "sub": _text(row[COL_DEBIT_SUB]),
                "dept": _text(row[COL_DEBIT_DEPT]),
                "tax": _text(row[COL_DEBIT_TAX]),
                "amount_taxin": _amount(row[COL_DEBIT_AMOUNT]),
                "tax_amount": _amount(row[COL_DEBIT_TAX_AMOUNT]),
            })
        if _text(row[COL_CREDIT_ACCOUNT]):
            journal["credits"].append({
                "account": _text(row[COL_CREDIT_ACCOUNT]),
                "sub": _text(row[COL_CREDIT_SUB]),
                "dept": _text(row[COL_CREDIT_DEPT]),
                "tax": _text(row[COL_CREDIT_TAX]),
                "amount_taxin": _amount(row[COL_CREDIT_AMOUNT]),
                "tax_amount": _amount(row[COL_CREDIT_TAX_AMOUNT]),
            })
        remark = _text(row[COL_REMARK])
        if remark:
            journal["remarks"].append(remark)
    return list(journals.values())


def load_journals(path: str) -> list[dict]:
    with open(path, "rb") as f:
        return group_journals(parse_rows(f.read()))


def unbalanced(journals: list[dict]) -> list[dict]:
    """貸借が一致しない仕訳を返す。登録前の必須チェック（一致しないものは登録しない）。"""
    out = []
    for journal in journals:
        debit_total = sum(line["amount_taxin"] for line in journal["debits"])
        credit_total = sum(line["amount_taxin"] for line in journal["credits"])
        if debit_total != credit_total:
            out.append({"invoice_id": journal["invoice_id"],
                        "debit_total": debit_total, "credit_total": credit_total})
    return out


def split_by_company(journals: list[dict],
                     department_by_invoice: dict[str, str],
                     department_to_company: dict[str, str]) -> tuple[dict[str, list[dict]], list[dict]]:
    """請求書の部門名から会社を引き当てて振り分ける。

    弥生形式のデータだけでは会社が分からない（借方部門列は仕訳辞書が当たると空になる）ため、
    `invoice/get`の`department_name`を請求書IDごとに渡してもらう。
    `department_to_company`は invox部門名 → 会計システム上の会社名 の対応表
    （accounts.json の該当アカウントの `department_to_company`。部門名は完全一致で判定する。
    「DX」と「DX事業部」のように前方一致する別部門があるため、部分一致にしない）。
    対応表に無い部門・部門が取れなかった請求書は会社を推測せず、第2要素として返す
    （対応表の更新が必要な兆候として報告する）。
    """
    by_company: dict[str, list[dict]] = {}
    unknown: list[dict] = []
    for journal in journals:
        department = department_by_invoice.get(journal["invoice_id"])
        company = department_to_company.get(department) if department else None
        if company is None:
            unknown.append({"invoice_id": journal["invoice_id"], "department_name": department})
            continue
        by_company.setdefault(company, []).append(journal)
    return by_company, unknown


def build_prepared(company: str, source_file: str, journals: list[dict]) -> dict:
    """postJournals投入前の確認用JSON（prepared-<会社名>.json）の中身を作る。"""
    return {
        "company": company,
        "source_file": os.path.basename(source_file),
        "count": len(journals),
        "total_taxin": sum(line["amount_taxin"] for j in journals for line in j["debits"]),
        "note": "金額は税込（内税入力）。postJournalsには amount_taxin をそのまま value に渡す。",
        "journals": journals,
    }


def write_prepared(out_dir: str, company: str, source_file: str, journals: list[dict],
                   stamp: str) -> str:
    """会社ごとの登録前データを`prepared-<会社名>-<実行時刻>.json`に書く。

    **実行時刻を必ずファイル名に入れる**。過去の取り込み結果（何を会計システムに登録したかの記録）を上書きしないため。
    """
    path = os.path.join(out_dir, f"prepared-{company}-{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(build_prepared(company, source_file, journals), f, ensure_ascii=False, indent=1)
    return path
