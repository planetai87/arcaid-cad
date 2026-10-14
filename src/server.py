import os
import sys
import json
import base64
import logging
import argparse
from typing import Annotated, Any, List, Optional, Union

from pydantic import Field

sys.dont_write_bytecode = True

# 두 가지 실행 방식을 모두 지원한다:
# 1. 패키지로 import(pip install 후 모듈 이름은 src.server)
# 2. src/server.py를 직접 실행(기존 Claude/Cursor 설정을 그대로 쓸 수 있게)
if __package__:
    from .cad_controller import CADController
    from .config import get_config
    from .models import (ToolResult, ListEntitiesResult, ListLayersResult, EntityPropertiesResult,
                         ListBlocksResult, CommandResult, LispResult, ListDrawingsResult)
    from .colors import color_to_index
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cad_controller import CADController
    from config import get_config
    from models import (ToolResult, ListEntitiesResult, ListLayersResult, EntityPropertiesResult,
                        ListBlocksResult, CommandResult, LispResult, ListDrawingsResult)
    from colors import color_to_index

from mcp.server.fastmcp import FastMCP, Context
from mcp.types import ToolAnnotations, ImageContent, TextContent
from logging.handlers import RotatingFileHandler

# Windows에서 표준 입출력을 UTF-8로 맞춘다
if sys.platform == "win32" and os.environ.get('PYTHONIOENCODING') is None:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

config = get_config()

def _build_log_handlers() -> list:
    """로그 handler를 만든다: 콘솔 + 순환 파일

    로그 파일은 우선 소스 폴더에 쓴다(개발 중 바로 볼 수 있고, 실행 위치에 따라 경로가 바뀌지 않는다).
    pip으로 site-packages처럼 쓰기 권한이 없는 곳에 설치됐으면 사용자 폴더로 대체한다.
    그래야 FileHandler 생성 시 PermissionError로 서버가 시작에 실패하지 않는다.
    순환 파일(파일당 5MB, 백업 2개)로 오래 실행해도 로그가 끝없이 커지지 않게 한다.
    StreamHandler는 기본으로 stderr에 쓰므로 stdio 전송 모드에서 프로토콜 데이터를 오염시키지 않는다.
    """
    handlers = [logging.StreamHandler()]
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cad_mcp.log'),
        os.path.join(os.path.expanduser('~'), '.cad-mcp', 'cad_mcp.log'),
    ]
    for path in candidates:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            handlers.append(RotatingFileHandler(path, maxBytes=5 * 1024 * 1024,
                                                backupCount=2, encoding='utf-8'))
            break
        except OSError:
            continue
    return handlers


# 로그 설정(모듈 import 초기에 해야 하며, 프로세스 전체에서 basicConfig는 이 한 곳뿐이어야 한다.
# root에 이미 handler가 있으면 basicConfig는 조용히 무시된다. 자세한 내용은 src/__init__.py 설명 참고)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=_build_log_handlers()
)

logger = logging.getLogger('mcp_cad_server')
logger.info("arcaid-cad 서버 시작")

SERVER_NAME = config["server"]["name"]
SERVER_VERSION = config["server"].get("version", "3.0.0")

PROMPT_TEMPLATE = """
어시스턴트의 목표는 arcaid-cad 서비스 사용법을 보여 주는 것이다. 이 서비스를 쓰면 대화 안에서 CAD 프로그램을 직접 조작해 도형을 만들고 고칠 수 있다.

<arcaid-cad>

프롬프트:
이 서버는 "cad-assistant"라는 미리 정의된 프롬프트를 제공한다. 사용자가 자연어 지시로 CAD를 조작하도록 돕는다. 사용자는 여러 도형을 그리거나, 도형 요소를 고치거나, 도면을 저장해 달라고 요청할 수 있다.

리소스:
이 서버는 "drawing://current" 리소스를 제공한다. 현재 CAD 도면 상태(세션 기록 + CAD 문서의 실시간 통계)를 나타낸다.

도구:

이 서버는 다음 종류의 도구를 제공한다.

기본 그리기 도구:
"draw_line": CAD에 선을 그린다
"draw_circle": CAD에 원을 그린다
"draw_arc": CAD에 호를 그린다
"draw_ellipse": CAD에 타원을 그린다
"draw_polyline": CAD에 폴리선을 그린다
"draw_rectangle": CAD에 사각형을 그린다
"draw_text": CAD에 문자를 넣는다
"draw_hatch": CAD에 해치를 그린다
"add_dimension": CAD에 선형 치수를 넣는다

조회 도구(도면의 실제 내용 파악):
"list_layers": 모든 레이어 목록
"list_entities": 모형 공간 객체 목록(편집에 쓰는 핸들 포함)
"get_entity_properties": 핸들로 객체 상세 속성 조회
"screenshot": CAD 창을 캡처해 결과 확인

편집 도구(핸들로 기존 객체 조작):
"erase_entity": 객체 지우기
"move_entity": 객체 이동
"rotate_entity": 객체 회전
"scale_entity": 객체 크기 조정
"copy_entity": 객체 복사
"mirror_entity": 객체 대칭
"offset_entity": 객체 간격 띄우기
"array_linear_entity" / "array_polar_entity": 배열
"undo" / "redo": 실행 취소 / 다시 실행

레이어·도면 관리:
"create_layer" / "set_current_layer": 레이어 관리
"new_drawing" / "open_drawing" / "close_drawing" / "save_drawing": 도면 관리
"list_drawings" / "switch_drawing": 열린 도면 목록과 활성 도면 전환
"create_block" / "insert_block" / "list_blocks": 블록 작업
"send_command": CAD 명령행에 임의의 명령을 그대로 보낸다
"cancel_command": 진행 중인 명령을 취소한다(ESC)
"run_lisp": AutoLISP 식을 실행하고 결과 값을 돌려받는다

</arcaid-cad>

친근하게 시연을 시작한다. 예: "안녕하세요! 오늘은 arcaid-cad 사용법을 보여 드릴게요. 이 서비스를 쓰면 화면을 직접 조작하지 않고 대화 안에서 CAD를 다룰 수 있습니다. 시작해 볼까요?"
"""

