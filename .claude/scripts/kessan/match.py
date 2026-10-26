"""証憑と明細の突き合わせ。

取引先名の照合では、銀行摘要の法人格略語（`.claude/rules/company-name-abbreviations.md`）を読み替える。
略語の表・摘要・取引先名は、どれも NFKC で正規化してから照合する（全角「カ）」と半角「ｶ)」を同じに扱う）。
"""
import os
import re
import tempfile
import unicodedata
from datetime import date
from pathlib import Path

import yaml

from common import KessanError, parse_amount, parse_date

ABBREVIATIONS_PATH = Path(__file__).resolve().parents[2] / "rules" / "company-name-abbreviations.md"
ABBREVIATION_SECTIONS = ("## 法人略語", "## 営業所略語")  # カッコ付きの略語だけを使う（事業略語は誤読み替えが多い）
POSITIONS = ("先頭", "中間", "末尾")


def load_abbreviations(path=ABBREVIATIONS_PATH):
    """略語表を [(位置, 正規化した略語, 正式名称)] にする。正式名称が「／」区切りのときは最初の名称を使う。"""
    path = Path(path)
    if not path.exists():
        raise KessanError(f"法人格略語の表がありません: {path.name}")
    entries = []
    section = None
    for text in path.read_text(encoding="utf-8").splitlines():
        if text.startswith("## "):
            section = text.strip()
            continue
        if section not in ABBREVIATION_SECTIONS or not text.startswith("|"):
            continue
        cells = [c.strip() for c in text.strip().strip("|").split("|")]
        if len(cells) < 4 or cells[0] == "名称" or set(cells[0]) <= {"-"}:
            continue
        name = cells[0].split("／")[0]
        for position, cell in zip(POSITIONS, cells[1:4]):
            if cell and cell != "―":
                entries.append((position, unicodedata.normalize("NFKC", cell), name))
    return entries


def expand_abbreviations(text, abbreviations):
    """NFKC で正規化し、法人格略語を正式名称に読み替え、空白を除いた文字列を返す。

    先頭形（カ)）は文字列の先頭か空白の直後、末尾形（(カ）は文字列の末尾か空白の直前にあるときだけ読み替える。
    長い略語から順に読み替える（「(カ)」を「(カ」より先に読み替える）。
    """
    s = unicodedata.normalize("NFKC", str(text or ""))
    for position, abbreviation, name in sorted(abbreviations, key=lambda e: -len(e[1])):
        escaped = re.escape(abbreviation)
        if position == "先頭":
            pattern = rf"(?:^|(?<=\s)){escaped}"
        elif position == "末尾":
            pattern = rf"{escaped}(?=\s|$)"
        else:
            pattern = escaped
        s = re.sub(pattern, name, s)
    return re.sub(r"\s+", "", s)


_SMALL_KANA = str.maketrans("ァィゥェォッャュョヮ", "アイウエオツヤユヨワ")


def reading_key(text, abbreviations):
    """照合用の正規形。expand_abbreviations の結果を、読みの揺れを吸収した形にする。

    ひらがな→カタカナ、小書きカナ→並字（ショ→シヨ）、長音記号・中黒・記号の除去。
    法人格略語の読み替えは外さない（両側を同じ関数に通すので、カ）と株式会社は同じ形になる）。
    """
    s = expand_abbreviations(text, abbreviations)
    s = "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)
    s = s.translate(_SMALL_KANA)
    return "".join(c for c in s if c != "ー" and unicodedata.category(c)[0] not in ("P", "S", "Z", "C"))


MIN_KEY_LENGTH = 2  # 照合に使う文字列の最小の長さ（reading_key 後）。短いと無関係な摘要に当たる


def _check_key_length(text, label, where, abbreviations):
    if len(reading_key(text, abbreviations)) < MIN_KEY_LENGTH:
        raise KessanError(f"{where}: {label}「{text}」は短すぎます（記号・長音を除いて最小{MIN_KEY_LENGTH}文字）")


def load_payees(path):
    """支払先の対応表（context/company/kessan-payees.yaml）を {取引先: [名義...]} にする。無ければ空。"""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise KessanError(f"{path.name} を読めません: {e}") from None
    if not isinstance(data, dict) or set(data) - {"payees"}:
        raise KessanError(f"{path.name} は「payees:」の一覧だけで書いてください（上書きしないよう止めました）")
    items = data.get("payees")
    if items is None:
        items = []
    if not isinstance(items, list):
        raise KessanError(f"{path.name} の payees は一覧で書いてください")
    payees = {}
    abbreviations = load_abbreviations()
    for i, item in enumerate(items, 1):
        name = item.get("取引先") if isinstance(item, dict) else None
        kana = item.get("名義") if isinstance(item, dict) else None
        if (not isinstance(name, str) or not name.strip() or not isinstance(kana, list)
                or not all(isinstance(k, str) and k.strip() for k in kana)):
            raise KessanError(f"{path.name} の{i}件目の書き方に誤りがあります（取引先: 文字列、名義: 文字列の一覧）")
        _check_key_length(name, "取引先", f"{path.name} の{i}件目", abbreviations)
        for k in kana:
            _check_key_length(k, "名義", f"{path.name} の{i}件目", abbreviations)
        merged = payees.setdefault(name.strip(), [])
        merged.extend(k.strip() for k in kana if k.strip() not in merged)
    return payees


