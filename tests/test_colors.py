"""색 인자 해석(src/colors.py) 테스트"""
from src.colors import color_to_index


def test_none_keeps_bylayer():
    assert color_to_index(None) is None


def test_index_range():
    assert color_to_index(1) == 1
    assert color_to_index(256) == 256
    assert color_to_index(0) is None
    assert color_to_index(257) is None
    assert color_to_index("7") == 7


def test_names_in_three_languages():
    assert color_to_index("빨강") == 1
    assert color_to_index("red") == 1          # 대소문자 무시
    assert color_to_index("红色") == 1
    assert color_to_index("light gray") == 9


def test_unknown_name():
    assert color_to_index("없는색") is None