# 서버 수준 사용 안내(initialize 응답과 함께 클라이언트에 전달된다)
SERVER_INSTRUCTIONS = """arcaid-cad: MCP 도구로 Windows의 GstarCAD / BricsCAD / AutoCAD / ZWCAD를 조작한다.

사용 요점:
- 도구를 처음 호출할 때 실행 중인 CAD에 붙고, CAD가 없으면 자동으로 띄운다(약 20초 걸릴 수 있음)
- 모든 도구는 CAD의 "활성 도면"에 작업한다. 사용자가 CAD에서 탭을 바꾸면 그 도면을 따라간다.
  여러 도면이 열려 있으면 list_drawings로 활성 도면을 확인하고, 필요하면 switch_drawing으로 바꾼다
- 그리기·편집 도구는 entity_handles(객체 핸들)를 돌려준다. 이후 편집과 조회에 쓴다
- 색상 인자는 ACI 인덱스(1-256) 또는 한국어·영어·중국어 색 이름을 받는다. 지정하지 않으면 ByLayer를 유지한다
- 그린 뒤에는 screenshot으로 결과를 확인하고, list_entities로 도면의 실제 내용을 조회하는 것을 권장한다.
  screenshot은 CAD 창을 앞으로 가져오지 않으며, 다른 창에 가려져 있어도 CAD 화면만 찍힌다
- send_command는 CAD 명령행에 그대로 보내고, 명령이 끝나거나 입력을 기다릴 때까지 기다린다.
  결과의 still_active=true는 명령이 아직 입력을 기다린다는 뜻이다. 이어서 입력을 보내거나 cancel_command로 취소한다.
  BricsCAD는 이어서 보낸 입력을 받지 않으므로 명령과 모든 입력을 한 문자열에 담아 보낸다
  (예: "_.-INSERT\n블록\n0,0\n1\n1\n0\nX1\n")
- cancel_command는 멈춘 명령을 ESC처럼 취소한다(대화상자·문자 편집기가 열려 COM이 막힌 경우 포함)
- run_lisp는 AutoLISP 식의 결과 값을 돌려받는다(예: (getvar "CLAYER")). 진행 중인 명령이 있으면 거부된다
- undo, redo, close_drawing, save_drawing은 연결 전에는 실행되지 않는다(사용자의 손 작업을 건드리지 않기 위해)
"""

# ==================== 인자 타입(원본 schema의 좌표 제약 유지) ====================

# 좌표점: 숫자 2~3개 [x, y] 또는 [x, y, z]
Point = Annotated[List[float], Field(min_length=2, max_length=3, description="좌표 [x, y] 또는 [x, y, z]")]
# 점 목록: 좌표점 2개 이상
Points = Annotated[List[Point], Field(min_length=2, description="점 목록 [[x1,y1], [x2,y2], ...](점마다 성분 2~3개)")]
# 해치 경계 점 목록: 좌표점 3개 이상
HatchPoints = Annotated[List[Point], Field(min_length=3, description="해치 경계 점 목록(3개 이상)")]
# 양수 인자(반지름/문자 높이/배율 등: CAD는 0이나 음수에 바로 오류를 내므로 schema 단계에서 미리 막는다)
PosFloat = Annotated[float, Field(gt=0, description="양수")]
# 배열 개수(원본 객체 포함): 하한 2(2 미만은 배열 의미가 없음), 상한은 실수로 객체를 대량 생성해 도면이 멈추는 것을 막기 위함
ArrayCount = Annotated[int, Field(ge=2, le=10000, description="배열 총 개수(원본 포함, 2~10000)")]


