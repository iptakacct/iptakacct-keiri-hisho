"""証憑と明細の突き合わせ。

取引先名の照合では、銀行摘要の法人格略語（`.claude/rules/company-name-abbreviations.md`）を読み替える。
略語の表・摘要・取引先名は、どれも NFKC で正規化してから照合する（全角「カ）」と半角「ｶ)」を同じに扱う）。
"""
import re
import unicodedata
from pathlib import Path

from common import KessanError

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


def name_in_description(name, description, abbreviations):
    """取引先名が摘要に含まれるか（両側を正規化・略語を読み替えてから部分一致）。"""
    needle = expand_abbreviations(name, abbreviations)
    return bool(needle) and needle in expand_abbreviations(description, abbreviations)
