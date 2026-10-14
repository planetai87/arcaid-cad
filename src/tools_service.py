"""T7 툴 조율(MCP 쪽, COM 스레드에서 실행): 템플릿으로 새 도면, 고른 객체 읽기, PDF 출력

결과는 D2 형식(EngineResult 확장)이다. 도면을 바꾸는 표준 레이어 만들기는 apply_service.ensure_layers(보호된 경로).
- new_from_template: 템플릿 DWG를 새 경로로 복사해 연다(이미 있으면 덮어쓰지 않음)
- selection: Pickfirst 선택을 스냅샷으로 읽어 종류·레이어별 개수·범위·엔진 객체 oid를 돌려준다
- plot: 도곽(또는 창) 범위를 회사 CTB·DWG To PDF·A3·1/k로 출력한다. 사용자 도면 설정을 건드리지 않도록
  Export한 DXF 사본에서 출력한다(T7 사전 확인 P3·P3b)
"""
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import ezdxf
from ezdxf import bbox
from pydantic import BaseModel, ConfigDict, Field

try:
    from .engine.results import Applied, Assumption, Checked, EngineResult, ResultIds, Scope, Status
    from .engine.sheet import SheetInfo, find_sheets
    from .engine.snapshot import SnapshotError
    from .engine.state import arcaid_tags
    from .snapshot_service import snapshot_active
except ImportError:
    from engine.results import Applied, Assumption, Checked, EngineResult, ResultIds, Scope, Status
    from engine.sheet import SheetInfo, find_sheets
    from engine.snapshot import SnapshotError
    from engine.state import arcaid_tags
    from snapshot_service import snapshot_active

LIST_LIMIT = 200
MEDIA_MM = {"A3": (420.0, 297.0)}
MEDIA_TOL_MM = 1.0


class SelectedEngineObject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    handle: str
    oid: str
    path: Optional[str] = None


class SelectionResult(EngineResult):
    """get_selection 결과: 사용자가 CAD에서 고른 객체"""
    count: int = 0
    handles: List[str] = Field(default_factory=list, description="고른 객체 핸들(limit까지)")
    by_type: Dict[str, int] = Field(default_factory=dict)
    by_layer: Dict[str, int] = Field(default_factory=dict)
    extents: Optional[List[List[float]]] = Field(default=None, description="고른 객체 범위 [[xmin, ymin], [xmax, ymax]]")
    engine_objects: List[SelectedEngineObject] = Field(default_factory=list, description="엔진이 넣은 객체(태그)")
    truncated: bool = False
    warnings: List[str] = Field(default_factory=list)


class PlotResult(EngineResult):
    """plot 결과"""
    pdf_path: Optional[str] = None
    device: Optional[str] = None
    ctb: Optional[str] = None
    media: Optional[str] = None
    media_mm: Optional[List[float]] = Field(default=None, description="PDF 용지 크기(mm, 가로·세로)")
    k: Optional[int] = Field(default=None, description="A3 축척 분모(종이 1mm = 도면 k mm)")
    window: Optional[List[List[float]]] = None
    sheet_handle: Optional[str] = None
    trial: bool = Field(default=True, description="시험 출력인지(정식 출력기·용지 확인을 통과하지 못함)")
    warnings: List[str] = Field(default_factory=list)


# ---------- new_from_template ----------

def template_refusal(std, file_path: str) -> Optional[EngineResult]:
    """새 도면 경로가 틀리면 거부 결과(CAD 없이 판단)"""
    scope = Scope(target=str(file_path), supported=["회사 템플릿으로 새 도면"])
    ids = ResultIds(standard=std.ref)
    path = Path(file_path)
    if path.suffix.lower() != ".dwg":
        return EngineResult(status=Status.FAILED, summary="새 도면은 .dwg 경로로만 만든다", scope=scope, ids=ids)
    if not path.is_absolute():
        return EngineResult(status=Status.FAILED, summary="새 도면 경로는 절대 경로로 준다", scope=scope, ids=ids)
    if path.exists():
        return EngineResult(status=Status.FAILED, summary="이미 있는 파일이라 덮어쓰지 않음(다른 이름을 쓸 것)",
                            scope=scope, ids=ids)
    if not std.path(std.profile.template.dwg).exists():
        return EngineResult(status=Status.FAILED, summary="회사 템플릿 DWG가 없음", scope=scope, ids=ids)
    return None