class CADService:
    def __init__(self):
        """CAD 서비스를 초기화한다"""
        self.controller = CADController()
        self.drawing_state = {
            "entities": [],
            "current_layer": "0",
            "last_command": "",
            "last_result": ""
        }
        logger.info("CAD 서비스 초기화 완료")

    @staticmethod
    def _handle(entity) -> Optional[str]:
        """객체 핸들을 안전하게 읽는다"""
        return CADController._safe_attr(entity, "Handle")

    def start_cad(self):
        """CAD를 시작한다"""
        return self.controller.start_cad()

    def draw_line(self, start_point, end_point, layer=None, color=None, lineweight=None):
        """선을 그린다"""
        if not self.controller.is_running():
            self.start_cad()

        # 지정한 레이어가 없으면 현재 레이어를 쓴다
        current_layer = layer or self.drawing_state["current_layer"]

        result = self.controller.draw_line(start_point, end_point, current_layer, color, lineweight)
        if result:
            self.drawing_state["entities"].append({
                "type": "line",
                "handle": self._handle(result),
                "start": start_point,
                "end": end_point,
                "layer": current_layer,
                "color": color,
                "lineweight": lineweight
            })
            self.drawing_state["last_command"] = f"선 그리기: {start_point}에서 {end_point}까지"
            self.drawing_state["last_result"] = "성공"
        else:
            self.drawing_state["last_result"] = "실패"

        return result

    def draw_circle(self, center, radius, layer=None, color=None, lineweight=None):
        """원을 그린다"""
        if not self.controller.is_running():
            self.start_cad()

        # 지정한 레이어가 없으면 현재 레이어를 쓴다
        current_layer = layer or self.drawing_state["current_layer"]

        result = self.controller.draw_circle(center, radius, current_layer, color, lineweight)
        if result:
            self.drawing_state["entities"].append({
                "type": "circle",
                "handle": self._handle(result),
                "center": center,
                "radius": radius,
                "layer": current_layer,
                "color": color,
                "lineweight": lineweight
            })
            self.drawing_state["last_command"] = f"원 그리기: 중심 {center}, 반지름 {radius}, 레이어 {current_layer}"
            self.drawing_state["last_result"] = "성공"
        else:
            self.drawing_state["last_result"] = "실패"

        return result

    def draw_arc(self, center, radius, start_angle, end_angle, layer=None, color=None, lineweight=None):
        """호를 그린다"""
        if not self.controller.is_running():
            self.start_cad()

        # 지정한 레이어가 없으면 현재 레이어를 쓴다
        current_layer = layer or self.drawing_state["current_layer"]

        result = self.controller.draw_arc(center, radius, start_angle, end_angle, current_layer, color, lineweight)

        if result:
            self.drawing_state["entities"].append({
                "type": "arc",
                "handle": self._handle(result),
                "center": center,
                "radius": radius,
                "start_angle": start_angle,
                "end_angle": end_angle,
                "layer": current_layer,
                "color": color,
                "lineweight": lineweight
            })
            self.drawing_state["last_command"] = f"호 그리기: 중심 {center}, 반지름 {radius}, 시작 각도 {start_angle}, 끝 각도 {end_angle}, 레이어 {current_layer}"
            self.drawing_state["last_result"] = "성공"
        else:
            self.drawing_state["last_result"] = "실패"

        return result

    def draw_ellipse(self, center, major_axis, minor_axis, rotation=0, layer=None, color=None, lineweight=None):
        """타원을 그린다"""
        if not self.controller.is_running():
            self.start_cad()

        # 지정한 레이어가 없으면 현재 레이어를 쓴다
        current_layer = layer or self.drawing_state["current_layer"]

        result = self.controller.draw_ellipse(center, major_axis, minor_axis, rotation, current_layer, color, lineweight)

        if result:
            self.drawing_state["entities"].append({
                "type": "ellipse",
                "handle": self._handle(result),
                "center": center,
                "major_axis": major_axis,
                "minor_axis": minor_axis,
                "rotation": rotation,
                "layer": current_layer,
                "color": color,
                "lineweight": lineweight
            })
            self.drawing_state["last_command"] = f"타원 그리기: 중심 {center}, 장축 {major_axis}, 단축 {minor_axis}, 회전 각도 {rotation}, 레이어 {current_layer}"
            self.drawing_state["last_result"] = "성공"
        else:
            self.drawing_state["last_result"] = "실패"

        return result

    def draw_polyline(self, points, closed=False, layer=None, color=None, lineweight=None):
        """폴리선을 그린다"""
        if not self.controller.is_running():
            self.start_cad()

        # 지정한 레이어가 없으면 현재 레이어를 쓴다
        current_layer = layer or self.drawing_state["current_layer"]

        result = self.controller.draw_polyline(points, closed, current_layer, color, lineweight)
        if result:
            self.drawing_state["entities"].append({
                "type": "polyline",
                "handle": self._handle(result),
                "points": points,
                "closed": closed,
                "layer": current_layer,
                "color": color,
                "lineweight": lineweight
            })
            self.drawing_state["last_command"] = f"폴리선 그리기: 점 {points}, {'닫힘' if closed else '열림'}, 레이어 {current_layer}"
            self.drawing_state["last_result"] = "성공"
        else:
            self.drawing_state["last_result"] = "실패"

        return result

    def draw_rectangle(self, corner1, corner2, layer=None, color=None, lineweight=None):
        """사각형을 그린다"""
        if not self.controller.is_running():
            self.start_cad()

        # 지정한 레이어가 없으면 현재 레이어를 쓴다
        current_layer = layer or self.drawing_state["current_layer"]

        result = self.controller.draw_rectangle(corner1, corner2, current_layer, color, lineweight)
        if result:
            self.drawing_state["entities"].append({
                "type": "rectangle",
                "handle": self._handle(result),
                "corner1": corner1,
                "corner2": corner2,
                "layer": current_layer,
                "color": color,
                "lineweight": lineweight
            })
            self.drawing_state["last_command"] = f"사각형 그리기: 대각점 {corner1}, {corner2}, 레이어 {current_layer}"
            self.drawing_state["last_result"] = "성공"
        else:
            self.drawing_state["last_result"] = "실패"

        return result

    def draw_text(self, position, text, height=2.5, rotation=0, layer=None, color=None):
        """문자를 넣는다"""
        if not self.controller.is_running():
            self.start_cad()

        # 지정한 레이어가 없으면 현재 레이어를 쓴다
        current_layer = layer or self.drawing_state["current_layer"]

        result = self.controller.draw_text(position, text, height, rotation, current_layer, color)
        if result:
            self.drawing_state["entities"].append({
                "type": "text",
                "handle": self._handle(result),
                "position": position,
                "text": text,
                "height": height,
                "rotation": rotation,
                "layer": current_layer,
                "color": color
            })
            self.drawing_state["last_command"] = f"문자 넣기: '{text}', 위치 {position}, 높이 {height}, 회전 {rotation}"
            self.drawing_state["last_result"] = "성공"
        else:
            self.drawing_state["last_result"] = "실패"

        return result

    def draw_hatch(self, points, pattern_name="SOLID", scale=1.0, layer=None, color=None):
        """해치를 그린다"""
        if not self.controller.is_running():
            self.start_cad()

        # 지정한 레이어가 없으면 현재 레이어를 쓴다
        current_layer = layer or self.drawing_state["current_layer"]

        result = self.controller.draw_hatch(points, pattern_name, scale, current_layer, color)
        if result:
            self.drawing_state["entities"].append({
                "type": "hatch",
                "handle": self._handle(result),
                "points": points,
                "pattern_name": pattern_name,
                "scale": scale,
                "layer": current_layer,
                "color": color
            })
            self.drawing_state["last_command"] = f"해치 그리기: 점 {points}, 패턴 {pattern_name}, 축척 {scale}, 레이어 {current_layer}"
            self.drawing_state["last_result"] = "성공"
        else:
            self.drawing_state["last_result"] = "실패"

        return result

    def add_dimension(self, start_point, end_point, text_position=None, textheight=5, layer=None, color=None):
        """선형 치수를 넣는다"""
        if not self.controller.is_running():
            self.start_cad()

        # 지정한 레이어가 없으면 현재 레이어를 쓴다
        current_layer = layer or self.drawing_state["current_layer"]

        result = self.controller.add_dimension(start_point, end_point, text_position, textheight, current_layer, color)
        if result:
            self.drawing_state["entities"].append({
                "type": "dimension",
                "handle": self._handle(result),
                "start": start_point,
                "end": end_point,
                "text_position": text_position,
                "textheight": textheight,
                "layer": current_layer,
                "color": color
            })
            self.drawing_state["last_command"] = f"치수 넣기: {start_point}에서 {end_point}까지"
            self.drawing_state["last_result"] = "성공"
        else:
            self.drawing_state["last_result"] = "실패"

        return result

    def save_drawing(self, file_path):
        """도면을 저장한다"""
        if not self.controller.is_running():
            return False

        result = self.controller.save_drawing(file_path)
        if result:
            self.drawing_state["last_command"] = f"도면 저장: {file_path}"
            self.drawing_state["last_result"] = "성공"
        else:
            self.drawing_state["last_result"] = "실패"

        return result


# ==================== 서비스 인스턴스와 공통 도우미 ====================

cad_service = CADService()


