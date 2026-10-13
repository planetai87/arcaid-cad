import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import os
import os.path

# 설정 싱글턴을 공통으로 쓴다(모듈마다 config.json을 다시 읽지 않는다)
try:
    from .config import get_config
except ImportError:
    from config import get_config

config = get_config()

logger = logging.getLogger('nlp_processor')

# 주의: 이 파일의 문자열 리터럴(중국어 키워드, 정규식, 메시지)은 명령 해석에 쓰이므로 번역하지 않는다.

class NLPProcessor:
    """자연어 처리기: 사용자 지시를 해석해 CAD 작업으로 바꾼다"""

    def __init__(self):
        """
        NLP 처리기를 초기화한다
        """

        self.logger = logging.getLogger(__name__)
        self.logger.info("自然语言处理器已初始化")

        # 옛 버전 CAD는 RGB 색 설정을 지원하지 않으므로, 호환성을 위해 인덱스로 색을 설정한다

        # 색 이름 → 색상 인덱스 대응표
        self.color_rgb_map = {
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

        # 도형 키워드 대응표
        self.shape_keywords = {
            # 기본 도형
            "直线": "line", "线": "line",
            "圆": "circle", "圆形": "circle",
            "弧": "arc", "圆弧": "arc",
            "矩形": "rectangle", "方形": "rectangle", "正方形": "square",
            "多段线": "polyline", "折线": "polyline",
            "文本": "text", "文字": "text",
            "标注": "dimension", "尺寸标注": "dimension",

        }

        # 동작 키워드 대응표
        self.action_keywords = {
            # 기본 동작
            "画": "draw", "绘制": "draw", "创建": "draw", "添加": "draw",
            "修改": "modify", "调整": "modify", "改变": "modify",
            "移动": "move", "旋转": "rotate", "缩放": "scale",
            "放大": "scale_up", "缩小": "scale_down",
            "删除": "erase", "擦除": "erase", "移除": "erase",
            "保存": "save",

            # 전문 작업
            "标注": "dimension",
            "填充": "hatch",
            "创建图层": "create_layer",
            "切换图层": "change_layer"
        }


    # 좌표점 매칭: (x,y,z) / (x,y) / x,y,z / x,y
    # 다시 쓰려고 클래스 속성으로 올렸다: 반지름 같은 숫자 인자를 해석하기 전에 좌표 부분을 먼저 빼야 한다.
    # 그러지 않으면 원 중심 좌표를 반지름으로 잘못 읽는다
    _COORD_PATTERN = re.compile(
        r'\(?\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)(?:\s*,\s*(-?\d+\.?\d*))?\s*\)?')

    def color_to_index(self, color) -> Optional[int]:
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
            if text in self.color_rgb_map:
                return self.color_rgb_map[text]
            lower = text.lower()
            for name, idx in self.color_rgb_map.items():
                if name.lower() == lower:
                    return idx
        return None

    def extract_color_from_command(self, command: str) -> Optional[int]:
        # 색 정보가 없으면 None을 돌려 객체의 ByLayer 색을 유지한다
        if command is None:
            return None

        try:
            num = int(command)
            if num >= 1 and num <= 255:
                return num
        except:
            pass

        # 명령을 소문자로 바꾼다
        command = command.lower()

        # 색 이름을 찾아본다(긴 이름 우선: "浅灰色"이 "灰色"에 먼저 걸리지 않게)
        for color_name in sorted(self.color_rgb_map.keys(), key=len, reverse=True):
            if color_name.lower() in command:
                return self.color_rgb_map[color_name]

        # 색 표현을 찾아본다(예: "淡蓝色")
        color_pattern = r'([深浅淡]?[a-zA-Z\u4e00-\u9fa5]+色)'
        color_matches = re.findall(color_pattern, command)

        for color_match in color_matches:
            # 알려진 색 이름인지 확인한다
            if color_match in self.color_rgb_map:
                return self.color_rgb_map[color_match]

        # 색 정보를 찾지 못하면 None을 돌려준다(ByLayer 유지, ByLayer 의미를 덮어쓰지 않기 위해)
        return None

    def process_command(self, command: str) -> Dict[str, Any]:
        """자연어 명령을 처리하고 결과를 돌려준다"""
        self.logger.info(f"处理命令: {command}")

        # 명령 해석
        parsed_command = self.parse_command(command)

        # 해석 결과를 돌려준다
        return parsed_command

    def parse_command(self, command: str) -> Dict[str, Any]:
        """자연어 명령을 해석해 CAD 작업 인자로 바꾼다"""
        self.logger.info(f"解析命令: {command}")

        # 명령을 소문자로 바꾸고 앞뒤 공백을 없앤다
        command = command.lower().strip()

        # 명령 종류를 알아본다
        command_type = self._identify_command_type(command)
        self.logger.debug(f"识别到的命令类型: {command_type}")

        # 명령 종류에 따라 처리 함수로 나눈다
        if command_type == "draw_line":
            return self._parse_draw_line(command)
        elif command_type == "draw_circle":
            return self._parse_draw_circle(command)
        elif command_type == "draw_arc":
            return self._parse_draw_arc(command)
        elif command_type == "draw_rectangle":
            return self._parse_draw_rectangle(command)
        elif command_type == "draw_polyline":
            return self._parse_draw_polyline(command)
        elif command_type == "draw_text":
            return self._parse_draw_text(command)
        elif command_type == "draw_hatch":
            return self._parse_draw_hatch(command)
        elif command_type == "add_dimension":
            return self._parse_add_dimension(command)
        elif command_type == "create_layer":
            return self._parse_create_layer(command)
        elif command_type == "save":
            return self._parse_save(command)
        else:
            # 기본으로 오류 결과를 돌려준다
            return {
                "type": "unknown",
                "error": "无法识别的命令类型",
                "original_command": command
            }

    def _identify_command_type(self, command: str) -> str:
        """명령 종류를 알아낸다"""
        # 동작 종류 확인
        for action, action_type in self.action_keywords.items():
            if action in command:
                # 기본 도형 처리
                for shape, shape_type in self.shape_keywords.items():
                    if shape in command:
                        # "标注层" 안의 "标注"는 레이어 이름의 일부다(예: "创建一个名为标注层的图层").
                        # 치수 동작의 도형 단어로 보면 add_dimension으로 잘못 알아본다
                        if shape == "标注" and "标注层" in command:
                            continue
                        if action_type == "draw":
                            if shape_type == "line":
                                return "draw_line"
                            elif shape_type == "circle":
                                return "draw_circle"
                            elif shape_type == "arc":
                                return "draw_arc"
                            elif shape_type in ["rectangle", "square"]:
                                return "draw_rectangle"
                            elif shape_type == "polyline":
                                return "draw_polyline"
                            elif shape_type == "text":
                                return "draw_text"
                            elif shape_type == "dimension":
                                return "add_dimension"

        # 레이어 만들기 명령인지 확인한다(동작 단어는 _parse_create_layer가 알아보는 범위와 같아야 한다)
        if "图层" in command and any(action in command for action in ["创建", "新建", "添加", "建立", "生成"]):
            return "create_layer"

        # 치수 명령인지 확인한다
        if "标注" in command:
            return "add_dimension"

        # 해치 명령인지 확인한다("填充" 동작은 동작 반복문에서 draw 분기에 걸리지 않아 따로 알아본다)
        if "填充" in command:
            return "draw_hatch"

        if "保存" in command:
            return "save"

        # 알아볼 수 없으면 unknown을 돌려준다
        return "unknown"

    def _extract_coordinates(self, text: str) -> List[Tuple[float, float, float]]:
        """문자열에서 좌표점을 뽑는다"""
        coordinates = []
        for match in self._COORD_PATTERN.finditer(text):
            x = float(match.group(1))
            y = float(match.group(2))
            z = float(match.group(3)) if match.group(3) else 0.0
            coordinates.append((x, y, z))

        return coordinates

    def _extract_numbers(self, text: str) -> List[float]:
        """문자열에서 숫자를 뽑는다"""
        pattern = r'(-?\d+\.?\d*)'
        matches = re.findall(pattern, text)
        return [float(match) for match in matches]

    def _parse_draw_line(self, command: str) -> Dict[str, Any]:
        """선 그리기 명령을 해석한다"""
        # 좌표를 뽑아 본다
        coordinates = self._extract_coordinates(command)

        if len(coordinates) >= 2:
            # 좌표가 두 개 이상이면 앞의 두 개를 시작점과 끝점으로 쓴다
            return {
                "type": "draw_line",
                "start_point": coordinates[0],
                "end_point": coordinates[1]
            }
        else:
            # 좌표가 모자라면 기본값을 쓴다
            # 필요하면 여기서 기본 시작점과 끝점을 정할 수 있다
            return {
                "type": "draw_line",
                "start_point": (0, 0, 0),
                "end_point": (100, 100, 0),
                "note": "使用默认坐标，因为命令中未提供足够的坐标信息"
            }

    def _parse_draw_circle(self, command: str) -> Dict[str, Any]:
        """원 그리기 명령을 해석한다"""
        # 좌표와 반지름을 뽑아 본다
        coordinates = self._extract_coordinates(command)
        # 좌표 부분을 빼고 숫자를 뽑아, 원 중심 좌표를 반지름으로 잘못 읽지 않게 한다
        numbers = self._extract_numbers(self._COORD_PATTERN.sub(' ', command))

        # 반지름을 뽑는다
        radius = None
        radius_pattern = r'(?:半径|r|radius)[^\d]*?(-?\d+\.?\d*)'
        radius_match = re.search(radius_pattern, command, re.IGNORECASE)
        if radius_match:
            radius = float(radius_match.group(1))
        elif len(numbers) > 0:
            # 반지름을 명시하지 않았으면 처음 찾은 숫자를 반지름으로 쓴다
            radius = numbers[0]
        else:
            # 기본 반지름
            radius = 50.0

        # 중심점을 뽑는다
        center = None
        if len(coordinates) > 0:
            center = coordinates[0]
        else:
            # 기본 중심점
            center = (0, 0, 0)

        return {
            "type": "draw_circle",
            "center": center,
            "radius": radius
        }

    def _parse_draw_arc(self, command: str) -> Dict[str, Any]:
        """호 그리기 명령을 해석한다"""
        # 좌표, 반지름, 각도를 뽑아 본다
        coordinates = self._extract_coordinates(command)
        # 좌표 부분을 빼고 숫자를 뽑아, 원 중심 좌표를 반지름으로 잘못 읽지 않게 한다
        numbers = self._extract_numbers(self._COORD_PATTERN.sub(' ', command))

        # 중심점을 뽑는다
        center = None
        if len(coordinates) > 0:
            center = coordinates[0]
        else:
            # 기본 중심점
            center = (0, 0, 0)

        # 반지름을 뽑는다
        radius = None
        radius_pattern = r'(?:半径|r|radius)[^\d]*?(-?\d+\.?\d*)'
        radius_match = re.search(radius_pattern, command, re.IGNORECASE)
        if radius_match:
            radius = float(radius_match.group(1))
        elif len(numbers) > 0:
            # 반지름을 명시하지 않았으면 처음 찾은 숫자를 반지름으로 쓴다
            radius = numbers[0]
        else:
            # 기본 반지름
            radius = 50.0

        # 시작 각도와 끝 각도를 뽑는다
        start_angle = 0.0
        end_angle = 90.0

        start_angle_pattern = r'(?:起始角度|start angle)[^\d]*?(-?\d+\.?\d*)'
        start_angle_match = re.search(start_angle_pattern, command, re.IGNORECASE)
        if start_angle_match:
            start_angle = float(start_angle_match.group(1))

        end_angle_pattern = r'(?:结束角度|end angle)[^\d]*?(-?\d+\.?\d*)'
        end_angle_match = re.search(end_angle_pattern, command, re.IGNORECASE)
        if end_angle_match:
            end_angle = float(end_angle_match.group(1))

        # 필요한 인자에 모두 값이 있게 한다
        if center is None:
            center = (0, 0, 0)
        if radius is None:
            radius = 50.0
        if start_angle is None:
            start_angle = 0.0
        if end_angle is None:
            end_angle = 90.0

        self.logger.debug(f"解析圆弧命令结果: 中心点={center}, 半径={radius}, 起始角度={start_angle}, 结束角度={end_angle}")

        return {
            "type": "draw_arc",
            "center": center,
            "radius": radius,
            "start_angle": start_angle,
            "end_angle": end_angle
        }

    def _parse_draw_rectangle(self, command: str) -> Dict[str, Any]:
        """사각형 그리기 명령을 해석한다"""
        # 좌표를 뽑아 본다
        coordinates = self._extract_coordinates(command)

        if len(coordinates) >= 2:
            # 좌표가 두 개 이상이면 앞의 두 개를 대각점으로 쓴다
            return {
                "type": "draw_rectangle",
                "corner1": coordinates[0],
                "corner2": coordinates[1]
            }
        else:
            # 좌표가 모자라면 너비와 높이를 뽑아 본다
            width = 100.0
            height = 100.0

            width_pattern = r'(?:宽度|width)[^\d]*?(-?\d+\.?\d*)'
            width_match = re.search(width_pattern, command, re.IGNORECASE)
            if width_match:
                width = float(width_match.group(1))

            height_pattern = r'(?:高度|height)[^\d]*?(-?\d+\.?\d*)'
            height_match = re.search(height_pattern, command, re.IGNORECASE)
            if height_match:
                height = float(height_match.group(1))

            # 좌표가 하나 있으면 그것을 시작점으로 쓴다
            if len(coordinates) == 1:
                corner1 = coordinates[0]
                corner2 = (corner1[0] + width, corner1[1] + height, corner1[2])
            else:
                # 기본 시작점과 끝점
                corner1 = (0, 0, 0)
                corner2 = (width, height, 0)

            return {
                "type": "draw_rectangle",
                "corner1": corner1,
                "corner2": corner2
            }

    def _parse_draw_polyline(self, command: str) -> Dict[str, Any]:
        """폴리선 그리기 명령을 해석한다"""
        # 좌표를 뽑아 본다
        coordinates = self._extract_coordinates(command)

        # 닫아야 하는지 확인한다
        closed = "闭合" in command or "封闭" in command

        if len(coordinates) >= 2:
            # 좌표가 두 개 이상이면 폴리선의 점으로 쓴다
            return {
                "type": "draw_polyline",
                "points": coordinates,
                "closed": closed
            }
        else:
            # 좌표가 모자라면 오류를 돌려준다
            return {
                "type": "error",
                "message": "绘制多段线需要至少两个坐标点"
            }

    def _parse_draw_text(self, command: str) -> Dict[str, Any]:
        """문자 넣기 명령을 해석한다"""
        # 좌표를 뽑아 본다
        coordinates = self._extract_coordinates(command)

        # 문자 내용을 뽑는다
        # 주의: 문자 클래스[...]가 아니라 그룹(?:...)을 써야 한다. 문자 클래스는 "文本内容|text|内容"을
        # 글자 하나하나의 집합으로 쪼개 아무 글자 하나와 콜론에 걸리게 된다
        text_pattern = r'(?:文本内容|text|内容)\s*[：:]\s*[\"\'](.*?)[\"\']'
        text_match = re.search(text_pattern, command)

        text = ""
        if text_match:
            text = text_match.group(1)
        else:
            # 따옴표 안의 내용을 문자로 뽑아 본다
            quote_pattern = r'[\"\'](.*?)[\"\']'
            quote_match = re.search(quote_pattern, command)
            if quote_match:
                text = quote_match.group(1)
            else:
                # 따옴표 안의 내용이 없으면 기본 문자를 쓴다
                text = "示例文本"

        # 문자 높이를 뽑는다
        height = 2.5  # 기본 높이
        height_pattern = r'(?:高度|height)[^\d]*?(-?\d+\.?\d*)'
        height_match = re.search(height_pattern, command, re.IGNORECASE)
        if height_match:
            height = float(height_match.group(1))

        # 회전 각도를 뽑는다
        rotation = 0.0  # 기본 각도
        rotation_pattern = r'(?:旋转|角度|rotation)[^\d]*?(-?\d+\.?\d*)'
        rotation_match = re.search(rotation_pattern, command, re.IGNORECASE)
        if rotation_match:
            rotation = float(rotation_match.group(1))

        # 삽입점을 뽑는다
        position = None
        if len(coordinates) > 0:
            position = coordinates[0]
        else:
            # 기본 삽입점
            position = (0, 0, 0)

        return {
            "type": "draw_text",
            "position": position,
            "text": text,
            "height": height,
            "rotation": rotation
        }

    def _parse_draw_hatch(self, command: str) -> Dict[str, Any]:
        """해치 그리기 명령을 해석한다"""
        # 좌표 점 목록을 뽑아 본다
        coordinates = self._extract_coordinates(command)

        # 해치 패턴 이름을 뽑는다
        pattern_name = "SOLID"  # 기본은 솔리드 채우기
        pattern_patterns = [
            r'(?:图案|pattern)[^\w]*?["\'](.*?)["\']',
            r'(?:图案|pattern)[^\w]*?(\w+)'
        ]

        for pattern in pattern_patterns:
            pattern_match = re.search(pattern, command, re.IGNORECASE)
            if pattern_match:
                pattern_name = pattern_match.group(1).upper()
                break

        # 해치 축척을 뽑는다
        scale = 1.0  # 기본 축척
        scale_pattern = r'(?:比例|缩放|scale)[^\d]*?(\d+\.?\d*)'
        scale_match = re.search(scale_pattern, command, re.IGNORECASE)
        if scale_match:
            scale = float(scale_match.group(1))

        # 해치 경계를 정할 점이 충분한지 확인한다
        if len(coordinates) >= 3:
            self.logger.debug(f"解析填充命令结果: 点集={coordinates}, 图案={pattern_name}, 比例={scale}")
            return {
                "type": "draw_hatch",
                "points": coordinates,
                "pattern_name": pattern_name,
                "scale": scale
            }
        else:
            # 점이 모자라면 오류 정보를 돌려준다
            self.logger.warning("填充命令解析失败: 需要至少3个点来定义填充边界")
            return {
                "type": "error",
                "message": "绘制填充需要至少3个点来定义边界"
            }


    def _parse_add_dimension(self, command: str) -> Dict[str, Any]:
        """선형 치수 넣기 명령을 해석한다(앞의 좌표 두 개를 치수 시작점과 끝점으로 쓴다)"""
        coordinates = self._extract_coordinates(command)
        if len(coordinates) < 2:
            return {
                "type": "error",
                "message": "添加标注需要两个坐标点（起点和终点）"
            }
        return {
            "type": "add_dimension",
            "start_point": coordinates[0],
            "end_point": coordinates[1]
        }

    def _parse_create_layer(self, command: str) -> Dict[str, Any]:
        """레이어 만들기 명령을 해석한다

        여러 어순을 지원한다: "名为WALL的图层", "创建WALL图层", "创建图层WALL",
        따옴표 형식 "创建图层'墙体'"
        """
        # 따옴표 안의 레이어 이름을 먼저 뽑는다
        quote_match = re.search(r'["\'](.*?)["\']', command)
        if quote_match:
            return {"type": "create_layer", "layer_name": quote_match.group(1)}

        # "名为/叫做/名称为 X": 이름이 키워드 뒤에 온다(예: "创建一个名为WALL的图层")
        # 캡처 그룹은 반드시 최소 매칭이어야 하고 "的/图层" 앞에서 끝나야 한다.
        # 그러지 않으면 탐욕 매칭이 "WALL的图层" 전체를 레이어 이름으로 잘못 잡는다
        # ("的"와 "图层" 사이에 "新" 한 글자를 허용: "叫做结构层的新图层")
        m = re.search(r'(?:名为|叫做|名称为|命名为)\s*([\w\u4e00-\u9fa5-]+?)(?=\s*(?:的)?(?:新)?图层|\s*$)', command)
        if m:
            return {"type": "create_layer", "layer_name": m.group(1)}

        # "X图层": 이름이 "图层" 바로 앞에 온다(예: "创建墙体图层")
        m = re.search(r'(?:创建|新建|添加|建立|生成)\s*(?:一个|一个新|新的)?\s*([\w\u4e00-\u9fa5-]+)\s*(?:的)?\s*(?:新)?图层', command)
        if m:
            return {"type": "create_layer", "layer_name": m.group(1)}

        # "图层X": 이름이 "图层" 뒤에 온다(예: "创建图层WALL")
        m = re.search(r'图层\s*(?:名为|叫做|名称为|:|：)?\s*([\w\u4e00-\u9fa5-]+)', command)
        if m:
            return {"type": "create_layer", "layer_name": m.group(1)}

        # 레이어 이름을 알아보지 못함
        return {"type": "create_layer", "layer_name": None}

    def _parse_save(self, command: str) -> Dict[str, Any]:
        """저장 명령을 해석한다"""
        # 파일 경로를 뽑아 본다
        path_pattern = r'(?:路径|保存到|path)[^\w]*?[\"\'](.*?)[\"\']'
        path_match = re.search(path_pattern, command, re.IGNORECASE)

        if path_match:
            file_path = path_match.group(1)
        else:
            # 기본 파일 경로
            file_path = os.path.join(config["output"]["directory"], config["output"]["default_filename"])

        return {
            "type": "save",
            "file_path": file_path
        }
