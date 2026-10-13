import logging
import math
import time
import os
import io
import json
import threading
import queue
import tempfile
import uuid
import concurrent.futures
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple, Union

# 설정 싱글턴을 공통으로 쓴다(예전처럼 모듈마다 config.json을 다시 읽지 않는다)
try:
    from .config import get_config
except ImportError:
    from config import get_config

config = get_config()

try:
    import win32com.client
    # pythoncom은 pywin32에 포함돼 있어 따로 설치할 필요가 없다
    import pythoncom
except ImportError:
    logging.error("win32com.client 또는 pythoncom을 불러올 수 없습니다. pywin32가 설치돼 있는지 확인하세요")
    raise

try:
    import anyio
except ImportError:
    # anyio는 mcp SDK의 의존성이라 보통 mcp와 함께 설치된다
    anyio = None

logger = logging.getLogger('cad_controller')

# COM 일시 오류: 바쁜 CAD가 호출을 거부함(RPC_E_CALL_REJECTED).
# CAD가 명령 실행/대화상자/재생성 중일 때 잠깐 COM 호출을 거부하며, 잠시 뒤 재시도하면 회복된다
_RPC_E_CALL_REJECTED = -2147418111

# AutoCAD 일시 저장 오류("문서 저장 중 오류").
# 저장/열기/닫기 진행 중에 SaveAs를 부르면 이 오류가 나며, 잠시 뒤 재시도하면 회복된다
_ACAD_SAVE_TRANSIENT = -2145320861

# COM 죽은 참조 오류: 사용자가 CAD를 닫았거나 CAD가 죽어서 들고 있던 인터페이스 포인터가 무효가 됨
# RPC_E_DISCONNECTED(-2147417848): 객체가 클라이언트와 연결이 끊김
# RPC_S_SERVER_UNAVAILABLE(-2147023174): RPC 서버를 쓸 수 없음
# 일시 오류와 달리 재시도해도 소용없으므로, 연결 상태를 초기화해 다음 작업이 다시 연결하게 한다
_DEAD_COM_HRESULTS = (-2147417848, -2147023174)


def _is_retryable_com_error(e) -> bool:
    """재시도할 수 있는 COM 일시 오류인지 판단한다(바쁨으로 거부 또는 저장 충돌)"""
    if getattr(e, "hresult", None) == _RPC_E_CALL_REJECTED:
        return True
    # 일시 저장 오류는 com_error의 excepinfo 튜플 안에 들어 있다
    info = tuple(getattr(e, "excepinfo", None) or ())
    return _ACAD_SAVE_TRANSIENT in info


def _is_retryable_attr_error(e, retry_attr) -> bool:
    """pywin32 동적 디스패치가 "멤버 조회 거부"를 바꿔 낸 AttributeError인지 판단한다

    동적 디스패치에서 멤버 조회(GetIDsOfNames)가 바쁜 CAD에 거부되면, pywin32는
    com_error를 삼키고 AttributeError를 던지며 hresult도 사라진다(문서를 연 직후 닫을 때
    "Open.Close" 오류가 나고 호출 단위 재시도가 전혀 동작하지 않은 적이 있다).
    알려진 메시지 형식은 두 가지다: dynamic.py는 "Open.Close"를,
    client/__init__.py는 "'<COMObject Open>' object has no attribute 'Close'"를 던진다.
    멤버 이름을 지정하지 않으면 재시도하지 않는다.
    """
    if not retry_attr or not isinstance(e, AttributeError):
        return False
    msg = str(e)
    return msg.endswith("." + retry_attr) or msg.endswith(f"attribute '{retry_attr}'")


def _call_with_com_retry(fn, args, kwargs, retries: int = 20, delay: float = 0.5, retry_attr: str = None):
    """COM 호출을 실행하고, 바쁨(RPC_E_CALL_REJECTED)이나 일시 저장 충돌이면 자동으로 재시도한다

    총 대기 시간은 최대 약 retries*delay초이며, 넘으면 원래 예외를 던진다.
    retry_attr은 _is_retryable_attr_error 설명을 참고한다.
    """
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            # 일시 오류(COM 바쁨/저장 충돌/멤버 조회 거부)만 재시도하고, 나머지 예외는 바로 던진다
            if (_is_retryable_com_error(e) or _is_retryable_attr_error(e, retry_attr)) \
                    and attempt < retries - 1:
                logger.debug(f"CAD가 바쁘거나 저장 충돌로 COM 호출이 거부됨, {attempt + 1}번째 재시도...")
                time.sleep(delay)
                continue
            raise
    raise RuntimeError("unreachable")  # 논리상 도달하지 않음, 타입 완결성용


class COMExecutor:
    """전용 COM 스레드 실행기

    Windows COM의 아파트(Apartment) 모델은 인터페이스 포인터를 만든 스레드에서 쓰도록 요구한다.
    다른 스레드에서 바로 접근하면 호출이 실패하거나 프로세스가 죽을 수 있다. 이 실행기는 모든 COM 호출을
    하나의 STA 스레드로 직렬화해 두 가지 이점을 준다:
    1. 스레드 안전: 여러 도구가 동시에 호출해도 아파트를 넘나드는 접근이 생기지 않는다
    2. asyncio 이벤트 루프를 막지 않는다: COM의 블로킹 호출(예: CAD 시작을 20초 기다림)이
       모두 전용 스레드에서 일어나므로 서버는 다른 요청에 계속 응답할 수 있다
    """

    def __init__(self):
        self._queue: "queue.Queue" = queue.Queue()
        self._thread = threading.Thread(target=self._worker, name="cad-com", daemon=True)
        self._thread.start()

    def _worker(self) -> None:
        """COM 스레드 메인 루프: COM을 초기화한 뒤 큐에서 작업을 꺼내 계속 실행한다"""
        # 프로세스 수명 동안 한 번만 초기화하고 CoUninitialize는 부르지 않는다(아파트 유지)
        pythoncom.CoInitialize()
        while True:
            fn, args, kwargs, future = self._queue.get()
            if fn is None:
                # 정지 신호
                future.set_result(True)
                break
            try:
                future.set_result(_call_with_com_retry(fn, args, kwargs))
            except BaseException as e:
                future.set_exception(e)
            finally:
                self._queue.task_done()

    def submit(self, fn, *args, **kwargs):
        """COM 스레드에서 fn을 동기 실행하고 결과를 돌려준다(호출한 스레드는 기다린다)"""
        if threading.current_thread() is self._thread:
            # 이미 COM 스레드 위(내부 중첩 호출)면 교착을 피하려고 바로 실행한다
            return fn(*args, **kwargs)
        future = concurrent.futures.Future()
        self._queue.put((fn, args, kwargs, future))
        return future.result()

    async def run(self, fn, *args, **kwargs):
        """COM 호출을 비동기로 실행한다: 블로킹 submit을 스레드 풀로 보내 이벤트 루프를 막지 않는다"""
        if anyio is None:
            # anyio가 없으면 동기 실행으로 대체한다(일어나면 안 되는 경우)
            return self.submit(fn, *args, **kwargs)
        return await anyio.to_thread.run_sync(lambda: self.submit(fn, *args, **kwargs))