def _run_on_active_doc(fn, args, kwargs):
    """COM 스레드에서 활성 도면을 먼저 따라간 뒤 fn을 실행한다"""
    if cad_service.controller._sync_active_doc():
        # 세션 그리기 기록은 이전 도면 것이므로 도면이 바뀌면 비운다
        cad_service.drawing_state["entities"] = []
    return fn(*args, **kwargs)


async def com(fn, *args, **kwargs):
    """전용 COM 스레드에서 호출을 실행한다(이벤트 루프를 막지 않고, 스레드 안전)

    실행 전에 CAD의 활성 도면을 따라가므로, 사용자가 CAD에서 탭을 바꾸면 그 도면에 작업한다.
    """
    return await cad_service.controller.executor.run(_run_on_active_doc, fn, args, kwargs)


def _noop() -> None:
    """활성 도면 동기화만 하려고 com()에 넘기는 빈 함수"""
    return None


def _resolve_color(color) -> Optional[int]:
    """색상 인자를 ACI 인덱스로 바꾼다: None=ByLayer. 인덱스와 한국어·영어·중국어 색 이름을 받는다"""
    return color_to_index(color)


async def _entity_result(entity, ok_message: str, fail_message: str) -> ToolResult:
    """COM 객체 하나를 공통 도구 결과로 감싼다

    핸들은 반드시 COM 스레드에서 읽어야 한다. 객체는 COM 스레드가 만들었으므로,
    메인 스레드에서 마샬링되지 않은 COM 인터페이스 포인터에 직접 접근하면 아파트 간 호출이 되어 실패하거나 프로세스가 죽을 수 있다.
    """
    if entity is None:
        return ToolResult(success=False, message=fail_message, error="CAD 작업 실패, 자세한 내용은 cad_mcp.log 참고")
    handle = await com(CADController._safe_attr, entity, "Handle")
    return ToolResult(success=True, message=ok_message,
                      entity_handles=[str(handle)] if handle else [])


def _extract_handles_sync(entities: List[Any]) -> List[str]:
    """COM 스레드에서 객체 핸들을 한꺼번에 뽑는다"""
    return [str(h) for h in (CADController._safe_attr(e, "Handle") for e in entities) if h]


async def _entities_result(entities: List[Any], ok_message: str, fail_message: str) -> ToolResult:
    """COM 객체 목록을 공통 도구 결과로 감싼다(핸들은 COM 스레드에서 읽는다)"""
    if not entities:
        return ToolResult(success=False, message=fail_message)
    handles = await com(_extract_handles_sync, entities)
    return ToolResult(success=True, message=ok_message, entity_handles=handles)


async def _ensure_running(ctx: Context) -> bool:
    """CAD가 실행 중인지 보장한다: 연결이 없으면 진행 알림을 먼저 보내고 자동으로 시작한다(첫 시작은 느릴 수 있음)

    실제로 시작까지 해야 한다. 예전에는 알림만 보내고 시작하지 않아서,
    screenshot처럼 자동 시작 로직이 없는 도구가 CAD 미실행 시 바로 예외를 냈다.
    """
    if cad_service.controller.app is not None:
        # 이미 연결돼 있으면 활성 도면부터 맞춘다(사용자가 도면을 모두 닫았으면 여기서 doc이 None이 된다)
        await com(_noop)
    if cad_service.controller.is_running():
        return True
    await ctx.info("CAD가 연결되지 않아 CAD를 시작하는 중입니다(처음에는 20초쯤 걸릴 수 있음)...")
    if await com(cad_service.start_cad):
        return True
    await ctx.info("CAD 자동 시작 실패. CAD가 설치돼 있고 정상적으로 실행되는지 확인하세요")
    return False


async def _busy_refusal() -> Optional[str]:
    """명령이 진행 중이면 도면을 닫거나 바꾸지 않도록 거부 메시지를 돌려준다(아니면 None)"""
    reason = await com(cad_service.controller.command_busy_reason)
    if reason:
        return f"{reason}. 도면을 열거나 닫거나 바꾸기 전에 cancel_command로 먼저 취소하세요"
    return None


# ==================== MCP 서버와 도구 등록 ====================

mcp = FastMCP(name=SERVER_NAME, instructions=SERVER_INSTRUCTIONS)


# ---------- 기본 그리기 도구 ----------

@mcp.tool()
async def draw_line(ctx: Context, start_point: Point, end_point: Point,
                    layer: Optional[str] = None, color: Union[int, str, None] = None,
                    lineweight: Optional[float] = None) -> ToolResult:
    """CAD에 선을 그린다

    color는 CAD 색상 인덱스(1-256) 또는 한국어·영어·중국어 색 이름(예: "빨강", "red")을 받는다. 지정하지 않으면 ByLayer를 유지한다.
    """
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    entity = await com(cad_service.draw_line, start_point, end_point, layer, _resolve_color(color), lineweight)
    return await _entity_result(entity, "선을 그렸습니다", "선 그리기 실패")


@mcp.tool()
async def draw_circle(ctx: Context, center: Point, radius: PosFloat,
                      layer: Optional[str] = None, color: Union[int, str, None] = None,
                      lineweight: Optional[float] = None) -> ToolResult:
    """CAD에 원을 그린다

    color는 CAD 색상 인덱스(1-256) 또는 한국어·영어·중국어 색 이름을 받는다. 지정하지 않으면 ByLayer를 유지한다.
    """
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    entity = await com(cad_service.draw_circle, center, radius, layer, _resolve_color(color), lineweight)
    return await _entity_result(entity, "원을 그렸습니다", "원 그리기 실패")


@mcp.tool()
async def draw_arc(ctx: Context, center: Point, radius: PosFloat,
                   start_angle: float, end_angle: float,
                   layer: Optional[str] = None, color: Union[int, str, None] = None,
                   lineweight: Optional[float] = None) -> ToolResult:
    """CAD에 호를 그린다(각도 단위: 도)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    entity = await com(cad_service.draw_arc, center, radius, start_angle, end_angle,
                       layer, _resolve_color(color), lineweight)
    return await _entity_result(entity, "호를 그렸습니다", "호 그리기 실패")


@mcp.tool()
async def draw_ellipse(ctx: Context, center: Point, major_axis: PosFloat, minor_axis: PosFloat,
                       rotation: float = 0, layer: Optional[str] = None,
                       color: Union[int, str, None] = None,
                       lineweight: Optional[float] = None) -> ToolResult:
    """CAD에 타원을 그린다(rotation은 회전 각도, 단위: 도)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    entity = await com(cad_service.draw_ellipse, center, major_axis, minor_axis, rotation,
                       layer, _resolve_color(color), lineweight)
    return await _entity_result(entity, "타원을 그렸습니다", "타원 그리기 실패")


