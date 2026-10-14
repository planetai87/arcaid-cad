"""색 인자 해석: 한국어·영어·중국어 색 이름과 ACI 인덱스를 CAD 색상 인덱스로 바꾼다

옛 nlp_processor.py(자연어 명령 해석, 제거함)에서 옮겼다. 그리기 도구의 color 인자는 계속 색 이름을 받는다.
주의: 중국어 색 이름은 기능 키워드라 번역하지 않는다.
"""

from typing import Optional

# 색 이름 → 색상 인덱스(ACI). 옛 버전 CAD는 RGB 색 설정을 지원하지 않으므로 인덱스로 설정한다
COLOR_NAMES = {
    # 기본 색(중국어 키)
    "红色": 1,
    "黄色": 2,
    "绿色": 3,
    "青色": 4,
    "蓝色": 5,
    "洋红色": 6,
    "白色": 7,
    "灰色": 8,
    "浅灰色": 9,
    "黑色": 250,
    "棕色": 251,
    "橙色": 30,
    "紫色": 200,
    "粉色": 221,

    # 기본 색 - 영어 키
    "Red": 1,
    "Yellow": 2,
    "Green": 3,
    "Cyan": 4,
    "Blue": 5,
    "Magenta": 6,
    "White": 7,
    "Gray": 8,
    "Light Gray": 9,
    "Black": 250,
    "Brown": 251,
    "Orange": 30,
    "Purple": 200,
    "Pink": 221,

    # 기본 색 - 한국어 키
    "빨강": 1, "빨간색": 1,
    "노랑": 2, "노란색": 2,
    "초록": 3, "초록색": 3, "녹색": 3,
    "청록": 4, "청록색": 4, "시안": 4,
    "파랑": 5, "파란색": 5,
    "자홍": 6, "자홍색": 6, "마젠타": 6,
    "흰색": 7, "하양": 7,
    "회색": 8,
    "밝은회색": 9, "연회색": 9,
    "검정": 250, "검은색": 250,
    "갈색": 251,
    "주황": 30, "주황색": 30,
    "보라": 200, "보라색": 200,
    "분홍": 221, "분홍색": 221,
}


def color_to_index(color) -> Optional[int]:
    """색 인자(이름 또는 인덱스)를 CAD 색상 인덱스(ACI)로 바꾼다

    한국어·영어·중국어 색 이름과 1-256 정수 인덱스(256=ByLayer)를 받는다.
    알아볼 수 없으면 None을 돌려준다(ByLayer 유지).
    """
    if color is None:
        return None
    if isinstance(color, int):
        return color if 1 <= color <= 256 else None
    if isinstance(color, str):
        text = color.strip()
        # 숫자로만 된 문자열은 인덱스로 처리한다
        if text.isdigit():
            num = int(text)
            return num if 1 <= num <= 256 else None
        # 색 이름을 먼저 정확히 비교하고, 그다음 대소문자를 무시하고 비교한다
        if text in COLOR_NAMES:
            return COLOR_NAMES[text]
        lower = text.lower()
        for name, idx in COLOR_NAMES.items():
            if name.lower() == lower:
                return idx
    return None
