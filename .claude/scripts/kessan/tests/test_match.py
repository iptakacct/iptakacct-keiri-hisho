import pytest

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