@mcp.tool()
async def draw_polyline(ctx: Context, points: Points, closed: bool = False,
                        layer: Optional[str] = None, color: Union[int, str, None] = None,
                        lineweight: Optional[float] = None) -> ToolResult:
    """CAD에 폴리선을 그린다(closed=True면 닫는다)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    entity = await com(cad_service.draw_polyline, points, closed, layer, _resolve_color(color), lineweight)
    return await _entity_result(entity, "폴리선을 그렸습니다", "폴리선 그리기 실패")


@mcp.tool()
async def draw_rectangle(ctx: Context, corner1: Point, corner2: Point,
                         layer: Optional[str] = None, color: Union[int, str, None] = None,
                         lineweight: Optional[float] = None) -> ToolResult:
    """CAD에 사각형을 그린다(대각점 두 개로 정한다)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    entity = await com(cad_service.draw_rectangle, corner1, corner2, layer, _resolve_color(color), lineweight)
    return await _entity_result(entity, "사각형을 그렸습니다", "사각형 그리기 실패")


@mcp.tool()
async def draw_text(ctx: Context, position: Point, text: str, height: PosFloat = 2.5,
                    rotation: float = 0, layer: Optional[str] = None,
                    color: Union[int, str, None] = None) -> ToolResult:
    """CAD에 한 줄 문자를 넣는다"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    entity = await com(cad_service.draw_text, position, text, height, rotation, layer, _resolve_color(color))
    return await _entity_result(entity, "문자를 넣었습니다", "문자 넣기 실패")


@mcp.tool()
async def draw_hatch(ctx: Context, points: HatchPoints, pattern_name: str = "SOLID",
                     scale: PosFloat = 1.0, layer: Optional[str] = None,
                     color: Union[int, str, None] = None) -> ToolResult:
    """CAD에 해치를 그린다(points는 닫힌 경계 점 목록, 3개 이상)

    pattern_name은 해치 패턴 이름이고 기본값은 SOLID(솔리드 채우기)다.
    경계 폴리선이 같은 레이어에 함께 만들어진다. 패턴 이름이 잘못되면 실패하고 아무것도 남기지 않는다.
    GstarCAD는 명령행 -HATCH로 만든다(COM 해치가 화면에 그려지지 않아서). 이때 undo 한 번에는 해치만 취소된다.
    """
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    entity = await com(cad_service.draw_hatch, points, pattern_name, scale, layer, _resolve_color(color))
    return await _entity_result(entity, "해치를 그렸습니다", "해치 그리기 실패")


@mcp.tool()
async def add_dimension(ctx: Context, start_point: Point, end_point: Point,
                        text_position: Optional[Point] = None, textheight: PosFloat = 5,
                        layer: Optional[str] = None, color: Union[int, str, None] = None) -> ToolResult:
    """CAD에 선형 치수를 넣는다(text_position을 주지 않으면 중점 위에 자동으로 놓는다)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    entity = await com(cad_service.add_dimension, start_point, end_point, text_position, textheight,
                       layer, _resolve_color(color))
    return await _entity_result(entity, "치수를 넣었습니다", "치수 넣기 실패")


# ---------- 조회 도구(AI가 도면의 실제 내용을 볼 수 있게 피드백 고리를 닫는다) ----------

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def list_layers(ctx: Context) -> ListLayersResult:
    """현재 도면의 모든 레이어를 나열한다(이름, 색, 동결/잠금/켜짐 상태, 현재 레이어 여부)"""
    if not await _ensure_running(ctx):
        return ListLayersResult(success=False, message="CAD 미연결, 자동 시작 실패")
    layers = await com(cad_service.controller.list_layers)
    return ListLayersResult(success=True, message=f"레이어 {len(layers)}개", layers=layers)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def list_entities(ctx: Context, entity_type: Optional[str] = None,
                        limit: int = 50, layer: Optional[str] = None,
                        color: Union[int, str, None] = None) -> ListEntitiesResult:
    """모형 공간의 객체를 나열한다(실시간 조회, CAD 문서와 항상 일치)

    객체 핸들(handle)을 돌려주며, 편집 도구와 get_entity_properties에 쓸 수 있다.
    entity_type으로 종류를 거를 수 있고(예: line/circle/arc/text), limit으로 돌려줄 개수를 정한다.
    layer로 레이어 이름(대소문자 무시), color로 객체 색(ACI 인덱스 또는 색 이름, 256=ByLayer)을 거를 수 있다.
    """
    if not await _ensure_running(ctx):
        return ListEntitiesResult(success=False, message="CAD 미연결, 자동 시작 실패")
    color_index = _resolve_color(color)
    if color is not None and color_index is None:
        return ListEntitiesResult(success=False, message=f"알 수 없는 색: {color}")
    result = await com(cad_service.controller.list_entities, entity_type, limit, layer, color_index)
    return ListEntitiesResult(success=True,
                              message=f"모형 공간 객체 {result['total']}개 중 {len(result['entities'])}개 반환",
                              total=result["total"],
                              returned=len(result["entities"]),
                              entities=result["entities"])


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_entity_properties(ctx: Context, handle: str) -> EntityPropertiesResult:
    """핸들로 객체 상세 속성을 조회한다(기하 정보는 객체 종류마다 다르다: 좌표, 반지름, 문자 내용 등)"""
    if not await _ensure_running(ctx):
        return EntityPropertiesResult(success=False, message="CAD 미연결, 자동 시작 실패")
    try:
        props = await com(cad_service.controller.get_entity_properties, handle)
        return EntityPropertiesResult(success=True, message="조회 성공", **props)
    except Exception as e:
        return EntityPropertiesResult(success=False, message=f"핸들 {handle} 조회 실패", error=str(e))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def screenshot(ctx: Context):
    """CAD 메인 창을 캡처해 PNG 미리보기를 돌려준다(그린 결과 확인용)

    창을 앞으로 가져오지 않으므로 사용자의 작업 창 포커스를 뺏지 않고, 다른 창에 가려져 있어도 CAD 화면만 찍힌다.
    """
    if not await _ensure_running(ctx):
        return [TextContent(type="text", text="CAD 미연결, 자동 시작 실패. 캡처할 수 없습니다")]
    png_bytes = await com(cad_service.controller.screenshot)
    # base64로 인코딩해 이미지 콘텐츠 블록으로 돌려준다
    b64 = base64.b64encode(png_bytes).decode("utf-8")
    return [
        ImageContent(type="image", data=b64, mimeType="image/png"),
        TextContent(type="text", text="CAD 창 캡처(PNG)"),
    ]