class CADController:
    """CAD 프로그램과 상호작용하는 CAD 컨트롤러"""

    def __init__(self):
        """CAD 컨트롤러를 초기화한다"""
        self.app = None
        self.doc = None
        self.entities = {}  # 만든 도형의 객체 참조를 저장해 나중에 수정할 때 쓴다
        # 전용 COM 스레드 실행기: 모든 COM 호출은 이것을 거쳐 순서대로 실행된다
        self.executor = COMExecutor()
        # 설정 파일에서 값을 불러온다
        self.startup_wait_time = config["cad"]["startup_wait_time"]
        self.command_delay = config["cad"]["command_delay"]
        # 명령 완료를 기다리는 최대 시간(초)
        self.command_timeout = float(config["cad"].get("command_timeout", 10))
        # CAD 메인 창 핸들. COM이 막혀도(대화상자·문자 편집기) ESC를 보낼 수 있게 미리 저장한다
        self._hwnd = None
        # 아직 돌아오지 않은 SendCommand 보조 스레드(BricsCAD는 명령이 끝날 때까지 SendCommand가 돌아오지 않는다)
        self._pending_send = None
        # CAD 종류
        self.cad_type = config["cad"]["type"]
        # 유효한 선굵기 값 목록
        self.valid_lineweights = [0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211]
        logger.info("CAD 컨트롤러 초기화 완료")

    def _get_app_info(self) -> Tuple[List[str], str]:
        """설정된 CAD 종류에 따라 (후보 COM ProgID 목록, 표시 이름)을 돌려준다

        지원: AutoCAD, GstarCAD(GCAD/GSTARCAD), ZWCAD, BricsCAD.
        참고: COM ProgID 조회는 대소문자를 구분하지 않는다. GCAD.Application과 Gcad.Application은 같다.
        같은 CAD라도 버전에 따라 등록하는 ProgID가 다를 수 있다(GstarCAD는 GCAD/GstarCAD 두 가지).
        그래서 후보 목록을 돌려주고 start_cad가 차례로 시도하게 해, 대상 PC에 다른 쪽만 등록돼 있어도 연결되게 한다.
        """
        # CAD별 COM ProgID 대응표(일치하지 않는 종류는 AutoCAD로 대체)
        app_map = {
            "autocad": (["AutoCAD.Application"], "AutoCAD"),
            "gcad": (["GCAD.Application", "GstarCAD.Application"], "GstarCAD"),
            "gstarcad": (["GCAD.Application", "GstarCAD.Application"], "GstarCAD"),
            "zwcad": (["ZWCAD.Application"], "ZWCAD"),
            "bricscad": (["BricscadApp.AcadApplication"], "BricsCAD"),
        }
        return app_map.get(self.cad_type.lower(), (["AutoCAD.Application"], "AutoCAD"))

    def start_cad(self) -> bool:
        """CAD를 시작하고 문서를 만들거나 연다"""
        # 이전 인스턴스 참조가 있으면 저장해 두었다가 나중에 정리한다
        old_app = None
        if self.app is not None:
            old_app = self.app
            self.app = None
            self.doc = None

        try:
            # COM 초기화(이미 COM 스레드면 여러 번 불러도 같은 결과)
            pythoncom.CoInitialize()

            app_ids, app_name = self._get_app_info()

            # 1단계: 이미 실행 중인 인스턴스에 연결을 시도한다(후보 ProgID를 차례로)
            for app_id in app_ids:
                try:
                    logger.info(f"실행 중인 {app_name} 인스턴스에 연결 시도({app_id})...")
                    self.app = win32com.client.GetActiveObject(app_id)
                    logger.info(f"실행 중인 {app_name} 인스턴스에 연결됨")
                    break
                except Exception as e:
                    logger.info(f"실행 중인 {app_name} 인스턴스를 찾지 못함({app_id}), 다음 시도: {str(e)}")

            # 2단계: 연결에 실패하면 새 인스턴스를 띄운다(마찬가지로 후보 ProgID를 차례로)
            if self.app is None:
                for app_id in app_ids:
                    try:
                        logger.info(f"{app_name} 인스턴스를 시작하는 중({app_id})...")
                        self.app = win32com.client.Dispatch(app_id)
                        self._call(setattr, self.app, "Visible", True)
                        break
                    except Exception as e:
                        logger.info(f"{app_name} 인스턴스 시작 실패({app_id}), 다음 시도: {str(e)}")
                if self.app is None:
                    raise Exception(f"{app_name}에 연결하거나 시작할 수 없습니다(후보 ProgID: {app_ids})")
                # CAD가 시작되기를 기다린다
                time.sleep(self.startup_wait_time)  # 설정된 대기 시간 사용

            # 3단계: 문서를 가져오거나 만든다(컬렉션과 속성 접근 모두 바쁜 CAD에 거부될 수 있어 호출마다 재시도)
            docs = self._call(getattr, self.app, "Documents")
            if self._call(getattr, docs, "Count") == 0:
                logger.info("새 문서를 만드는 중...")
                self.doc = self._call_attr(docs, "Add")
            else:
                try:
                    logger.info("활성 문서를 가져오는 중...")
                    self.doc = self._call(getattr, self.app, "ActiveDocument")
                except Exception:
                    # CAD가 "시작 시 새 문서 만들지 않음"으로 설정돼 있으면 활성 문서가 없어 직접 만든다.
                    # 주의: "문서를 억지로 얻으려고" 사용자가 연 도면을 닫으면 절대 안 된다.
                    # Close(False)는 저장하지 않은 수정을 그냥 버린다(옛 대체 로직이 그렇게 했는데,
                    # 데이터 손실급 결함이었다). 활성 문서를 못 얻으면 새로 만드는 것이 안전하다
                    logger.info("활성 문서가 없어 새 문서를 만듭니다...")
                    self.doc = self._call_attr(docs, "Add")

            # 추가 안전 확인과 대기
            time.sleep(2)  # CAD가 문서 생성을 처리할 시간을 더 준다

            if self.doc is None:
                raise Exception("유효한 Document 객체를 얻지 못했습니다")

            # 문서 속성을 읽어 유효한지 확인한다
            try:
                name = self._call(getattr, self.doc, "Name")
                logger.info(f"문서 이름: {name}")
            except Exception as name_ex:
                logger.error(f"문서 이름을 읽을 수 없음: {str(name_ex)}")
                raise Exception("문서 객체가 유효하지 않습니다")

            self._hwnd = self._safe_attr(self.app, "HWND")
            logger.info("CAD 시작 및 준비 완료")
            return True

        except Exception as e:
            logger.error(f"CAD 시작 실패: {str(e)}")
            return False
        finally:
            # 이전 인스턴스 정리
            if old_app is not None:
                try:
                    del old_app
                except:
                    pass

    def is_running(self) -> bool:
        """CAD가 실행 중(연결됨)인지 확인한다"""
        return self.app is not None and self.doc is not None

    def _sync_active_doc(self) -> bool:
        """CAD에서 지금 활성화된 도면을 작업 대상으로 삼는다(COM 스레드에서 호출)

        연결할 때 잡은 도면에 계속 붙어 있으면, 사용자가 CAD에서 다른 도면 탭으로 옮긴 뒤에도
        그리기가 이전 도면에 들어간다. 그래서 도구를 호출할 때마다 활성 도면을 따라간다.
        도면이 바뀌었으면 True를 돌려준다. 열린 도면이 하나도 없으면 self.doc을 None으로 둔다.
        CAD가 바빠 활성 도면을 읽지 못하면 지금 대상을 그대로 둔다.
        """
        if self.app is None:
            return False
        try:
            docs = self._call(getattr, self.app, "Documents")
            if self._call(getattr, docs, "Count") == 0:
                changed = self.doc is not None
                self.doc = None
                return changed
            active = self._call(getattr, self.app, "ActiveDocument")
        except Exception as e:
            logger.debug(f"활성 도면 확인 실패, 현재 대상 유지: {str(e)}")
            return False
        if active is None:
            return False
        if self.doc is not None and self._safe_attr(self.doc, "Name") == self._safe_attr(active, "Name"):
            return False
        logger.info(f"활성 도면으로 대상 변경: {self._safe_attr(active, 'Name')}")
        self.doc = active
        return True

    def _pt(self, point) -> "win32com.client.VARIANT":
        """좌표점을 COM이 받는 VARIANT double 배열로 감싼다

        2차원 좌표는 자동으로 3차원(Z=0)으로 채워 AddLine/AddCircle 등에 쓸 수 있게 한다.
        """
        if point is None or len(point) < 2:
            raise ValueError(f"좌표점에는 성분이 2개 이상 필요합니다: {point}")
        x, y = float(point[0]), float(point[1])
        z = float(point[2]) if len(point) > 2 else 0.0
        return win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [x, y, z])

    @staticmethod
    def _safe_attr(obj, name: str, default=None):
        """COM 객체 속성을 안전하게 읽는다. 객체 종류에 없는 속성이면 기본값을 돌려준다"""
        try:
            return getattr(obj, name)
        except Exception:
            return default

    @classmethod
    def _read_color(cls, obj) -> Optional[int]:
        """객체 색을 ACI 인덱스 정수로 읽는다(COM 스레드에서 호출)

        여러 COM 래핑 차이를 모두 처리한다:
        1. gen_py 정적 래핑(EnsureDispatch/makepy 캐시)은 속성 이름의 대소문자를 구분하며,
           AutoCAD 타입 라이브러리의 객체 색 속성은 소문자 color다
        2. 동적 디스패치에서는 IDispatch 이름이 대소문자를 구분하지 않아 Color로도 접근된다
        3. 값은 정수(옛 Color/color 속성)일 수도, AcadAcCmColor 객체(TrueColor)일 수도 있다
        """
        for name in ("color", "Color", "ColorIndex"):
            raw = cls._safe_attr(obj, name)
            if raw is None:
                continue
            if isinstance(raw, int):
                return raw
            # 새 버전 CAD는 AcadAcCmColor 객체를 돌려주므로 ColorIndex를 읽는다
            idx = cls._safe_attr(raw, "ColorIndex")
            if idx is not None:
                return idx
        return None

    @staticmethod
    def _write_color(entity, color: int) -> None:
        """객체 색을 ACI 인덱스로 설정한다(gen_py의 소문자 color와 동적 디스패치의 Color 두 래핑 모두 지원)"""
        try:
            entity.color = color  # gen_py 정적 래핑에서의 실제 속성 이름
        except AttributeError:
            entity.Color = color  # 동적 디스패치에서는 IDispatch가 대소문자를 구분하지 않는다

    def _call(self, fn, *args, **kwargs):
        """COM 호출 하나를 재시도와 함께 실행한다(COM 스레드에서만 호출)

        CAD가 바빠서(RPC_E_CALL_REJECTED) 호출이 거부됐다는 것은 그 호출이 실행되지 않았다는 뜻이므로,
        COM 호출 하나를 재시도하는 것은 안전하다. 반면 업무 메서드 전체를 재시도하면 중복 그리기가 생길 수 있다
        (메서드 앞부분은 성공하고 뒷부분이 거부된 경우). 그래서 재시도 단위는 반드시 호출 단위여야 한다.
        """
        try:
            return _call_with_com_retry(fn, args, kwargs)
        except Exception as e:
            # 사용자가 CAD 프로세스를 닫으면 들고 있던 COM 참조가 모두 무효가 된다(재시도 의미 없음).
            # 연결 상태를 초기화해 다음 작업이 is_running()==False 분기로 가서 자동으로 다시 연결하게 한다.
            # 그러지 않으면 모든 도구가 계속 "작업 실패"만 내고 재시작이 영영 일어나지 않는다
            if getattr(e, "hresult", None) in _DEAD_COM_HRESULTS:
                logger.warning("CAD 프로세스 종료 감지, 연결 상태 초기화(다음 작업에서 CAD를 다시 시작함)")
                self.app = None
                self.doc = None
            raise

    def _call_attr(self, obj, name: str, *args, **kwargs):
        """obj.name(*args)를 재시도와 함께 실행한다: 멤버 조회와 호출을 모두 재시도로 보호한다

        동적 디스패치(gen_py 캐시가 없는 PC, 즉 최종 사용자의 일반적인 환경)에서는 obj.name
        조회 자체도 COM 호출(GetIDsOfNames)이라 바쁜 CAD에 거부될 수 있다.
        그래서 self._call(obj.name, ...)처럼 쓰면 안 된다. 조회가 _call에 들어가기 전에 일어나기 때문이다.
        이 메서드는 조회와 호출을 하나의 재시도 단위로 묶고,
        pywin32가 바꿔 낸 "조회 거부" AttributeError도 일시 오류로 재시도한다.
        """
        return _call_with_com_retry(lambda: getattr(obj, name)(*args, **kwargs),
                                    (), {}, retry_attr=name)

    def _ms(self):
        """모형 공간을 가져온다(속성 읽기도 바쁜 CAD에 거부될 수 있어 재시도)"""
        return self._call(getattr, self.doc, "ModelSpace")

    @contextmanager
    def _undo_group(self):
        """이번 도구 작업을 독립된 실행 취소 그룹으로 묶는다(UNDO 1이 정확히 한 단계를 취소하게)

        AutoCAD는 명령행이 쉬는 동안의 모든 COM 수정을 하나의 실행 취소 그룹으로 합친다.
        표시를 하지 않으면 UNDO 한 번에 그린 것 전체가 취소될 수 있다.
        중첩해서 써도 안전하다(예: draw_rectangle→draw_polyline). 안쪽 그룹은 바깥 그룹에 들어간다.
        """
        doc = self.doc
        # 표시 호출도 바쁜 CAD에 거부될 수 있어 재시도
        self._call_attr(doc, "StartUndoMark")
        try:
            yield
        finally:
            try:
                self._call_attr(doc, "EndUndoMark")
            except Exception:
                # 문서가 바뀌는 등 극단적인 경우에는 표시를 마무리할 수 없어 무시한다(표시는 문서와 함께 초기화된다)
                pass

    def _check_target_not_occupied(self, file_path: str) -> None:
        """저장할 경로를 다른 열린 도면이 쓰고 있는지 확인한다

        AutoCAD는 열린 문서의 DWG 파일을 잠근다. 그런 경로로 SaveAs를 하면
        "문서 저장 중 오류"(-2145320861)가 계속 나고 재시도도 소용없다(40초를 기다려도 실패함을 확인).
        그래서 미리 검사해 빨리 실패한다. 현재 문서가 같은 경로를 쓰는 것은 제자리 저장이므로 허용한다.
        """
        try:
            norm = os.path.normcase(file_path)
            current_name = self._safe_attr(self.doc, "Name")
            docs = self._call(getattr, self.app, "Documents")
            for i in range(self._call(getattr, docs, "Count")):
                doc = self._call_attr(docs, "Item", i)
                full = self._safe_attr(doc, "FullName")
                if full and os.path.normcase(str(full)) == norm:
                    # 열린 문서의 이름은 같은 CAD 세션 안에서 고유하므로, 이름이 같으면 현재 문서다
                    if self._safe_attr(doc, "Name") == current_name:
                        continue
                    raise ValueError(
                        f"대상 파일을 다른 열린 도면이 쓰고 있어 덮어쓸 수 없습니다: {file_path}(그 도면을 먼저 닫으세요)"
                    )
        except ValueError:
            raise
        except Exception:
            # 사전 검사 자체가 실패하면(예: CAD 바쁨) 저장을 막지 않고 SaveAs와 호출 단위 재시도에 맡긴다
            pass

    def save_drawing(self, file_path: str) -> bool:
        """현재 도면을 지정한 경로에 저장한다

        Raises:
            ValueError: 대상 파일을 다른 열린 도면이 쓰고 있을 때
        """
        if not self.is_running():
            logger.error("CAD 미연결, 도면을 저장할 수 없음")
            return False

        try:
            # 상대 경로는 먼저 절대 경로로 바꿔야 한다: SaveAs는 CAD 프로세스 안에서 경로를 해석하는데,
            # 그 작업 폴더는 보통 MCP 서버의 작업 폴더와 다르다(상대 경로 파일이 엉뚱한 곳에 저장되는 것을 확인)
            file_path = os.path.abspath(file_path)

            # 폴더가 있는지 확인한다(abspath 뒤에는 dirname이 반드시 있다. 폴더가 아직 없을 수 있다)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            # 대상이 사용 중이면 SaveAs가 계속 실패하고 재시도도 소용없으므로 미리 빨리 실패한다
            self._check_target_not_occupied(file_path)

            # 파일 저장(SaveAs가 돌아온 뒤에도 CAD가 잠깐 이후 수정 호출을 거부할 수 있는데,
            # 이후 도구의 COM 호출은 호출 단위 재시도로 회복을 자동으로 기다린다)
            self._call_attr(self.doc, "SaveAs", file_path)
            logger.info(f"도면 저장: {file_path}")

            return True
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"도면 저장 실패: {str(e)}")
            return False

    def refresh_view(self) -> None:
        """뷰를 강제로 재생성한다(명시적으로 새로 고칠 때만 쓰고, 그리기/편집 뒤에는 부르지 않는다)

        주의: AutoCAD는 COM 수정 뒤의 재생성(Regen)을 실행 취소 스택에 기록한다. 그러면
        이어지는 UNDO가 눈에 보이지 않는 빈 작업이 된다(실행 취소 표시 여부와 상관없이 확인됨).
        그래서 그리기/편집 메서드는 더 이상 자동으로 새로 고치지 않는다. 새 객체는 자동으로 보이고,
        전체를 새로 고쳐야 하면 zoom_extents나 send_command로 ZOOM을 실행한다.
        """
        if self.is_running():
            try:
                self._call_attr(self.doc, "Regen", 1)  # acAllViewports = 1
            except Exception as e:
                logger.error(f"뷰 새로 고침 실패: {str(e)}")

    def validate_lineweight(self, lineweight) -> int:
        """유효한 선굵기 값인지 확인해 돌려준다

        주어진 선굵기 값이 유효한 값 목록에 없으면 기본값 0을 돌려준다

        Args:
            lineweight: 확인할 선굵기 값

        Returns:
            유효한 선굵기 값
        """
        if lineweight is None:
            return None

        # 선굵기가 유효한 값 목록에 있는지 확인한다
        if lineweight in self.valid_lineweights:
            return lineweight
        else:
            logger.warning(f"선굵기 값 {lineweight}이(가) 유효하지 않아 기본값 0을 씁니다")
            return 0

    def _apply_style(self, entity, layer=None, color=None, lineweight=None) -> None:
        """객체에 레이어/색/선굵기를 한꺼번에 설정한다(그리기 메서드들의 공통 부분)

        속성 설정은 멱등 작업이라 하나씩 재시도와 함께 실행한다: 한 속성이 CAD에 거부돼 재시도해도
        이미 성공한 부분에는 영향이 없다.
        """
        # 레이어를 지정했으면 레이어를 설정한다
        if layer:
            # 레이어가 있도록 보장한다(만들기만 하고 현재로 지정하지 않는다: 사용자가 쓰던 현재 레이어를 몰래 바꾸지 않기 위해)
            target_layer = self._ensure_layer(layer)
            # 객체의 레이어를 설정한다(CAD가 돌려준 실제 레이어 이름을 써서 대소문자 불일치를 피한다)
            self._call(setattr, entity, "Layer", self._call(getattr, target_layer, "Name"))

        # 색을 지정했으면 색을 설정한다
        if color is not None:
            self._call(self._write_color, entity, color)

        if lineweight is not None:
            lw = self.validate_lineweight(lineweight)
            # gen_py 래핑에서는 속성 이름이 Lineweight다(LineWeight와의 표기 차이는 Color/color와 같은 문제)
            try:
                self._call(setattr, entity, "Lineweight", lw)
            except AttributeError:
                self._call(setattr, entity, "LineWeight", lw)

    def draw_line(self, start_point: Tuple[float, float, float],
                 end_point: Tuple[float, float, float], layer: str = None, color: int = None, lineweight=None) -> Any:
        """선을 그린다"""
        if not self.is_running():
            return None

        try:
            # 좌표점 데이터를 VARIANT로 감싼다(_pt가 Z축을 자동으로 채운다)
            start_array = self._pt(start_point)
            end_array = self._pt(end_point)

            # 선 추가(독립 실행 취소 그룹, 바쁘면 호출 단위로 자동 재시도)
            with self._undo_group():
                line = self._call_attr(self._ms(), "AddLine", start_array, end_array)

                # 레이어, 색, 선굵기 설정
                self._apply_style(line, layer, color, lineweight)

            logger.debug(f"선 그림: 시작 {start_point}, 끝 {end_point}, 레이어 {layer if layer else '기본'}, 색 {color if color is not None else '기본'}")
            return line

        except Exception as e:
            logger.error(f"선 그리기 오류: {str(e)}")
            return None

    def draw_circle(self, center: Tuple[float, float, float],
                   radius: float, layer: str = None, color: int = None, lineweight=None) -> Any:
        """원을 그린다"""
        if not self.is_running():
            return None

        # 반지름은 양수여야 한다(CAD는 0과 음수 반지름을 거부함). 미리 실패하고 분명한 로그를 남긴다
        if radius <= 0:
            logger.error(f"원 그리기 실패: 반지름({radius})은 양수여야 합니다")
            return None

        try:
            # 좌표점 데이터를 VARIANT로 감싼다(_pt가 Z축을 자동으로 채운다)
            center_array = self._pt(center)

            # 원 추가(독립 실행 취소 그룹, 바쁘면 호출 단위로 자동 재시도)
            with self._undo_group():
                circle = self._call_attr(self._ms(), "AddCircle", center_array, radius)

                # 레이어, 색, 선굵기 설정
                self._apply_style(circle, layer, color, lineweight)

            logger.debug(f"원 그림: 중심 {center}, 반지름 {radius}, 레이어 {layer if layer else '기본'}, 색 {color if color is not None else '기본'}")
            return circle

        except Exception as e:
            logger.error(f"원 그리기 오류: {str(e)}")
            return None

    def draw_arc(self, center: Tuple[float, float, float],
                radius: float, start_angle: float, end_angle: float, layer: str = None, color: int = None, lineweight=None) -> Any:
        """호를 그린다"""
        if not self.is_running():
            return None

        # 반지름은 양수여야 한다(CAD는 0과 음수 반지름을 거부함). 미리 실패하고 분명한 로그를 남긴다
        if radius <= 0:
            logger.error(f"호 그리기 실패: 반지름({radius})은 양수여야 합니다")
            return None

        try:
            # 각도를 라디안으로 바꾼다
            start_rad = math.radians(start_angle)
            end_rad = math.radians(end_angle)

            # 좌표점 데이터를 VARIANT로 감싼다
            center_array = self._pt(center)

            # 호 추가(독립 실행 취소 그룹, 바쁘면 호출 단위로 자동 재시도)
            with self._undo_group():
                arc = self._call_attr(self._ms(), "AddArc", center_array, radius, start_rad, end_rad)

                # 레이어, 색, 선굵기 설정
                self._apply_style(arc, layer, color, lineweight)

            logger.debug(f"호 그림: 중심 {center}, 반지름 {radius}, 시작 각도 {start_angle}, 끝 각도 {end_angle}, 레이어 {layer if layer else '기본'}, 색 {color if color is not None else '기본'}")
            return arc
        except Exception as e:
            logger.error(f"호 그리기 실패: {str(e)}")
            return None

    def draw_ellipse(self, center: Tuple[float, float, float],
                    major_axis: float, minor_axis: float, rotation: float = 0,
                    layer: str = None, color: int = None, lineweight=None) -> Any:
        """타원을 그린다"""
        if not self.is_running():
            return None

        # 장축/단축은 양수여야 하고 장축이 단축보다 작으면 안 된다. AddEllipse의 RadiusRatio 인자는
        # 단축/장축이라 (0,1] 구간에 있어야 하며, 장축이 0이면 여기서 0으로 나누게 된다
        if major_axis <= 0 or minor_axis <= 0 or minor_axis > major_axis:
            logger.error(f"타원 그리기 실패: 장축({major_axis})은 양수이고 단축({minor_axis})보다 작지 않아야 합니다")
            return None

        try:
            if rotation is None:
                rotation = 0

            # 회전 각도를 라디안으로 바꾼다
            rotation_rad = math.radians(rotation)

            # 좌표점 데이터를 VARIANT로 감싼다
            center_array = self._pt(center)

            # 타원의 장축 벡터를 계산한다
            major_x = major_axis * math.cos(rotation_rad)
            major_y = major_axis * math.sin(rotation_rad)
            major_vector = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8,
                                               [major_x, major_y, 0])

            # 타원 추가(독립 실행 취소 그룹, 바쁘면 호출 단위로 자동 재시도)
            with self._undo_group():
                ellipse = self._call_attr(self._ms(), "AddEllipse", center_array, major_vector, minor_axis / major_axis)

                # 레이어, 색, 선굵기 설정
                self._apply_style(ellipse, layer, color, lineweight)

            logger.debug(f"타원 그림: 중심 {center}, 장축 {major_axis}, 단축 {minor_axis}, 회전 각도 {rotation}, 레이어 {layer if layer else '기본'}, 색 {color if color is not None else '기본'}")
            return ellipse
        except Exception as e:
            logger.error(f"타원 그리기 실패: {str(e)}")
            return None

    def draw_polyline(self, points: List[Tuple[float, float, float]], closed: bool = False, layer: str = None, color: int = None, lineweight=None) -> Any:
        """폴리선을 그린다"""
        if not self.is_running():
            return None

        try:
            # 모든 점을 3차원으로 맞춘다
            processed_points = []
            for point in points:
                if len(point) == 2:
                    processed_points.append((point[0], point[1], 0))
                else:
                    processed_points.append(point)

            # 점 배열을 만든다
            point_array = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8,
                                                [coord for point in processed_points for coord in point])

            # 폴리선 추가(독립 실행 취소 그룹, 바쁘면 호출 단위로 자동 재시도)
            with self._undo_group():
                polyline = self._call_attr(self._ms(), "AddPolyline", point_array)

                # 닫아야 하면 닫는다
                if closed and len(processed_points) > 2:
                    self._call(setattr, polyline, "Closed", True)

                # 레이어, 색, 선굵기 설정
                self._apply_style(polyline, layer, color, lineweight)

            logger.debug(f"폴리선 그림: 점 {len(points)}개, {'닫힘' if closed else '열림'}, 레이어 {layer if layer else '기본'}, 색 {color if color is not None else '기본'}")
            return polyline
        except Exception as e:
            logger.error(f"폴리선 그리기 오류: {str(e)}")
            return None

    def draw_rectangle(self, corner1: Tuple[float, float, float],
                      corner2: Tuple[float, float, float], layer: str = None, color: int = None, lineweight=None) -> Any:
        """사각형을 그린다"""
        if not self.is_running():
            return None

        try:
            # 점을 3차원으로 맞춘다
            if len(corner1) == 2:
                corner1 = (corner1[0], corner1[1], 0)
            if len(corner2) == 2:
                corner2 = (corner2[0], corner2[1], 0)

            # 사각형의 네 모서리를 계산한다
            x1, y1, z1 = corner1
            x2, y2, z2 = corner2

            # 사각형의 네 점을 만든다
            points = [
                (x1, y1, z1),
                (x2, y1, z1),
                (x2, y2, z1),
                (x1, y2, z1),
                (x1, y1, z1)  # 사각형을 닫는다
            ]

            # 폴리선으로 사각형을 그린다
            return self.draw_polyline(points, True, layer, color, lineweight)
        except Exception as e:
            logger.error(f"사각형 그리기 오류: {str(e)}")
            return None

    def draw_text(self, position: Tuple[float, float, float],
                 text: str, height: float = 2.5, rotation: float = 0, layer: str = None, color: int = None) -> Any:
        """문자를 넣는다"""
        if not self.is_running():
            return None

        try:
            # 좌표점 데이터를 VARIANT로 감싼다(_pt가 Z축을 자동으로 채운다)
            position_array = self._pt(position)

            # 문자 추가(독립 실행 취소 그룹, 바쁘면 호출 단위로 자동 재시도)
            with self._undo_group():
                text_obj = self._call_attr(self._ms(), "AddText", text, position_array, height)

                # 회전 각도 설정
                if rotation != 0:
                    self._call(setattr, text_obj, "Rotation", math.radians(rotation))

                # 레이어, 색 설정
                self._apply_style(text_obj, layer, color)

            logger.debug(f"문자 넣음: '{text}', 위치 {position}, 높이 {height}, 회전 {rotation}도, 레이어 {layer if layer else '기본'}, 색 {color if color is not None else '기본'}")
            return text_obj
        except Exception as e:
            logger.error(f"문자 넣기 오류: {str(e)}")
            return None

    def draw_hatch(self, points: List[Tuple[float, float, float]],
                  pattern_name: str = "SOLID", scale: float = 1.0, layer: str = None, color: int = None) -> Any:
        """해치를 그린다

        Args:
            points: 해치 경계 점 목록. 점마다 2차원 또는 3차원 좌표 튜플
            pattern_name: 해치 패턴 이름. 기본값 "SOLID"(솔리드 채우기)
            scale: 해치 패턴 축척. 기본값 1.0
            layer: 레이어 이름. None이면 현재 레이어를 쓴다
            color: 색상 인덱스. None이면 기본 색을 쓴다

        Returns:
            성공하면 해치 객체, 실패하면 None
        """
        if not self.is_running():
            return None

        closed_polyline = None
        hatch = None
        try:
            # 점이 모두 유효한지 확인한다
            if not points or len(points) < 3:
                logger.error("해치 만들기 실패: 해치 경계를 정하려면 점이 3개 이상 필요합니다")
                return None

            # 경계 폴리선을 만든다(안쪽에 이미 독립 실행 취소 그룹이 있고, 바깥 그룹이 해치+경계를 한 단계로 묶는다)
            with self._undo_group():
                closed_polyline = self.draw_polyline(points, closed=True, layer=layer)
                if not closed_polyline:
                    logger.error("해치 만들기 실패: 경계 폴리선을 만들 수 없음")
                    return None

                # 해치 객체를 만든다(True는 경계와 연관)
                hatch = self._add_hatch(pattern_name)

                # 바깥 경계 루프 추가
                # 객체 배열을 VARIANT로 감싼다
                object_ids = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [closed_polyline])
                self._call_attr(hatch, "AppendOuterLoop", object_ids)

                # 해치 패턴 축척 설정
                self._call(setattr, hatch, "PatternScale", scale)

                # 레이어, 색 설정
                self._apply_style(hatch, layer, color)

                # 해치 갱신(해치 영역 다시 계산). GstarCAD 2021은 Evaluate에서 서버 오류(RPC_E_SERVERFAULT)를 내지만,
                # AppendOuterLoop만으로 해치가 이미 완성돼 있다(면적이 맞게 계산됨을 확인). 그래서 실패해도 해치를 유지한다
                try:
                    self._call_attr(hatch, "Evaluate")
                except Exception as e:
                    logger.info(f"해치 Evaluate 실패(해치는 유지): {str(e)}")

            logger.debug(f"해치 만듦: 패턴 {pattern_name}, 축척 {scale}, 레이어 {layer if layer else '기본'}, 색 {color if color is not None else '기본'}")
            return hatch
        except Exception as e:
            # 실패하면 이미 만든 해치와 경계 폴리선을 지워 도면에 고아 객체가 남지 않게 한다
            # (호출한 쪽은 "실패"를 봤는데 도면에 객체가 남아 있으면 안 된다)
            for leftover in (hatch, closed_polyline):
                if leftover is not None:
                    try:
                        self._call_attr(leftover, "Delete")
                    except Exception:
                        pass
            logger.error(f"해치 만들기 오류: {str(e)}")
            return None

    def _add_hatch(self, pattern_name: str):
        """모형 공간에 해치 객체를 만든다(패턴 유형을 CAD에 맞춰 고른다)

        AddHatch의 첫 인자는 패턴 유형이다(0 사용자 정의, 1 기본 제공, 2 사용자 파일).
        SOLID/ANSI31 같은 기본 패턴은 1이 맞다. 원본 코드는 0을 썼는데(직접 확인):
        - BricsCAD V26: 0은 "해치 패턴에 대한 잘못된 이름" 오류, 1은 된다
        - GstarCAD 2021: 0과 1 모두 된다
        그래서 1을 먼저 쓰고, 실패하면 0으로 다시 시도한다(실패한 시도는 객체를 남기지 않는다).
        """
        last_error = None
        for pattern_type in (1, 0):
            try:
                return self._call_attr(self._ms(), "AddHatch", pattern_type, pattern_name, True)
            except Exception as e:
                last_error = e
                logger.info(f"AddHatch(패턴 유형 {pattern_type}) 실패, 다음 유형 시도: {str(e)}")
        raise last_error

    def zoom_extents(self) -> bool:
        """모든 객체가 보이도록 뷰를 줌한다"""
        if not self.is_running():
            return False

        try:
            # ZoomExtents는 Application 객체의 메서드다(ActiveViewport가 아님)
            self._call_attr(self.app, "ZoomExtents")
            logger.info("모든 객체가 보이도록 뷰를 줌함")
            return True
        except Exception as e:
            logger.error(f"뷰 줌 오류: {str(e)}")
            return False

    def close(self) -> None:
        """CAD 컨트롤러를 닫는다

        참고: COM 아파트는 COMExecutor 스레드가 들고 있고 프로세스가 끝날 때 시스템이 회수한다.
        여기서는 참조만 놓고, 스레드를 넘어 CoUninitialize를 부르지 않는다.
        """
        try:
            # COM 자원 해제
            if self.app is not None:
                del self.app
        except:
            pass

    def _ensure_layer(self, layer_name: str):
        """레이어가 있도록 보장하고 레이어 객체를 돌려준다(이미 있으면 그대로 돌려주며 현재 레이어는 바꾸지 않는다)

        그릴 때 레이어를 지정하면 여기를 거친다: 객체가 대상 레이어에 들어가게만 보장하고,
        사용자가 쓰던 현재 레이어를 몰래 바꾸지 않는다.
        Layers 컬렉션과 Name 읽기 모두 바쁜 CAD에 거부될 수 있어 호출마다 재시도한다.
        """
        layers = self._call(getattr, self.doc, "Layers")
        for i in range(self._call(getattr, layers, "Count")):
            layer = self._call_attr(layers, "Item", i)
            # CAD 레이어 이름은 대소문자를 구분하지 않으므로 비교할 때 대소문자를 무시한다
            # (그러지 않으면 "wall"이 이미 있는 "WALL"에 대해 다시 Add되어 CAD에 거부된다)
            if self._call(getattr, layer, "Name").casefold() == layer_name.casefold():
                return layer
        # 없으면 만든다(독립 실행 취소 그룹)
        with self._undo_group():
            return self._call_attr(layers, "Add", layer_name)

    def create_layer(self, layer_name: str) -> bool:    # , color: Union[int, Tuple[int, int, int]] = 7
        """새 레이어를 만들고 현재 레이어로 지정한다(이미 있으면 그 레이어를 현재로 지정)

        Args:
            layer_name: 레이어 이름
            color: 색 값. CAD 색상 인덱스(int) 또는 RGB 값(tuple)

        Returns:
            작업 성공 여부
        """
        if not self.is_running():
            return False

        try:
            # 레이어가 있도록 보장한다(있으면 그대로 쓰고, 없으면 만든다)
            new_layer = self._ensure_layer(layer_name)

            # 레이어에는 색을 설정하지 않고, 그 안의 객체 색을 설정한다
            # # 색 설정
            # if isinstance(color, int):
            #     # 색상 인덱스 사용
            #     new_layer.Color = color
            # elif isinstance(color, tuple) and len(color) == 3:
            #     # RGB 값 사용
            #     r, g, b = color
            #     # TrueColor 설정
            #     new_layer.TrueColor = self._create_true_color(r, g, b)

            # 현재 레이어로 지정한다
            self._call(setattr, self.doc, "ActiveLayer", new_layer)
            logger.info(f"레이어를 만들고 현재 레이어로 지정함: {layer_name}")  #, 색: {color}
            return True
        except Exception as e:
            logger.error(f"레이어 만들기 오류: {str(e)}")
            return False

    def add_dimension(self, start_point: Tuple[float, float, float],
                     end_point: Tuple[float, float, float],
                     text_position: Tuple[float, float, float] = None, textheight: float = 5, layer: str = None, color: int=None) -> Any:
            """선형 치수를 넣는다"""
            if not self.is_running():
                return None

            try:
                # 문자 위치를 주지 않으면 자동으로 계산한다
                if text_position is None:
                    # 시작점과 끝점 사이 중점의 위쪽
                    mid_x = (start_point[0] + end_point[0]) / 2
                    mid_y = (start_point[1] + end_point[1]) / 2
                    text_position = (mid_x, mid_y + 5, 0)

                # 좌표점 데이터를 VARIANT로 감싼다(_pt가 Z축을 자동으로 채운다)
                start_array = self._pt(start_point)
                end_array = self._pt(end_point)
                text_pos_array = self._pt(text_position)

                # 정렬 치수 추가(독립 실행 취소 그룹, 바쁘면 호출 단위로 자동 재시도)
                with self._undo_group():
                    dimension = self._call_attr(self._ms(), "AddDimAligned", start_array, end_array, text_pos_array)

                    # 문자 높이 설정
                    if textheight is not None:
                        self._call(setattr, dimension, "TextHeight", textheight)

                    # 레이어, 색 설정
                    self._apply_style(dimension, layer, color)

                logger.info(f"치수 넣음: {start_point}에서 {end_point}까지, 레이어 {layer if layer else '기본'}")
                return dimension
            except Exception as e:
                logger.error(f"치수 넣기 오류: {str(e)}")
                return None

    # ==================== 조회 작업(CAD 문서 상태를 실시간으로 읽음) ====================

    @staticmethod
    def _friendly_type(object_name: str) -> str:
        """COM 객체 종류 이름(예: AcDbLine)을 알기 쉬운 이름(예: line)으로 바꾼다"""
        mapping = {
            "AcDbLine": "line",
            "AcDbCircle": "circle",
            "AcDbArc": "arc",
            "AcDbEllipse": "ellipse",
            "AcDbPolyline": "polyline",       # 경량 폴리선
            "AcDb2dPolyline": "polyline",
            "AcDb3dPolyline": "polyline",
            "AcDbText": "text",
            "AcDbMText": "mtext",
            "AcDbHatch": "hatch",
            "AcDbBlockReference": "block_reference",
            "AcDbDimension": "dimension",
            "AcDbAlignedDimension": "dimension",
            "AcDbRotatedDimension": "dimension",
        }
        return mapping.get(object_name, object_name)

    def get_entity_info(self, entity) -> Dict[str, Any]:
        """객체 요약 정보를 얻는다(핸들/종류/레이어/색)"""
        return {
            "handle": str(self._safe_attr(entity, "Handle", "")),
            "entity_type": self._friendly_type(str(self._safe_attr(entity, "ObjectName", ""))),
            "layer": self._safe_attr(entity, "Layer"),
            "color": self._read_color(entity),
        }

    def list_layers(self) -> List[Dict[str, Any]]:
        """현재 도면의 모든 레이어 정보를 나열한다"""
        if not self.is_running():
            return []

        layers = []
        # 컬렉션과 속성 읽기 모두 바쁜 CAD에 거부될 수 있어 호출마다 재시도
        active_name = self._call(getattr, self._call(getattr, self.doc, "ActiveLayer"), "Name")
        layer_set = self._call(getattr, self.doc, "Layers")
        for i in range(self._call(getattr, layer_set, "Count")):
            layer = self._call_attr(layer_set, "Item", i)
            name = self._call(getattr, layer, "Name")
            layers.append({
                "name": name,
                "color": self._read_color(layer),
                "is_current": name == active_name,
                "is_frozen": bool(self._safe_attr(layer, "Freeze", False)),
                "is_locked": bool(self._safe_attr(layer, "Lock", False)),
                "is_on": bool(self._safe_attr(layer, "LayerOn", True)),
            })
        return layers

    def list_entities(self, entity_type: str = None, limit: int = 50,
                      layer: str = None, color: int = None) -> Dict[str, Any]:
        """모형 공간의 객체를 나열한다(실시간 조회, CAD 문서와 항상 일치)

        Args:
            entity_type: 종류로 거른다(line/circle/arc 등, 대소문자 무시). None이면 거르지 않는다
            limit: 돌려줄 최대 객체 수(큰 도면에서 한 번에 너무 많이 돌려주지 않기 위해)
            layer: 레이어 이름으로 거른다(대소문자 무시). None이면 거르지 않는다
            color: 객체 색상 인덱스(ACI)로 거른다. 256은 ByLayer. None이면 거르지 않는다

        Returns:
            {"total": 모형 공간 객체 총수, "entities": [객체 정보]}
        """
        if not self.is_running():
            return {"total": 0, "entities": []}

        ms = self._ms()
        # Count와 Item 접근 모두 바쁜 CAD에 거부될 수 있어 호출마다 재시도
        total = self._call(getattr, ms, "Count")
        entities = []
        for i in range(total):
            info = self.get_entity_info(self._call_attr(ms, "Item", i))
            # 종류 이름을 정확히 비교한다(부분 문자열 비교면 "line"에 polyline까지 걸린다)
            if entity_type and info["entity_type"].lower() != entity_type.lower():
                continue
            if layer and str(info["layer"] or "").casefold() != layer.casefold():
                continue
            if color is not None and info["color"] != color:
                continue
            entities.append(info)
            if len(entities) >= limit:
                break
        return {"total": total, "entities": entities}

    def get_entity_properties(self, handle: str) -> Dict[str, Any]:
        """핸들로 객체 상세 속성을 얻는다(기하 정보는 객체 종류마다 다르다)"""
        if not self.is_running():
            raise RuntimeError("CAD 미연결")

        entity = self._get_entity_by_handle(handle)
        object_name = str(self._safe_attr(entity, "ObjectName", ""))
        props: Dict[str, Any] = {
            "handle": str(handle),
            "entity_type": self._friendly_type(object_name),
            "layer": self._safe_attr(entity, "Layer"),
            "color": self._read_color(entity),
            "linetype": self._safe_attr(entity, "Linetype"),
            "lineweight": self._safe_attr(entity, "Lineweight"),
        }

        # 객체 종류에 따라 기하 속성을 덧붙인다
        detail: Dict[str, Any] = {}
        if object_name == "AcDbLine":
            detail["start_point"] = list(self._safe_attr(entity, "StartPoint") or [])
            detail["end_point"] = list(self._safe_attr(entity, "EndPoint") or [])
            detail["length"] = self._safe_attr(entity, "Length")
        elif object_name == "AcDbCircle":
            detail["center"] = list(self._safe_attr(entity, "Center") or [])
            detail["radius"] = self._safe_attr(entity, "Radius")
            detail["area"] = self._safe_attr(entity, "Area")
        elif object_name == "AcDbArc":
            detail["center"] = list(self._safe_attr(entity, "Center") or [])
            detail["radius"] = self._safe_attr(entity, "Radius")
            # StartAngle/EndAngle은 라디안이라 이해하기 쉽게 도로 바꾼다
            start_rad = self._safe_attr(entity, "StartAngle")
            end_rad = self._safe_attr(entity, "EndAngle")
            detail["start_angle"] = math.degrees(start_rad) if start_rad is not None else None
            detail["end_angle"] = math.degrees(end_rad) if end_rad is not None else None
        elif object_name == "AcDbEllipse":
            detail["center"] = list(self._safe_attr(entity, "Center") or [])
            detail["radius_ratio"] = self._safe_attr(entity, "RadiusRatio")
            detail["start_param"] = self._safe_attr(entity, "StartParam")
            detail["end_param"] = self._safe_attr(entity, "EndParam")
        elif object_name == "AcDbText":
            detail["text"] = self._safe_attr(entity, "TextString")
            detail["insertion_point"] = list(self._safe_attr(entity, "InsertionPoint") or [])
            detail["height"] = self._safe_attr(entity, "Height")
            rotation_rad = self._safe_attr(entity, "Rotation")
            detail["rotation"] = math.degrees(rotation_rad) if rotation_rad is not None else None
        elif object_name == "AcDbMText":
            detail["text"] = self._safe_attr(entity, "TextString")
            detail["insertion_point"] = list(self._safe_attr(entity, "InsertionPoint") or [])
        elif object_name in ("AcDbPolyline", "AcDb2dPolyline", "AcDb3dPolyline"):
            # Coordinates는 평평한 좌표 배열이라, 종류에 따라 점당 성분 수를 정해 자른다:
            # 경량 폴리선(AcDbPolyline)은 성분 2개 [x,y,...],
            # 옛 2D/3D 폴리선(AcDb2dPolyline/AcDb3dPolyline)은 성분 3개 [x,y,z,...]다.
            # 모두 2개씩 자르면 z를 다음 점의 x로 읽어 좌표가 전부 어긋난다
            coords = self._safe_attr(entity, "Coordinates") or []
            step = 2 if object_name == "AcDbPolyline" else 3
            detail["coordinates"] = [list(coords[i:i + step]) for i in range(0, len(coords), step)]
            detail["closed"] = bool(self._safe_attr(entity, "Closed", False))
            detail["length"] = self._safe_attr(entity, "Length")
        elif object_name == "AcDbHatch":
            detail["pattern_name"] = self._safe_attr(entity, "PatternName")
            detail["area"] = self._safe_attr(entity, "Area")
        elif object_name == "AcDbBlockReference":
            detail["name"] = self._safe_attr(entity, "Name")
            detail["insertion_point"] = list(self._safe_attr(entity, "InsertionPoint") or [])
            detail["x_scale"] = self._safe_attr(entity, "XScaleFactor")
            detail["y_scale"] = self._safe_attr(entity, "YScaleFactor")
            rotation_rad = self._safe_attr(entity, "Rotation")
            detail["rotation"] = math.degrees(rotation_rad) if rotation_rad is not None else None

        props["properties"] = detail
        return props

    def get_drawing_stats(self) -> Dict[str, Any]:
        """현재 문서의 실시간 통계를 얻는다(객체 수/레이어 수/문서 이름)"""
        if not self.is_running():
            return {"running": False}
        return {
            "running": True,
            # 속성 읽기마다 바쁜 CAD에 거부될 수 있어 호출마다 재시도
            "entity_count": self._call(getattr, self._ms(), "Count"),
            "layer_count": self._call(getattr, self._call(getattr, self.doc, "Layers"), "Count"),
            "document_name": self._call(getattr, self.doc, "Name"),
        }

    def screenshot(self) -> bytes:
        """CAD 메인 창을 캡처해 PNG 바이트를 돌려준다"""
        if not self.is_running():
            raise RuntimeError("CAD 미연결")

        import win32gui
        import win32con
        from PIL import ImageGrab

        # 캡처 전에 모든 뷰포트를 강제로 재생성한다: GstarCAD는 COM으로 한꺼번에 그린 뒤 화면을 자동으로 새로 고치지 않아,
        # 재생성하지 않으면 그리기 전의 옛 화면이 찍힌다(객체는 이미 들어가 있고 표시만 늦음)
        try:
            self._call_attr(self.doc, "Regen", 1)  # 1 = acAllViewports
        except Exception as e:
            logger.warning(f"캡처 전 재생성 실패(그래도 캡처를 시도함): {str(e)}")

        # CAD 메인 창 핸들(속성 읽기가 바쁜 CAD에 거부될 수 있어 재시도)
        hwnd = self._call(getattr, self.app, "HWND")
        self._hwnd = hwnd
        # 창이 최소화돼 있으면 GetWindowRect가 무효 좌표(-32000 근처)를 돌려주고,
        # 그대로 찍으면 쓰레기 이미지가 나오므로 먼저 창을 복원한다
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.3)
        # 가능하면 CAD 창을 앞으로 가져와 다른 창에 가려지지 않게 한다
        try:
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.3)
        except Exception as e:
            logger.warning(f"CAD 창을 앞으로 가져올 수 없음(그래도 캡처를 시도함): {str(e)}")

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        # all_screens=True로 여러 모니터/음수 좌표 상황을 지원한다
        img = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    # ==================== 편집 작업 ====================

    def _get_entity_by_handle(self, handle: str):
        """핸들로 객체를 찾는다(조회는 읽기 전용이라 호출 단위 재시도가 안전하다)"""
        entity = self._call_attr(self.doc, "HandleToObject", str(handle))
        if entity is None:
            raise ValueError(f"핸들 {handle}인 객체를 찾지 못함")
        return entity

    def erase_entity(self, handle: str) -> bool:
        """지정한 객체를 지운다"""
        if not self.is_running():
            return False
        try:
            # 독립 실행 취소 그룹: UNDO 1이 이번 지우기만 정확히 취소한다
            with self._undo_group():
                self._call_attr(self._get_entity_by_handle(handle), "Delete")
            return True
        except Exception as e:
            logger.error(f"객체 지우기 실패: {str(e)}")
            return False

    def move_entity(self, handle: str, displacement: List[float]) -> bool:
        """객체를 이동한다(이동 벡터 [dx, dy, dz])"""
        if not self.is_running():
            return False
        try:
            entity = self._get_entity_by_handle(handle)
            # Move(기준점, 두 번째 점)로 평행 이동한다: 기준점은 원점, 두 번째 점이 곧 이동 벡터
            with self._undo_group():
                self._call_attr(entity, "Move", self._pt((0, 0, 0)), self._pt(displacement))
            return True
        except Exception as e:
            logger.error(f"객체 이동 실패: {str(e)}")
            return False

    def rotate_entity(self, handle: str, base_point: List[float], angle_degrees: float) -> bool:
        """기준점을 중심으로 객체를 회전한다(각도 단위: 도, 반시계 방향이 양수)"""
        if not self.is_running():
            return False
        try:
            entity = self._get_entity_by_handle(handle)
            with self._undo_group():
                self._call_attr(entity, "Rotate", self._pt(base_point), math.radians(angle_degrees))
            return True
        except Exception as e:
            logger.error(f"객체 회전 실패: {str(e)}")
            return False

    def scale_entity(self, handle: str, base_point: List[float], scale_factor: float) -> bool:
        """기준점을 중심으로 객체 크기를 바꾼다"""
        if not self.is_running():
            return False
        try:
            entity = self._get_entity_by_handle(handle)
            with self._undo_group():
                self._call_attr(entity, "ScaleEntity", self._pt(base_point), scale_factor)
            return True
        except Exception as e:
            logger.error(f"객체 크기 조정 실패: {str(e)}")
            return False

    def copy_entity(self, handle: str, displacement: List[float]):
        """객체를 복사해 지정한 만큼 옮기고, 새 객체를 돌려준다"""
        if not self.is_running():
            return None
        try:
            entity = self._get_entity_by_handle(handle)
            # 복사+이동을 하나의 실행 취소 그룹으로 묶는다(Copy와 Move는 따로 재시도하며 둘 다 멱등이라 안전)
            with self._undo_group():
                copy = self._call_attr(entity, "Copy")
                if copy is not None and displacement:
                    self._call_attr(copy, "Move", self._pt((0, 0, 0)), self._pt(displacement))
            return copy
        except Exception as e:
            logger.error(f"객체 복사 실패: {str(e)}")
            return None

    def mirror_entity(self, handle: str, point1: List[float], point2: List[float]):
        """두 점으로 정한 대칭축을 기준으로 객체를 대칭 복사하고, 새로 생긴 객체를 돌려준다(원본 유지)"""
        if not self.is_running():
            return None
        try:
            entity = self._get_entity_by_handle(handle)
            with self._undo_group():
                mirrored = self._call_attr(entity, "Mirror", self._pt(point1), self._pt(point2))
            return mirrored
        except Exception as e:
            logger.error(f"객체 대칭 실패: {str(e)}")
            return None

    def offset_entity(self, handle: str, distance: float) -> List[Any]:
        """객체를 간격 띄우기 하고(양수/음수로 방향 결정), 새 객체 목록을 돌려준다"""
        if not self.is_running():
            return []
        try:
            entity = self._get_entity_by_handle(handle)
            with self._undo_group():
                # Offset은 새 객체 배열을 돌려준다(비어 있을 수 있음)
                result = self._call_attr(entity, "Offset", distance)
            return list(result) if result else []
        except Exception as e:
            logger.error(f"객체 간격 띄우기 실패: {str(e)}")
            return []

    def array_linear_entity(self, handle: str, count: int, displacement: List[float]) -> List[Any]:
        """직선 배열: 이동 벡터 방향으로 count-1개를 복사한다(원본 포함 총 count개)"""
        if not self.is_running():
            return []
        try:
            src = self._get_entity_by_handle(handle)
            dx, dy = float(displacement[0]), float(displacement[1])
            dz = float(displacement[2]) if len(displacement) > 2 else 0.0
            copies = []
            # 배열 전체를 하나의 실행 취소 그룹으로 묶는다. 반복 안에서는 호출마다 재시도해 일부만 끝난 뒤 중복되는 것을 막는다
            with self._undo_group():
                for i in range(1, count):
                    copy = self._call_attr(src, "Copy")
                    self._call_attr(copy, "Move", self._pt((0, 0, 0)), self._pt((dx * i, dy * i, dz * i)))
                    copies.append(copy)
            return copies
        except Exception as e:
            logger.error(f"직선 배열 실패: {str(e)}")
            return []

    def array_polar_entity(self, handle: str, count: int, center: List[float], total_angle_degrees: float = 360.0) -> List[Any]:
        """원형 배열: 중심점을 기준으로 total_angle 범위 안에 count-1개를 복사한다"""
        if not self.is_running():
            return []
        try:
            src = self._get_entity_by_handle(handle)
            step = math.radians(total_angle_degrees / count)
            center_pt = self._pt(center)
            copies = []
            # 배열 전체를 하나의 실행 취소 그룹으로 묶는다. 반복 안에서는 호출마다 재시도해 일부만 끝난 뒤 중복되는 것을 막는다
            with self._undo_group():
                for i in range(1, count):
                    copy = self._call_attr(src, "Copy")
                    self._call_attr(copy, "Rotate", center_pt, step * i)
                    copies.append(copy)
            return copies
        except Exception as e:
            logger.error(f"원형 배열 실패: {str(e)}")
            return []

    def undo(self) -> bool:
        """직전 작업을 실행 취소한다"""
        if not self.is_running():
            return False
        try:
            # 주의: AutoCAD에서 "_.UNDO 1"처럼 접두어와 숫자 인자를 함께 쓰면 동작하지 않음을 확인했다.
            # 순수 영문 명령 이름 "UNDO 1"이 안정적이다(현지화된 CAD도 영문 명령 이름을 그대로 지원)
            # 명령이 처리될 때까지 기다린 뒤 돌아가야 이후 호출이 취소 결과를 본다
            return self._send_and_wait("UNDO 1\n")["ok"]
        except Exception as e:
            logger.error(f"실행 취소 실패: {str(e)}")
            return False

    def redo(self) -> bool:
        """실행 취소한 작업을 다시 실행한다"""
        if not self.is_running():
            return False
        try:
            # 명령이 처리될 때까지 기다린 뒤 돌아간다
            return self._send_and_wait("_.REDO\n")["ok"]
        except Exception as e:
            logger.error(f"다시 실행 실패: {str(e)}")
            return False

    # ==================== 레이어 작업 ====================

    def set_current_layer(self, layer_name: str) -> bool:
        """현재 레이어를 바꾼다(레이어가 이미 있어야 한다)"""
        if not self.is_running():
            return False
        try:
            # Layers 컬렉션과 Name 읽기 모두 바쁜 CAD에 거부될 수 있어 호출마다 재시도
            layers = self._call(getattr, self.doc, "Layers")
            for i in range(self._call(getattr, layers, "Count")):
                layer = self._call_attr(layers, "Item", i)
                # CAD 레이어 이름은 대소문자를 구분하지 않으므로 비교할 때 대소문자를 무시한다
                if self._call(getattr, layer, "Name").casefold() == layer_name.casefold():
                    self._call(setattr, self.doc, "ActiveLayer", layer)
                    logger.info(f"현재 레이어 바꿈: {layer_name}")
                    return True
            logger.warning(f"레이어 {layer_name}이(가) 없음")
            return False
        except Exception as e:
            logger.error(f"레이어 바꾸기 실패: {str(e)}")
            return False

    # ==================== 도면 관리 ====================

    def new_drawing(self) -> bool:
        """새 도면 문서를 만든다"""
        if self.app is None:
            return False
        try:
            # Documents 컬렉션 얻기와 Add 호출 모두 바쁜 CAD에 거부될 수 있어 호출마다 재시도
            docs = self._call(getattr, self.app, "Documents")
            self.doc = self._call_attr(docs, "Add")
            # 막 만든 문서의 Name 읽기를 CAD가 잠깐 거부할 수 있어 재시도(안 하면 로그에 None이 찍힘)
            logger.info(f"새 문서 만듦: {self._call(getattr, self.doc, 'Name')}")
            return self.doc is not None
        except Exception as e:
            logger.error(f"새 문서 만들기 실패: {str(e)}")
            return False

    def open_drawing(self, file_path: str) -> bool:
        """기존 도면 파일을 연다"""
        if self.app is None:
            return False
        # 상대 경로는 먼저 절대 경로로 바꿔야 한다: Documents.Open은 CAD 프로세스 안에서 경로를 해석하는데,
        # 그 작업 폴더는 보통 MCP 서버의 작업 폴더와 다르다(save_drawing과 같은 이유)
        file_path = os.path.abspath(file_path)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"파일이 없습니다: {file_path}")
        try:
            # 연 직후 CAD가 잠깐 바쁘므로, 호출 단위 재시도로 Open 자체가 성공하게 한다
            docs = self._call(getattr, self.app, "Documents")
            opened = self._call_attr(docs, "Open", file_path)
            # 일부 CAD는 Documents.Open이 문서 객체를 돌려주지 않아 활성 문서로 대체한다
            self.doc = opened if opened is not None else self._call(getattr, self.app, "ActiveDocument")
            logger.info(f"문서 열음: {file_path}")
            return True
        except Exception as e:
            logger.error(f"문서 열기 실패: {str(e)}")
            return False

    def close_drawing(self, save: bool = False) -> bool:
        """현재 문서를 닫는다(save=False면 저장하지 않고 바로 닫는다)"""
        if not self.is_running():
            return False
        try:
            # 닫는 동안 CAD가 바빠 호출을 거부하므로, 호출 단위 재시도로 Close 자체가 성공하게 한다
            self._call_attr(self.doc, "Close", save)
            # 다른 열린 문서로 바꾸어 본다
            try:
                self.doc = self._call(getattr, self.app, "ActiveDocument")
            except Exception:
                self.doc = None
            logger.info(f"현재 문서 닫음(저장: {save})")
            return True
        except Exception as e:
            logger.error(f"문서 닫기 실패: {str(e)}")
            return False

    def list_drawings(self) -> List[Dict[str, Any]]:
        """CAD에 열린 도면을 모두 나열한다(인덱스, 이름, 전체 경로, 활성 여부, 저장 여부)"""
        if self.app is None:
            return []
        docs = self._call(getattr, self.app, "Documents")
        active_name = self._safe_attr(self._safe_attr(self.app, "ActiveDocument"), "Name")
        drawings = []
        for i in range(self._call(getattr, docs, "Count")):
            doc = self._call_attr(docs, "Item", i)
            name = str(self._call(getattr, doc, "Name"))
            saved = self._safe_attr(doc, "Saved")
            drawings.append({
                "index": i,
                "name": name,
                "full_name": str(self._safe_attr(doc, "FullName") or ""),
                "is_active": name == active_name,
                "saved": None if saved is None else bool(saved),
            })
        return drawings

    def switch_drawing(self, name_or_path: str) -> Optional[str]:
        """열린 도면 중 하나를 활성 도면으로 바꾸고 그 이름을 돌려준다(못 찾으면 None)

        전체 경로가 일치하는 도면을 먼저 찾고, 없으면 이름(대소문자 무시)이 일치하는 도면을 찾는다.
        """
        if self.app is None:
            return None
        docs = self._call(getattr, self.app, "Documents")
        candidates = [self._call_attr(docs, "Item", i) for i in range(self._call(getattr, docs, "Count"))]
        target = None
        wanted_path = os.path.normcase(os.path.abspath(name_or_path)) if os.path.isabs(name_or_path) else None
        if wanted_path:
            for doc in candidates:
                full = self._safe_attr(doc, "FullName")
                if full and os.path.normcase(str(full)) == wanted_path:
                    target = doc
                    break
        if target is None:
            wanted_name = os.path.basename(name_or_path).casefold()
            for doc in candidates:
                if str(self._safe_attr(doc, "Name", "")).casefold() == wanted_name:
                    target = doc
                    break
        if target is None:
            logger.warning(f"열린 도면 중에 찾지 못함: {name_or_path}")
            return None
        self._call_attr(target, "Activate")
        self.doc = target
        name = str(self._call(getattr, target, "Name"))
        logger.info(f"활성 도면 전환: {name}")
        return name

    # ==================== 블록 작업 ====================

    def create_block(self, name: str, base_point: List[float] = None):
        """블록 정의를 만든다(같은 이름의 블록이 있으면 그것을 돌려준다)

        참고: 블록 안의 객체는 send_command로 BEDIT 같은 명령을 실행해 편집할 수 있다.
        """
        if not self.is_running():
            return None
        try:
            # 같은 이름의 블록이 있는지 확인한다(Blocks 컬렉션과 Name 읽기 모두 바쁜 CAD에 거부될 수 있어 호출마다 재시도)
            blocks = self._call(getattr, self.doc, "Blocks")
            for i in range(self._call(getattr, blocks, "Count")):
                block = self._call_attr(blocks, "Item", i)
                if self._call(getattr, block, "Name") == name:
                    return block
            # 독립 실행 취소 그룹, 바쁘면 호출 단위로 자동 재시도
            with self._undo_group():
                return self._call_attr(blocks, "Add", self._pt(base_point or (0, 0, 0)), name)
        except Exception as e:
            logger.error(f"블록 만들기 실패: {str(e)}")
            return None

    def insert_block(self, name: str, position: List[float],
                     x_scale: float = 1.0, y_scale: float = 1.0, z_scale: float = 1.0,
                     rotation_degrees: float = 0.0):
        """모형 공간에 블록 참조를 넣는다"""
        if not self.is_running():
            return None
        try:
            # 독립 실행 취소 그룹, 바쁘면 호출 단위로 자동 재시도
            with self._undo_group():
                block_ref = self._call_attr(
                    self._ms(), "InsertBlock",
                    self._pt(position), name, x_scale, y_scale, z_scale,
                    math.radians(rotation_degrees)
                )
            return block_ref
        except Exception as e:
            logger.error(f"블록 넣기 실패: {str(e)}")
            return None

    def list_blocks(self) -> List[Dict[str, Any]]:
        """현재 도면의 사용자 블록 정의를 나열한다"""
        if not self.is_running():
            return []
        blocks = []
        try:
            # Blocks 컬렉션과 Name 읽기 모두 바쁜 CAD에 거부될 수 있어 호출마다 재시도
            block_set = self._call(getattr, self.doc, "Blocks")
            for i in range(self._call(getattr, block_set, "Count")):
                block = self._call_attr(block_set, "Item", i)
                name = str(self._call(getattr, block, "Name"))
                # 배치 자리 블록(*Model_Space, *Paper_Space 같은 익명 블록)은 건너뛴다
                if not name.startswith("*"):
                    blocks.append({
                        "name": name,
                        "entity_count": self._safe_attr(block, "Count", 0),
                    })
        except Exception as e:
            logger.error(f"블록 목록 실패: {str(e)}")
        return blocks

    # ==================== 명령 직접 실행 ====================

    def _read_cmdactive(self, retry: bool = True) -> int:
        """시스템 변수 CMDACTIVE를 읽는다. 읽지 못하면 -1

        retry=False면 CAD가 COM 호출을 거부할 때 기다리지 않고 바로 -1을 돌려준다
        (대화상자나 문자 편집기가 열려 COM이 막혔는지 빨리 알아볼 때 쓴다).
        """
        if self.doc is None:
            return -1
        try:
            if retry:
                return int(self._call_attr(self.doc, "GetVariable", "CMDACTIVE"))
            return int(self.doc.GetVariable("CMDACTIVE"))
        except Exception:
            return -1

    def _wait_cmd_idle(self, timeout: Optional[float] = None, retry: bool = True) -> int:
        """명령이 끝날 때까지 기다리고 마지막 CMDACTIVE 값을 돌려준다

        CMDACTIVE 비트: 1 명령, 2 투명 명령, 4 스크립트, 8 대화상자, 32 LISP.
        - SendCommand는 비동기라 돌아온 직후에는 명령이 아직 시작되지 않았을 수 있다.
          그래서 0이 연속 두 번 읽혀야 끝난 것으로 본다.
        - 명령이 사용자 입력(점, 옵션)을 기다리는 동안 CAD는 COM에 응답하면서 같은 값을 유지한다.
          같은 0 아닌 값이 0.5초 동안 이어지면 "입력 대기 중"으로 보고 바로 돌려준다.
          그러지 않으면 명령을 나눠 보낼 때마다 제한 시간만큼 멈춘다.
        - 제한 시간이 지나면 그때 값을 돌려준다. -1은 읽지 못했다는 뜻이다.
        """
        if timeout is None:
            timeout = self.command_timeout
        deadline = time.monotonic() + timeout
        idle_reads = 0
        same_reads = 0
        last = None
        time.sleep(0.05)
        while True:
            value = self._read_cmdactive(retry)
            idle_reads = idle_reads + 1 if value == 0 else 0
            same_reads = same_reads + 1 if (value > 0 and value == last) else 0
            last = value
            if idle_reads >= 2 or same_reads >= 5 or time.monotonic() >= deadline:
                return value
            time.sleep(0.1)

    def command_busy_reason(self) -> Optional[str]:
        """도면을 닫거나 바꾸면 안 되는 상태(명령 진행 중)면 그 이유를, 아니면 None을 돌려준다

        BricsCAD V26에서 명령이 붙잡혀 있는 상태로 도면을 닫은 직후 BricsCAD가 종료된 적이 있다.
        """
        if self._send_pending():
            return "앞서 보낸 명령이 아직 입력을 기다리는 중입니다"
        cmd = self._read_cmdactive(retry=False)
        if cmd > 0:
            return f"CAD에서 명령이 진행 중입니다(CMDACTIVE={cmd})"
        return None

    def _send_pending(self) -> bool:
        """앞서 보낸 SendCommand가 아직 돌아오지 않았는지(명령이 입력을 기다리며 붙잡혀 있는지)"""
        return self._pending_send is not None and self._pending_send.is_alive()

    def _send_on_helper(self, command: str) -> Tuple[threading.Thread, Dict[str, Any]]:
        """SendCommand를 보조 스레드에서 보낸다(COM 스레드에서 호출)

        CAD마다 SendCommand가 돌아오는 시점이 다르다.
        - GstarCAD: 명령을 대기열에 넣고 바로 돌아온다.
        - BricsCAD: 명령이 끝날 때까지 돌아오지 않는다. 입력을 기다리는 명령이면 무한정 붙잡힌다.
        전용 COM 스레드에서 직접 부르면 BricsCAD에서 MCP 전체가 멈추고 cancel_command도 그 뒤에 줄을 선다.
        그래서 지금 대상 도면의 인터페이스를 보조 스레드로 넘겨(마샬링) 거기서 보낸다.
        """
        state: Dict[str, Any] = {"error": None}
        target = self.doc
        stream = None
        try:
            stream = pythoncom.CoMarshalInterThreadInterfaceInStream(pythoncom.IID_IDispatch, self.doc._oleobj_)
        except AttributeError:
            # COM 객체가 아닌 경우(시험용 가짜 객체)에는 그대로 넘긴다
            stream = None

        def worker():
            pythoncom.CoInitialize()
            try:
                doc = target
                if stream is not None:
                    doc = win32com.client.Dispatch(
                        pythoncom.CoGetInterfaceAndReleaseStream(stream, pythoncom.IID_IDispatch))
                # 바쁜 CAD가 거부하면 호출 단위로 재시도한다(거부된 호출은 실행되지 않았으므로 안전)
                _call_with_com_retry(lambda: doc.SendCommand(command), (), {})
            except Exception as e:
                state["error"] = str(e)
                logger.error(f"명령 보내기 실패: {str(e)}")
            finally:
                pythoncom.CoUninitialize()

        thread = threading.Thread(target=worker, name="cad-send", daemon=True)
        thread.start()
        self._pending_send = thread
        return thread, state

    def _send_and_wait(self, command: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        """명령을 보내고, 끝나거나 입력을 기다리는 상태가 될 때까지 기다린다(COM 스레드에서 호출)

        돌려주는 값: {"ok", "cmdactive", "pending", "error"}
        pending=True면 SendCommand가 아직 돌아오지 않았다(BricsCAD에서 명령이 입력을 기다리는 중).
        이 상태에서는 이어서 입력을 보내도 명령이 받지 않으므로 새 명령을 보내지 않는다.
        """
        if self._send_pending():
            return {"ok": False, "cmdactive": self._read_cmdactive(retry=False), "pending": True,
                    "error": "앞서 보낸 명령이 아직 입력을 기다리는 중이라 새 명령을 보내지 않았습니다. cancel_command로 먼저 취소하세요"}
        if timeout is None:
            timeout = self.command_timeout
        deadline = time.monotonic() + timeout
        thread, state = self._send_on_helper(command)
        thread.join(0.3)
        if thread.is_alive():
            # SendCommand가 붙잡혀 있다(BricsCAD). 명령이 끝나면 돌아오고, 입력을 기다리면
            # CAD가 COM에 응답하면서 같은 CMDACTIVE 값을 유지한다. 같은 값이 0.5초 이어지면 입력 대기로 본다
            same_reads, last = 0, None
            while thread.is_alive() and time.monotonic() < deadline:
                value = self._read_cmdactive()
                same_reads = same_reads + 1 if (value > 0 and value == last) else 0
                last = value
                if same_reads >= 5:
                    break
                thread.join(0.1)
            if thread.is_alive():
                return {"ok": True, "cmdactive": self._read_cmdactive(retry=False), "pending": True, "error": None}
        if state["error"]:
            return {"ok": False, "cmdactive": self._read_cmdactive(retry=False), "pending": False, "error": state["error"]}
        # SendCommand가 바로 돌아오는 CAD(GstarCAD)는 명령이 끝나거나 입력을 기다릴 때까지 기다린다
        # (ZOOM이나 저장 같은 명령이 진행 중이면 이어지는 SaveAs 등이 오류를 낸다)
        cmd = self._wait_cmd_idle(max(0.5, deadline - time.monotonic()))
        return {"ok": True, "cmdactive": cmd, "pending": False, "error": None}

    def send_command(self, command: str) -> Dict[str, Any]:
        """CAD 명령행에 임의의 명령을 그대로 보낸다(예: "_.ZOOM E")

        도구로 없는 기능을 쓰는 비상구다: 도구로 만들어지지 않은 어떤 CAD 명령도 이것으로 실행할 수 있다.
        돌려주는 값: {"ok": 보냈는지, "cmdactive": 보낸 뒤 CMDACTIVE 값(0이면 끝남, -1이면 읽지 못함),
                      "pending": SendCommand가 아직 돌아오지 않았는지, "error": 실패 이유}
        """
        if not self.is_running():
            return {"ok": False, "cmdactive": -1, "pending": False, "error": "CAD 미연결"}
        # 명령이 Enter/공백으로 끝나지 않으면 Enter를 하나 붙여 명령이 제출되게 한다
        if not command.endswith(("\n", "\r", " ")):
            command += "\n"
        return self._send_and_wait(command)

    def _send_escape_keys(self, count: int = 2) -> bool:
        """CAD 창을 앞으로 가져와 키보드로 ESC를 보낸다(COM이 막혔을 때 쓰는 수단)

        포그라운드 창이 CAD 프로세스의 창(메인 창, CAD의 대화상자나 문자 편집기)일 때만 키를 보낸다.
        다른 프로그램에 ESC가 들어가는 것을 막기 위해서다.
        """
        import win32api
        import win32con
        import win32gui
        import win32process

        hwnd = self._hwnd or self._safe_attr(self.app, "HWND")
        if not hwnd:
            logger.warning("CAD 창 핸들이 없어 ESC를 보낼 수 없음")
            return False
        try:
            cad_pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        except Exception as e:
            logger.warning(f"CAD 창 프로세스를 확인할 수 없음: {str(e)}")
            return False

        def cad_is_foreground() -> bool:
            fg = win32gui.GetForegroundWindow()
            return bool(fg) and win32process.GetWindowThreadProcessId(fg)[1] == cad_pid

        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.3)
        for _ in range(3):
            if cad_is_foreground():
                break
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                # Windows의 포그라운드 잠금 때문에 실패하면 Alt 키를 한 번 눌렀다 떼고 다시 시도한다(널리 쓰는 우회법)
                try:
                    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
                    win32gui.SetForegroundWindow(hwnd)
                except Exception as e:
                    logger.warning(f"CAD 창을 앞으로 가져오지 못함: {str(e)}")
            time.sleep(0.3)
        if not cad_is_foreground():
            logger.warning("CAD 창이 앞에 있지 않아 ESC를 보내지 않음")
            return False
        for _ in range(count):
            win32api.keybd_event(win32con.VK_ESCAPE, 0, 0, 0)
            win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.1)
        return True

    def cancel_command(self) -> Dict[str, Any]:
        """진행 중인 명령을 취소한다(ESC와 같음)

        1) CAD가 COM에 응답하면 보조 스레드에서 SendCommand로 ESC 문자(chr(27))를 두 번 보낸다.
           GstarCAD 2021에서 확인: ESC 문자는 바로 취소되고, Ctrl+C 문자(chr(3))는 취소되지 않았다.
        2) 그래도 끝나지 않거나, COM이 막혀 있거나(대화상자·문자 편집기),
           앞서 보낸 SendCommand가 붙잡혀 있으면(BricsCAD) CAD 창에 키보드 ESC를 보낸다.
           BricsCAD에서 확인: 붙잡힌 명령에는 다른 SendCommand가 전달되지 않고, 키보드 ESC는 통한다.
        돌려주는 값: {"ok": 명령이 없는 상태가 됐는지, "method": 쓴 방법, "cmdactive": 마지막 CMDACTIVE}
        이 메서드는 COM이 막혀 있어도 빨리 끝나도록 재시도 없이 읽는다.
        """
        if self.app is None or self.doc is None:
            return {"ok": False, "method": None, "cmdactive": -1}
        before = self._read_cmdactive(retry=False)
        if before == 0 and not self._send_pending():
            return {"ok": True, "method": "none", "cmdactive": 0}
        methods = []
        cmd = before
        if before != -1 and not self._send_pending():
            methods.append("esc_char")
            result = self._send_and_wait(chr(27) + chr(27), timeout=2)
            cmd = result["cmdactive"]
        # 키보드 ESC는 최대 3번까지 보낸다. 창을 앞으로 가져올 때 쓴 Alt 키가 BricsCAD의 리본 단축키 모드를 켜서
        # 첫 ESC가 그 모드를 끄는 데 쓰이는 경우가 있었다(BricsCAD V26에서 첫 시도만 실패한 것을 확인)
        for _ in range(3):
            if cmd == 0 and not self._send_pending():
                break
            if not self._send_escape_keys():
                cmd = self._read_cmdactive(retry=False)
                continue
            if "esc_key" not in methods:
                methods.append("esc_key")
            # 붙잡혀 있던 SendCommand가 풀려 돌아오기를 잠깐 기다린다
            if self._pending_send is not None:
                self._pending_send.join(1.5)
            cmd = self._wait_cmd_idle(timeout=1.5, retry=False)
        ok = cmd == 0 and not self._send_pending()
        return {"ok": ok, "method": "+".join(methods) or None, "cmdactive": cmd}

    @staticmethod
    def _lisp_result_path() -> str:
        """LISP 결과를 받을 임시 파일 경로를 만든다

        LISP의 open이 경로의 한글을 제대로 다루지 못할 수 있으므로, 임시 폴더 경로에
        ASCII가 아닌 문자가 있으면(한글 사용자 이름 등) 공용 폴더(%PUBLIC%)를 쓴다.
        """
        base = tempfile.gettempdir()
        if not base.isascii():
            base = os.environ.get("PUBLIC") or "C:\\Users\\Public"
        return os.path.join(base, f"arcaid_lisp_{uuid.uuid4().hex}.txt")

    def run_lisp(self, expression: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        """AutoLISP 식 하나를 실행하고 그 결과를 문자열(prin1 표기)로 돌려준다

        식을 progn으로 감싸 결과를 임시 파일에 prin1로 쓰게 한 뒤, 실행이 끝나면 파일을 읽고 지운다.
        여러 식을 실행하려면 (progn ...)으로 묶는다. 식 안의 줄바꿈은 공백으로 바꿔 보낸다.
        진행 중인 명령이 있으면 LISP가 그 명령의 입력값으로 들어가므로 실행하지 않는다.
        돌려주는 값: {"ok", "value", "cmdactive", "error"}
        """
        if not self.is_running():
            return {"ok": False, "value": None, "cmdactive": -1, "error": "CAD 미연결"}
        cmd = self._read_cmdactive()
        if cmd != 0 or self._send_pending():
            return {"ok": False, "value": None, "cmdactive": cmd,
                    "error": f"진행 중인 명령이 있어 실행하지 않음(CMDACTIVE={cmd}). cancel_command로 먼저 취소하세요"}

        path = self._lisp_result_path()
        lisp_path = path.replace("\\", "/")
        body = " ".join(expression.splitlines())
        wrapped = (f'(progn (setq mcp-r {body}) (setq mcp-f (open "{lisp_path}" "w")) '
                   f'(prin1 mcp-r mcp-f) (close mcp-f) (setq mcp-r nil mcp-f nil) (princ))\n')
        sent = self._send_and_wait(wrapped, timeout)
        cmd = sent["cmdactive"]
        if not sent["ok"]:
            return {"ok": False, "value": None, "cmdactive": cmd, "error": f"LISP 보내기 실패: {sent['error']}"}
        if sent["pending"]:
            return {"ok": False, "value": None, "cmdactive": cmd,
                    "error": "LISP가 입력을 기다리는 중입니다(getpoint 같은 대화형 함수 등). cancel_command로 취소하세요"}

        # 파일이 닫히고 보이기까지 잠깐 걸릴 수 있다
        for _ in range(5):
            if os.path.exists(path):
                break
            time.sleep(0.1)
        if not os.path.exists(path):
            return {"ok": False, "value": None, "cmdactive": cmd,
                    "error": "결과 파일이 만들어지지 않았습니다. LISP 식에 오류가 있거나 아직 실행 중일 수 있습니다"}

        with open(path, "rb") as f:
            raw = f.read()
        try:
            os.remove(path)
        except OSError:
            pass
        # CAD마다 LISP 파일 인코딩이 다를 수 있다(UTF-8 또는 시스템 ANSI 코드 페이지)
        value = None
        for encoding in ("utf-8-sig", "mbcs"):
            try:
                value = raw.decode(encoding)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if value is None:
            value = raw.decode("utf-8", errors="replace")
        return {"ok": True, "value": value.rstrip("\r\n"), "cmdactive": cmd, "error": None}