def new_from_template(ctl, std, file_path: str) -> EngineResult:
    refused = template_refusal(std, file_path)
    if refused is not None:
        return refused
    path = Path(file_path)
    scope = Scope(target=str(path), supported=["회사 템플릿으로 새 도면"])
    ids = ResultIds(standard=std.ref)
    if ctl.doc is not None:
        busy = ctl.command_busy_reason()
        if busy:
            return EngineResult(status=Status.FAILED, summary=f"{busy}. 명령을 마친 뒤 다시 부를 것", scope=scope, ids=ids)
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(std.path(std.profile.template.dwg), path)
    if not ctl.open_drawing(str(path)):
        return EngineResult(status=Status.FAILED, summary="템플릿 사본을 만들었지만 열지 못함", scope=scope, ids=ids)
    return EngineResult(status=Status.SUCCESS, summary=f"회사 템플릿({std.ref})으로 새 도면을 만들어 엶", scope=scope, ids=ids,
                        applied=[Applied(action="새 도면", count=1)])


# ---------- get_selection ----------

def selection(ctl, work: Path, limit: int = LIST_LIMIT) -> SelectionResult:
    scope = Scope(target="활성 도면", supported=["Pickfirst 선택"])
    handles = ctl.pickfirst_handles()
    if not handles:
        return SelectionResult(status=Status.SUCCESS, summary="고른 객체 없음(CAD에서 객체를 고른 뒤 다시 부를 것)", scope=scope)
    try:
        snap, warnings = snapshot_active(ctl, work)
    except SnapshotError as e:
        return SelectionResult(status=Status.FAILED, summary=f"스냅샷 실패: {e}", scope=scope, count=len(handles),
                               handles=handles[:limit])
    doc = ezdxf.readfile(snap.dxf_path)
    entities = [doc.entitydb.get(h) for h in handles]
    entities = [e for e in entities if e is not None]
    ext = bbox.extents(entities, fast=True)
    engine = []
    for e in entities:
        tags = arcaid_tags(e)
        if tags and "oid" in tags:
            engine.append(SelectedEngineObject(handle=e.dxf.handle, oid=tags["oid"], path=tags.get("path")))
    by_type = dict(sorted(Counter(e.dxftype() for e in entities).items()))
    by_layer = dict(sorted(Counter(e.dxf.get("layer", "0") for e in entities).items()))
    return SelectionResult(
        status=Status.SUCCESS, summary=f"고른 객체 {len(handles)}개(엔진 객체 {len(engine)}개)", scope=scope,
        ids=ResultIds(snapshot_id=snap.snapshot_id, drawing_id=snap.drawing_id), count=len(handles),
        handles=handles[:limit], by_type=by_type, by_layer=by_layer,
        extents=[[round(ext.extmin.x, 3), round(ext.extmin.y, 3)], [round(ext.extmax.x, 3), round(ext.extmax.y, 3)]]
        if ext.has_data else None,
        engine_objects=engine[:limit], truncated=len(handles) > limit or len(engine) > limit,
        checked=[Checked(item="고른 객체", count=len(entities))])


# ---------- plot ----------

def plot_refusal(sheet_handle: Optional[str], window, out_path: Optional[str]) -> Optional[PlotResult]:
    scope = Scope(target="활성 도면", supported=["PDF 출력"])
    if sheet_handle and window:
        return PlotResult(status=Status.FAILED, summary="sheet_handle과 window 중 하나만 준다", scope=scope)
    if window is not None and (len(window) != 2 or any(len(p) < 2 for p in window)
                               or window[0][0] >= window[1][0] or window[0][1] >= window[1][1]):
        return PlotResult(status=Status.FAILED, summary="window는 [[왼쪽 아래 x, y], [오른쪽 위 x, y]]", scope=scope)
    if out_path is not None:
        out = Path(out_path)
        if out.suffix.lower() != ".pdf" or not out.is_absolute():
            return PlotResult(status=Status.FAILED, summary="out_path는 .pdf 절대 경로", scope=scope)
        if out.exists():
            return PlotResult(status=Status.FAILED, summary="이미 있는 PDF라 덮어쓰지 않음(다른 이름을 쓸 것)", scope=scope)
    return None