# ---------- 편집 도구(핸들로 기존 객체 조작) ----------

@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def erase_entity(ctx: Context, handle: str) -> ToolResult:
    """지정한 핸들의 객체를 지운다"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    ok = await com(cad_service.controller.erase_entity, handle)
    return ToolResult(success=ok, message="객체를 지웠습니다" if ok else f"핸들 {handle} 지우기 실패")


@mcp.tool()
async def move_entity(ctx: Context, handle: str, displacement: Point) -> ToolResult:
    """객체를 이동한다(displacement는 이동 벡터 [dx, dy, dz])"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    ok = await com(cad_service.controller.move_entity, handle, displacement)
    return ToolResult(success=ok, message="객체를 이동했습니다" if ok else f"핸들 {handle} 이동 실패")


@mcp.tool()
async def rotate_entity(ctx: Context, handle: str, base_point: Point, angle_degrees: float) -> ToolResult:
    """기준점을 중심으로 객체를 회전한다(각도 단위: 도, 반시계 방향이 양수)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    ok = await com(cad_service.controller.rotate_entity, handle, base_point, angle_degrees)
    return ToolResult(success=ok, message="객체를 회전했습니다" if ok else f"핸들 {handle} 회전 실패")


@mcp.tool()
async def scale_entity(ctx: Context, handle: str, base_point: Point, scale_factor: PosFloat) -> ToolResult:
    """기준점을 중심으로 객체 크기를 바꾼다(scale_factor>1이면 확대, <1이면 축소)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    ok = await com(cad_service.controller.scale_entity, handle, base_point, scale_factor)
    return ToolResult(success=ok, message="객체 크기를 바꿨습니다" if ok else f"핸들 {handle} 크기 조정 실패")


@mcp.tool()
async def copy_entity(ctx: Context, handle: str, displacement: Point) -> ToolResult:
    """객체를 복사해 지정한 만큼 옮기고, 새 객체 핸들을 돌려준다"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    copy = await com(cad_service.controller.copy_entity, handle, displacement)
    return await _entity_result(copy, "객체를 복사했습니다", f"핸들 {handle} 복사 실패")


@mcp.tool()
async def mirror_entity(ctx: Context, handle: str, point1: Point, point2: Point) -> ToolResult:
    """두 점으로 정한 대칭축을 기준으로 객체를 대칭 복사하고(원본 유지), 새 객체 핸들을 돌려준다"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    mirrored = await com(cad_service.controller.mirror_entity, handle, point1, point2)
    return await _entity_result(mirrored, "객체를 대칭 복사했습니다", f"핸들 {handle} 대칭 실패")


@mcp.tool()
async def offset_entity(ctx: Context, handle: str, distance: float) -> ToolResult:
    """객체를 간격 띄우기 하고(양수/음수로 방향 결정), 새 객체 핸들을 돌려준다"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    entities = await com(cad_service.controller.offset_entity, handle, distance)
    return await _entities_result(entities, "객체를 간격 띄우기 했습니다", f"핸들 {handle} 간격 띄우기 실패")


@mcp.tool()
async def array_linear_entity(ctx: Context, handle: str, count: ArrayCount, displacement: Point) -> ToolResult:
    """직선 배열: 이동 벡터 방향으로 count-1개를 복사한다(원본 포함 총 count개)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    entities = await com(cad_service.controller.array_linear_entity, handle, count, displacement)
    return await _entities_result(entities, f"{count}개 직선 배열 완료", f"핸들 {handle} 배열 실패")


@mcp.tool()
async def array_polar_entity(ctx: Context, handle: str, count: ArrayCount, center: Point,
                             total_angle_degrees: float = 360.0) -> ToolResult:
    """원형 배열: 중심점을 기준으로 total_angle 범위 안에 count-1개를 복사한다"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    entities = await com(cad_service.controller.array_polar_entity, handle, count, center, total_angle_degrees)
    return await _entities_result(entities, f"{count}개 원형 배열 완료", f"핸들 {handle} 배열 실패")


@mcp.tool()
async def undo(ctx: Context) -> ToolResult:
    """직전 작업을 실행 취소한다"""
    if not cad_service.controller.is_running():
        return ToolResult(success=False, message="CAD 미연결")
    ok = await com(cad_service.controller.undo)
    return ToolResult(success=ok, message="실행 취소했습니다" if ok else "실행 취소 실패")


@mcp.tool()
async def redo(ctx: Context) -> ToolResult:
    """실행 취소한 작업을 다시 실행한다"""
    if not cad_service.controller.is_running():
        return ToolResult(success=False, message="CAD 미연결")
    ok = await com(cad_service.controller.redo)
    return ToolResult(success=ok, message="다시 실행했습니다" if ok else "다시 실행 실패")


# ---------- 레이어 관리 ----------

@mcp.tool()
async def create_layer(ctx: Context, layer_name: str) -> ToolResult:
    """새 레이어를 만들고 현재 레이어로 지정한다(이미 있으면 그 레이어를 현재로 지정)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    ok = await com(cad_service.controller.create_layer, layer_name)
    if ok:
        cad_service.drawing_state["current_layer"] = layer_name
    return ToolResult(success=ok, message=f"레이어 {layer_name}을(를) 만들고 현재 레이어로 지정했습니다" if ok else f"레이어 {layer_name} 만들기 실패")


