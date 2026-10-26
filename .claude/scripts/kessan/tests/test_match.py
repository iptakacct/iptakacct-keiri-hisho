import pytest

from common import KessanError
from common import KessanError
from match import expand_abbreviations, load_abbreviations, name_in_description


@pytest.fixture(scope="module")
def abbreviations():
    return load_abbreviations()


def test_load_abbreviations_reads_legal_and_office_tables(abbreviations):
    assert ("先頭", "カ)", "株式会社") in abbreviations
    assert ("中間", "(カ)", "株式会社") in abbreviations
    assert ("末尾", "(カ", "株式会社") in abbreviations
    assert ("先頭", "イ)", "医療法人") in abbreviations
    assert ("末尾", "(エイ", "営業所") in abbreviations
    assert all(name != "連合会" for _, _, name in abbreviations)


@pytest.mark.parametrize("text, expected", [
    ("フリコミ カ）テストシヨウジ", "フリコミ株式会社テストシヨウジ"),
    ("ﾌﾘｺﾐ ｶ)ﾃｽﾄｼﾖｳｼﾞ", "フリコミ株式会社テストシヨウジ"),
    ("テスト（カ）サンプルシテン", "テスト株式会社サンプルシテン"),
    ("サンプルウンユ（カ", "サンプルウンユ株式会社"),
    ("フリコミ ザイ）テストキキン", "フリコミ財団法人テストキキン"),
])
def test_expand_abbreviations(abbreviations, text, expected):
    assert expand_abbreviations(text, abbreviations) == expected


def test_start_form_needs_word_boundary(abbreviations):
    assert expand_abbreviations("テストカ)サンプル", abbreviations) == "テストカ)サンプル"


@pytest.mark.parametrize("name, description, expected", [
    ("カ）テストシヨウジ", "ﾌﾘｺﾐ ｶ)ﾃｽﾄｼﾖｳｼﾞ", True),
    ("株式会社テストシヨウジ", "フリコミ カ)テストシヨウジ", True),
    ("ベツノシヨウジ", "フリコミ カ)テストシヨウジ", False),
    ("", "フリコミ カ)テストシヨウジ", False),
])
def test_name_in_description_normalizes_both_sides(abbreviations, name, description, expected):
    assert name_in_description(name, description, abbreviations) is expected


def test_missing_abbreviation_file_raises(tmp_path):
    with pytest.raises(KessanError, match="法人格略語の表がありません"):
        load_abbreviations(tmp_path / "none.md")


def test_reading_key_normalizes(abbreviations):
    from match import reading_key
    a = reading_key("ｶ)ﾃｽﾄｼｮｳｼﾞ", abbreviations)
    b = reading_key("株式会社テストショウジ", abbreviations)
    c = reading_key("カ）テストシヨウジ", abbreviations)
    assert a == b == c
    assert reading_key("テスト・サービス", abbreviations) == reading_key("テストサ-ビス", abbreviations)
    assert reading_key("てすとしょうじ", abbreviations) == reading_key("テストシヨウジ", abbreviations)


def test_name_matches_via_payees(abbreviations):
    payees = {"テスト商事": ["テストシヨウジ", "テストシヨウジ（カ"]}
    assert name_in_description("テスト商事", "フリコミ テストシヨウジ", abbreviations, payees) is True
    assert name_in_description("テスト商事", "フリコミ テストシヨウジ", abbreviations) is False
    assert name_in_description("別商事", "フリコミ テストシヨウジ", abbreviations, payees) is False


def test_load_payees_validates_and_merges(tmp_path):
    from match import load_payees
    assert load_payees(tmp_path / "none.yaml") == {}
    path = tmp_path / "p.yaml"
    path.write_text("payees:\n  - {取引先: A社, 名義: [エー]}\n  - {取引先: A社, 名義: [エー, ビー]}\n", encoding="utf-8")
    assert load_payees(path) == {"A社": ["エー", "ビー"]}
    for bad in ("payees: x\n", "payees:\n  - {取引先: A社}\n", "payees:\n  - {取引先: A社, 名義: エー}\n"):
        path.write_text(bad, encoding="utf-8")
        with pytest.raises(KessanError):
            load_payees(path)


def test_load_payees_rejects_unknown_shape(tmp_path):
    from match import add_payee, load_payees
    path = tmp_path / "p.yaml"
    for bad in ("- a\n", "other: 1\n"):
        path.write_text(bad, encoding="utf-8")
        with pytest.raises(KessanError):
            load_payees(path)
        with pytest.raises(KessanError):
            add_payee(path, "A社", "エー")
        assert path.read_text(encoding="utf-8") == bad