def add_payee(path, name, kana):
    """対応表に 取引先・名義 の組を1件足す（同じ組があれば何もしない。取引先が既にあれば名義を追記）。足したら True。"""
    path = Path(path)
    name, kana = str(name or "").strip(), str(kana or "").strip()
    if not name or not kana:
        raise KessanError("--name（取引先）と --kana（名義）は空にできません")
    abbreviations = load_abbreviations()
    _check_key_length(name, "取引先", "add-payee", abbreviations)
    _check_key_length(kana, "名義", "add-payee", abbreviations)
    payees = load_payees(path)  # 書き方が誤っているファイルは、ここで止めて上書きしない
    if kana in payees.get(name, []):
        return False
    payees.setdefault(name, []).append(kana)
    data = {"payees": [{"取引先": n, "名義": k} for n, k in payees.items()]}
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=None)
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8", newline=chr(10)) as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError as e:
        if tmp:
            Path(tmp).unlink(missing_ok=True)
        raise KessanError(f"{path.name} に書けません（開いているアプリを閉じてください）: {e}") from None
    return True


def name_in_description(name, description, abbreviations, payees=None):
    """取引先名が摘要に含まれるか（reading_key で両側を揃えてから部分一致）。

    payees（支払先の対応表）に name の名義があれば、その名義のどれかが含まれても一致とする。
    """
    haystack = reading_key(description, abbreviations)
    key = reading_key(name, abbreviations)
    names = [name]
    for payee, kana in (payees or {}).items():
        if reading_key(payee, abbreviations) == key:
            names += kana
    needles = (reading_key(n, abbreviations) for n in names)
    return any(len(needle) >= MIN_KEY_LENGTH and needle in haystack for needle in needles)


# --- 証憑と明細行の突き合わせ ---

MATCH_WINDOW_DAYS = 7  # 証憑の日付の前後7日（両端を含む）
RECEIPT_DEFAULTS = ("立替", "現金")
NO_MATCH = "明細に該当なし"
NO_MATCH_BANK = "明細に該当なし（口座・カードの明細が未取り込みでないか確認）"
MATCHED_RECEIPT = "証憑と一致"
SUSPECTED_DUPLICATE_RECEIPT = "証憑の重複の疑い"


def payment_accounts(sources):
    """kessan-sources.yaml の accounts に登録した口座・カード・現金の (科目, 補助)。"""
    return {(str(a.get("科目", "")).strip(), str(a.get("補助", "") or "").strip()) for a in sources.get("accounts") or []}


def receipt_default(sources):
    """領収書の既定の支払方法（kessan-sources.yaml の receipt_default）。未設定は空文字。"""
    value = sources.get("receipt_default") or ""
    if value not in ("",) + RECEIPT_DEFAULTS:
        raise KessanError(f"kessan-sources.yaml の receipt_default は {'／'.join(RECEIPT_DEFAULTS)} のどちらか（未設定なら空欄）: {value}")
    return value


def find_candidates(lines, receipt, accounts_for_payment, attached):
    """証憑と突き合わせる明細行の取り込み元IDを、見つかった順に返す。

    条件：貸方が支払口座（kessan-sources.yaml の口座・カード・現金）、貸方金額＝証憑の金額、
    日付が証憑の日付の前後7日以内、取り込み元IDがあり証憑由来（receipt:）ではない、まだ証憑が付いていない。
    """
    target = date.fromisoformat(receipt["日付"])
    found = []
    for r in lines:
        source_id = r["取り込み元ID"].strip()
        if not source_id or source_id.startswith("receipt:") or source_id in attached or source_id in found:
            continue
        if (r["貸方科目"].strip(), r["貸方補助"].strip()) not in accounts_for_payment:
            continue
        if parse_amount(r["貸方金額"], f"{r['_file']} {r['伝票番号'] or r['日付']}") != receipt["金額"]:
            continue
        line_date = parse_date(r["日付"])
        if line_date is None or abs((date.fromisoformat(line_date) - target).days) > MATCH_WINDOW_DAYS:
            continue
        found.append(source_id)
    return found


def credit_for_new_entry(kind, method, default, accounts_for_payment):
    """明細に該当が無い証憑から新しい仕訳を作るときの貸方 (科目, 補助, 要確認理由)。

    後払いは None（仕訳を作らず「未払候補」にする）。請求書（kind=請求書）で支払方法が不明の場合も
    同様に None にする（`receipt_default` を適用しない。銀行振込での支払と二重計上になりやすいため）。
    """
    if method == "後払い" or (kind == "請求書" and method == "不明"):
        return None
    if method in ("口座", "カード"):
        return "", "", NO_MATCH_BANK
    chosen = method if method in RECEIPT_DEFAULTS else default
    if chosen == "立替":
        return "役員借入金", "", NO_MATCH
    if chosen == "現金":
        cash = sorted(sub for name, sub in accounts_for_payment if name == "現金")
        return "現金", cash[0] if len(cash) == 1 else "", NO_MATCH
    return "", "", NO_MATCH