@mcp.tool()
async def set_current_layer(ctx: Context, layer_name: str) -> ToolResult:
    """현재 레이어를 바꾼다(레이어가 이미 있어야 한다)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    ok = await com(cad_service.controller.set_current_layer, layer_name)
    if ok:
        cad_service.drawing_state["current_layer"] = layer_name
    return ToolResult(success=ok, message=f"현재 레이어를 {layer_name}(으)로 바꿨습니다" if ok else f"레이어 {layer_name}이(가) 없거나 바꾸기 실패")


# ---------- 도면 관리 ----------

@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def new_drawing(ctx: Context) -> ToolResult:
    """새 도면 문서를 만든다"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    if busy := await _busy_refusal():
        return ToolResult(success=False, message=busy)
    ok = await com(cad_service.controller.new_drawing)
    if ok:
        # 세션 그리기 기록은 이전 문서 것이므로 문서를 바꾸면 비운다
        cad_service.drawing_state["entities"] = []
    return ToolResult(success=ok, message="새 문서를 만들었습니다" if ok else "새 문서 만들기 실패")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def open_drawing(ctx: Context, file_path: str) -> ToolResult:
    """기존 도면 파일(DWG/DXF 등)을 연다"""
    if cad_service.controller.app is None and not await com(cad_service.controller.start_cad):
        return ToolResult(success=False, message="CAD 미연결, 시작 실패")
    if cad_service.controller.doc is not None and (busy := await _busy_refusal()):
        return ToolResult(success=False, message=busy)
    try:
        ok = await com(cad_service.controller.open_drawing, file_path)
    except FileNotFoundError as e:
        return ToolResult(success=False, message=str(e))
    if ok:
        cad_service.drawing_state["entities"] = []
    return ToolResult(success=ok, message=f"문서를 열었습니다: {file_path}" if ok else f"문서 열기 실패: {file_path}")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def close_drawing(ctx: Context, save: bool = False) -> ToolResult:
    """현재 문서를 닫는다(save=False면 저장하지 않고 바로 닫는다)"""
    if not cad_service.controller.is_running():
        return ToolResult(success=False, message="CAD 미연결")
    if busy := await _busy_refusal():
        return ToolResult(success=False, message=busy)
    ok = await com(cad_service.controller.close_drawing, save)
    if ok:
        cad_service.drawing_state["entities"] = []
    return ToolResult(success=ok, message="문서를 닫았습니다" if ok else "문서 닫기 실패")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def list_drawings(ctx: Context) -> ListDrawingsResult:
    """CAD에 열린 도면을 모두 나열한다(이름, 전체 경로, 활성 여부, 저장 여부)

    모든 도구는 CAD의 활성 도면에 작업한다. 작업 전에 is_active인 도면이 맞는지 확인한다.
    """
    if not await _ensure_running(ctx):
        return ListDrawingsResult(success=False, message="CAD 미연결, 자동 시작 실패")
    drawings = await com(cad_service.controller.list_drawings)
    return ListDrawingsResult(success=True, message=f"열린 도면 {len(drawings)}개", drawings=drawings)


@mcp.tool()
async def switch_drawing(ctx: Context, name_or_path: str) -> ToolResult:
    """열린 도면 중 하나를 활성 도면으로 바꾼다(전체 경로 또는 도면 이름으로 지정)

    이후 모든 도구는 이 도면에 작업한다. 열려 있지 않은 파일은 open_drawing으로 연다.
    """
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    if busy := await _busy_refusal():
        return ToolResult(success=False, message=busy)
    name = await com(cad_service.controller.switch_drawing, name_or_path)
    if name is None:
        return ToolResult(success=False, message=f"열린 도면 중에 없습니다: {name_or_path}. list_drawings로 확인하세요")
    cad_service.drawing_state["entities"] = []
    return ToolResult(success=True, message=f"활성 도면을 {name}(으)로 바꿨습니다")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def save_drawing(ctx: Context, file_path: str) -> ToolResult:
    """현재 도면을 저장한다(확장자에 따라 DWG/DXF 형식을 자동 선택. 같은 이름의 파일은 덮어쓴다)"""
    if not cad_service.controller.is_running():
        return ToolResult(success=False, message="CAD 미연결")
    if busy := await _busy_refusal():
        return ToolResult(success=False, message=busy)
    await ctx.info(f"도면을 저장하는 중: {file_path} ...")
    try:
        ok = await com(cad_service.save_drawing, file_path)
    except ValueError as e:
        # 대상 경로를 다른 열린 도면이 쓰고 있음: 일찍 실패하고 조치할 수 있는 안내를 준다
        return ToolResult(success=False, message=f"저장 실패: {e}", error=str(e))
    return ToolResult(success=ok, message=f"도면을 저장했습니다: {file_path}" if ok else f"도면 저장 실패: {file_path}")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def send_command(ctx: Context, command: str) -> CommandResult:
    """CAD 명령행에 임의의 명령을 그대로 보낸다(도구로 없는 기능을 쓰는 비상구)

    예: "_.ZOOM E"(전체 줌), "_.ERASE"와 선택 세트 등.
    명령 앞에 "_."를 붙여 영문 명령 이름을 강제하면 현지화된 CAD에서도 동작한다.
    명령이 끝나거나 입력을 기다리는 상태가 될 때까지 기다린 뒤 돌아온다.
    still_active=true면 명령이 아직 입력(점, 옵션)을 기다리는 중이다. 이어서 입력을 보내거나 cancel_command로 취소한다.
    BricsCAD는 입력을 기다리는 명령에 이어서 보낸 입력을 받지 않는다. 명령과 입력을 한 문자열에 모두 담아 보낸다.
    """
    if not await _ensure_running(ctx):
        return CommandResult(success=False, message="CAD 미연결, 자동 시작 실패")
    result = await com(cad_service.controller.send_command, command)
    cmd = result["cmdactive"]
    still_active = True if result["pending"] else (None if cmd < 0 else cmd != 0)
    if not result["ok"]:
        return CommandResult(success=False, message="명령 실행 실패", still_active=still_active, cmdactive=cmd,
                             error=result["error"] or "자세한 내용은 cad_mcp.log 참고")
    if result["pending"]:
        message = (f"명령을 보냈고 명령이 입력을 기다리는 중입니다(CMDACTIVE={cmd}). 이 CAD(예: BricsCAD)는 "
                   "명령이 끝날 때까지 다음 입력을 받지 않으므로, cancel_command로 취소한 뒤 필요한 입력을 모두 포함해 한 번에 다시 보내세요")
    elif still_active:
        message = f"명령을 보냈습니다. 명령이 아직 입력을 기다리는 중입니다(CMDACTIVE={cmd})"
    elif still_active is None:
        message = "명령을 보냈습니다. 명령 상태는 읽지 못했습니다"
    else:
        message = "명령을 보냈고 명령이 끝났습니다"
    return CommandResult(success=True, message=message, still_active=still_active, cmdactive=cmd)


