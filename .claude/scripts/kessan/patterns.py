"""確立済みパターン（インスタンスの context/company/kessan-patterns.yaml）の読み込みと、staging.csv の自動承認。

- AIの判断では自動承認しない。オーナーが承認してファイルに書いたパターンに一致した行だけを 判定=確立済み・承認=済 にする
- 対象は明細の行（取り込み元IDが bank: の行：銀行CSV・通帳・出納帳）だけ。証憑から作った行（receipt:）は
  支払方法の見分けを人が確認するため対象外
- 読み取り信頼度が低い行、要確認理由が空か「相手科目未設定」以外の行、複合仕訳（伝票番号あり）は対象外
"""
from dataclasses import dataclass
from pathlib import Path

import yaml

from bank_import import UNSET_COUNTER_ACCOUNT
from common import STAGING_COLUMNS, KessanError, ensure_writable, parse_amount, project, read_rows, replace_rows
from match import expand_abbreviations, name_in_description

DIRECTIONS = ("入金", "出金")
ESTABLISHED = "確立済み"
MIN_KEYWORD_LENGTH = 2


@dataclass(frozen=True)
class Pattern:
    name: str
    direction: str
    keyword: str
    partner: str
    subject: str
    sub: str
    amount_range: tuple  # (下限, 上限)。指定なしは ()


@dataclass(frozen=True)
class AutoApproveResult:
    approved: int
    conflicts: int  # パターンと科目候補が違う・複数のパターンが一致した行（要確認理由に書く）


def _text(value):
    return "" if value is None else str(value).strip()


def _normalize_keyword(text):
    """パターン検証用の正規化：expand_abbreviations と同じく NFKC + 空白除去。"""
    import re
    import unicodedata
    s = unicodedata.normalize("NFKC", str(text or ""))
    return re.sub(r"\s+", "", s)


def load_patterns(path, accounts):
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise KessanError(f"{path.name} を読めません: {e}") from None
    items = data.get("patterns") if isinstance(data, dict) else None
    if items is None:
        items = []
    if not isinstance(items, list):
        raise KessanError(f"{path.name}: patterns は「- 名前: …」の並び（リスト）で書く")
    patterns, errors = [], []
    for i, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"{i}番目: 「名前: …」などの項目で書く")
            continue
        where = f"{i}番目（{_text(item.get('名前'))}）"
        name, direction = _text(item.get("名前")), _text(item.get("入出金"))
        keyword, partner = _text(item.get("摘要キーワード")), _text(item.get("取引先"))
        subject, sub = _text(item.get("科目")), _text(item.get("補助"))
        if not name:
            errors.append(f"{where}: 名前がありません")
        if direction not in DIRECTIONS:
            errors.append(f"{where}: 入出金は 入金／出金 のどちらかで書く（値: {direction}）")
        if not keyword and not partner:
            errors.append(f"{where}: 摘要キーワード・取引先のどちらかを書く")
        if keyword and len(_normalize_keyword(keyword)) < MIN_KEYWORD_LENGTH:
            errors.append(f"{where}: 摘要キーワード「{keyword}」は短すぎます（最小{MIN_KEYWORD_LENGTH}文字）")
        if partner and len(_normalize_keyword(partner)) < MIN_KEYWORD_LENGTH:
            errors.append(f"{where}: 取引先「{partner}」は短すぎます（最小{MIN_KEYWORD_LENGTH}文字）")
        if subject not in accounts:
            errors.append(f"{where}: 科目マスタに無い科目「{subject}」")
        amount_range = ()
        if item.get("金額の範囲") is not None:
            value = item["金額の範囲"]
            if (not isinstance(value, list) or len(value) != 2
                    or not all(isinstance(v, int) and not isinstance(v, bool) for v in value) or value[0] > value[1]):
                errors.append(f"{where}: 金額の範囲は [下限, 上限] の整数で書く（値: {value}）")
            else:
                amount_range = (value[0], value[1])
        patterns.append(Pattern(name, direction, keyword, partner, subject, sub, amount_range))
    if errors:
        raise KessanError(f"{path.name} の書き方に誤りがあります（自動承認はしていません）:\n" + "\n".join(errors))
    return patterns


def _matches(pattern, row, direction, amount, abbreviations):
    if pattern.direction != direction:
        return False
    if pattern.keyword and not name_in_description(pattern.keyword, row["摘要"], abbreviations):
        return False
    if pattern.partner and not (name_in_description(pattern.partner, row["取引先"], abbreviations)
                                or name_in_description(pattern.partner, row["摘要"], abbreviations)):
        return False
    if pattern.amount_range and not pattern.amount_range[0] <= amount <= pattern.amount_range[1]:
        return False
    return True


def _statement_side(row, payment_accounts):
    """明細側を (入出金, 相手側) で返す。明細の行でなければ None。"""
    debit = (row["借方科目"].strip(), row["借方補助"].strip())
    credit = (row["貸方科目"].strip(), row["貸方補助"].strip())
    if debit in payment_accounts and credit not in payment_accounts:
        return "入金", "貸方", "借方"
    if credit in payment_accounts and debit not in payment_accounts:
        return "出金", "借方", "貸方"
    return None


def auto_approve(year_dir, accounts, patterns, payment_accounts, abbreviations):
    """staging.csv の未承認の明細行をパターンと照合し、一致した行を自動承認する。"""
    year_dir = Path(year_dir)
    staging = read_rows(year_dir / "staging.csv")
    approved = conflicts = 0
    changed = False
    for row in staging:
        reason = row["要確認理由"].strip()
        check_allowed = reason == "" or reason == UNSET_COUNTER_ACCOUNT
        if (row["承認"].strip() == "済" or not row["取り込み元ID"].strip().startswith("bank:") or row["伝票番号"].strip()
                or row["読み取り信頼度"].strip() != "高"
                or not check_allowed):
            continue
        side = _statement_side(row, payment_accounts)
        if side is None:
            continue
        direction, counter, statement = side
        amount = parse_amount(row[f"{statement}金額"], f"staging.csv 取り込み元ID {row['取り込み元ID']}")
        matched = [p for p in patterns if _matches(p, row, direction, amount, abbreviations)]
        if not matched:
            continue
        targets = sorted({(p.subject, p.sub) for p in matched})
        current = (row[f"{counter}科目"].strip(), row[f"{counter}補助"].strip())
        if len(targets) > 1:
            reason = f"確立済みパターンが複数一致（{'・'.join(p.name for p in matched)}）"
        elif current[0] and current != targets[0]:
            reason = f"確立済みパターン「{matched[0].name}」（{' '.join(targets[0]).strip()}）と科目候補が違う"
        else:
            row.update({
                f"{counter}科目": targets[0][0], f"{counter}補助": targets[0][1],
                "判定": ESTABLISHED, "承認": "済", "要確認理由": f"確立済みパターン「{matched[0].name}」",
            })
            approved += 1
            changed = True
            continue
        conflicts += 1
        if row["要確認理由"] != reason:
            row["要確認理由"] = reason
            changed = True
    if changed:
        ensure_writable(year_dir, ["staging.csv"])
        replace_rows(year_dir / "staging.csv", STAGING_COLUMNS, [project(r, STAGING_COLUMNS) for r in staging])
    return AutoApproveResult(approved=approved, conflicts=conflicts)