def pdf_media_mm(path: Path) -> Optional[List[float]]:
    """PDF 첫 MediaBox의 크기(mm)"""
    m = re.search(rb"/MediaBox\s*\[\s*([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s*\]", Path(path).read_bytes())
    if not m:
        return None
    x0, y0, x1, y1 = (float(v) for v in m.groups())
    return [round((x1 - x0) * 25.4 / 72, 1), round((y1 - y0) * 25.4 / 72, 1)]


def plot(ctl, work: Path, std, sheet_handle: Optional[str] = None, window=None, scale: Optional[int] = None,
         out_path: Optional[str] = None, device: str = "DWG To PDF.pc3", ctb: str = "HEO(2020)_A3.ctb",
         media: str = "A3", official_device: str = "DWG To PDF.pc3") -> PlotResult:
    """도곽(또는 창) 범위를 PDF로 출력한다. 범위를 주지 않으면 도면에 도곽이 하나일 때 그것을 쓴다"""
    scope = Scope(target="활성 도면", supported=["PDF 출력"])
    refused = plot_refusal(sheet_handle, window, out_path)
    if refused is not None:
        return refused
    state = ctl.doc_state(ctl.doc)
    try:
        snap, warnings = snapshot_active(ctl, work)
    except SnapshotError as e:
        return PlotResult(status=Status.FAILED, summary=f"스냅샷 실패: {e}", scope=scope)
    ids = ResultIds(standard=std.ref, snapshot_id=snap.snapshot_id, drawing_id=snap.drawing_id)
    doc = ezdxf.readfile(snap.dxf_path)
    sheets, problems = find_sheets(doc, std)
    warnings = list(warnings) + problems
    assumptions = []
    sheet: Optional[SheetInfo] = None
    if window is None:
        if sheet_handle:
            sheet = next((s for s in sheets if s.handle.upper() == sheet_handle.upper()), None)
            if sheet is None:
                return PlotResult(status=Status.FAILED, summary=f"출력할 수 있는 도곽 {sheet_handle}이 없음", scope=scope,
                                  ids=ids, warnings=warnings)
        elif len(sheets) == 1:
            sheet = sheets[0]
        else:
            listed = ", ".join(f"{s.handle}(1/{s.k})" for s in sheets) or "없음"
            return PlotResult(status=Status.FAILED, summary=f"출력 범위를 정할 수 없음: 도곽 {len(sheets)}개({listed}). "
                                                            "sheet_handle 또는 window를 줄 것", scope=scope, ids=ids,
                              warnings=warnings)
        window = sheet.window
        if scale is not None and scale != sheet.k:
            warnings.append(f"도곽 배율은 1/{sheet.k}인데 scale 1/{scale}로 출력함")
        k = scale or sheet.k
    else:
        k = scale or std.profile.scale.default_k
        if scale is None:
            assumptions.append(Assumption(name="scale", value=k, reason=f"창 범위 출력에 축척이 없어 A3 1/{k}로 가정"))
    stem = Path(state["name"] or "drawing").stem
    out = Path(out_path) if out_path else Path(work) / "plots" / f"{stem}-{datetime.now():%Y%m%d-%H%M%S}.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    r = ctl.plot_copy_to_pdf(snap.dxf_path, window, k, str(out), device, ctb, media)
    if not r["ok"]:
        return PlotResult(status=Status.FAILED, summary=f"출력 실패: {r['error']}", scope=scope, ids=ids, device=device,
                          ctb=ctb, k=k, window=window, warnings=warnings, assumptions=assumptions)
    size = pdf_media_mm(out)
    want = MEDIA_MM.get(media.upper())
    media_ok = bool(size and want and all(abs(a - b) <= MEDIA_TOL_MM for a, b in zip(sorted(size), sorted(want))))
    trial = device != official_device or not media_ok
    if not media_ok:
        warnings.append(f"PDF 용지 크기 {size}mm가 {media} {want}mm와 다름")
    kind = "시험 출력" if trial else "정식 출력"
    return PlotResult(status=Status.SUCCESS, summary=f"{kind}: {media} 1/{k}, {device}, {ctb}", scope=scope, ids=ids,
                      applied=[Applied(action="PDF 출력", count=1)], pdf_path=str(out), device=device, ctb=ctb,
                      media=r["media"], media_mm=size, k=k, window=window,
                      sheet_handle=sheet.handle if sheet else None, trial=trial, warnings=warnings,
                      assumptions=assumptions)