@mcp.tool()
async def cancel_command(ctx: Context) -> CommandResult:
    """진행 중인 CAD 명령을 취소한다(ESC와 같음)

    먼저 명령행에 ESC 문자를 보내고, 끝나지 않거나 CAD가 COM에 응답하지 않으면
    (대화상자·문자 편집기가 열린 경우) CAD 창을 앞으로 가져와 키보드 ESC를 보낸다.
    명령이 입력을 기다리며 멈췄을 때, 또는 run_lisp가 진행 중인 명령 때문에 거부됐을 때 쓴다.
    """
    if cad_service.controller.app is None and not await _ensure_running(ctx):
        return CommandResult(success=False, message="CAD 미연결, 자동 시작 실패")
    # COM이 막혀 있어도 빨리 끝나야 하므로 활성 도면 동기화(재시도 포함)를 거치지 않고 바로 실행한다
    result = await cad_service.controller.executor.run(cad_service.controller.cancel_command)
    cmd = result["cmdactive"]
    still_active = None if cmd < 0 else cmd != 0
    method = result["method"] or "없음"
    if result["ok"]:
        message = "진행 중인 명령이 없습니다" if method == "none" else f"명령을 취소했습니다(방법: {method})"
        return CommandResult(success=True, message=message, still_active=False, cmdactive=cmd)
    return CommandResult(success=False, message=f"명령을 취소하지 못했습니다(시도: {method}). 사용자에게 CAD 창에서 ESC를 눌러 달라고 요청하세요",
                         still_active=still_active, cmdactive=cmd)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def run_lisp(ctx: Context, expression: str, timeout_seconds: Optional[PosFloat] = None) -> LispResult:
    """AutoLISP 식 하나를 실행하고 결과 값을 돌려받는다

    결과는 prin1 표기 문자열로 돌아온다(예: 3, "A-WALL", (1.0 2.0 0.0), nil).
    여러 식은 (progn ...)으로 묶는다. 식 안의 줄바꿈은 공백으로 바뀐다.
    진행 중인 명령이 있으면 실행하지 않는다(먼저 cancel_command).
    예: (getvar "CLAYER"), (sslength (ssget "_X" '((8 . "A-WALL")))), (tblsearch "LAYER" "0")
    """
    if not await _ensure_running(ctx):
        return LispResult(success=False, message="CAD 미연결, 자동 시작 실패")
    result = await com(cad_service.controller.run_lisp, expression, timeout_seconds)
    if result["ok"]:
        return LispResult(success=True, message="LISP 실행 완료", value=result["value"], cmdactive=result["cmdactive"])
    return LispResult(success=False, message="LISP 실행 실패", cmdactive=result["cmdactive"], error=result["error"])


# ---------- 블록 작업 ----------

@mcp.tool()
async def create_block(ctx: Context, block_name: str, base_point: Optional[Point] = None) -> ToolResult:
    """블록 정의를 만든다(같은 이름의 블록이 있으면 그대로 돌려준다)

    참고: 블록 안의 객체는 send_command로 BEDIT 같은 명령을 실행해 편집할 수 있다.
    """
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    block = await com(cad_service.controller.create_block, block_name, base_point)
    return await _entity_result(block, f"블록 {block_name} 준비 완료", f"블록 {block_name} 만들기 실패")


@mcp.tool()
async def insert_block(ctx: Context, block_name: str, position: Point,
                       x_scale: float = 1.0, y_scale: float = 1.0, z_scale: float = 1.0,
                       rotation_degrees: float = 0.0) -> ToolResult:
    """모형 공간에 블록 참조를 넣는다(블록 참조 객체의 핸들을 돌려준다)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message="CAD 미연결, 자동 시작 실패")
    block_ref = await com(cad_service.controller.insert_block, block_name, position,
                          x_scale, y_scale, z_scale, rotation_degrees)
    return await _entity_result(block_ref, f"블록 {block_name}을(를) 넣었습니다", f"블록 {block_name} 넣기 실패(블록이 정의돼 있는지 확인)")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def list_blocks(ctx: Context) -> ListBlocksResult:
    """현재 도면의 사용자 블록 정의를 나열한다"""
    if not await _ensure_running(ctx):
        return ListBlocksResult(success=False, message="CAD 미연결, 자동 시작 실패")
    blocks = await com(cad_service.controller.list_blocks)
    return ListBlocksResult(success=True, message=f"블록 {len(blocks)}개", blocks=blocks)


# ---------- 리소스와 프롬프트 ----------

@mcp.resource("drawing://current", name="현재 CAD 도면",
              description="현재 CAD 도면 상태(세션 그리기 기록 + CAD 문서의 실시간 통계)",
              mime_type="application/json")
async def current_drawing() -> str:
    """현재 CAD 도면 상태 리소스"""
    # 세션 기록 + 실시간 통계: 이번 세션에 무엇을 그렸는지와 문서의 실제 상태를 함께 보여 준다
    stats = await com(cad_service.controller.get_drawing_stats)
    payload = {
        "session": cad_service.drawing_state,
        "live": stats,
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


@mcp.prompt(name="cad-assistant", description="자연어로 CAD를 조작하는 어시스턴트")
def cad_assistant() -> str:
    """CAD 어시스턴트 프롬프트 템플릿"""
    return PROMPT_TEMPLATE.strip()


# ==================== 진입점 ====================

def main():
    """진입점: stdio(기본)와 streamable-http 두 가지 전송 방식을 지원한다"""
    parser = argparse.ArgumentParser(description="arcaid-cad 서버")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default=None,
                        help="전송 방식(기본은 config.json 값, 없으면 stdio)")
    parser.add_argument("--host", default=None, help="Streamable HTTP 수신 주소")
    parser.add_argument("--port", type=int, default=None, help="Streamable HTTP 수신 포트")
    args = parser.parse_args()

    server_cfg = config.get("server", {})
    transport = args.transport or server_cfg.get("transport", "stdio")
    host = args.host or server_cfg.get("host", "127.0.0.1")
    port = args.port or int(server_cfg.get("port", 8000))

    if transport == "streamable-http":
        # Streamable HTTP 전송: 원격/여러 클라이언트가 같은 CAD 인스턴스를 함께 쓸 수 있다
        mcp.settings.host = host
        mcp.settings.port = port
        logger.info(f"서버를 Streamable HTTP 전송으로 실행: http://{host}:{port}/mcp")
        mcp.run(transport="streamable-http")
    else:
        logger.info("서버를 stdio 전송으로 실행")
        mcp.run()  # 기본 stdio


if __name__ == "__main__":
    main()
