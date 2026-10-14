"""도면 읽기(구조 결정 D4): DXF 사본을 추출하고, 표준 레이어로 그린 그리드와 실명을 판독한다

- 추출(모든 도면): 모형 공간 객체의 종류·레이어·색·선종류·핸들, 블록 참조, 문자, 치수 → full_out JSON 파일
- 판독(표준 레이어 도면): CEN 레이어 축선 + 축열 버블 속성 = 그리드, ROM 레이어 문자 = 실명
- 요약(MCP·AI에 돌려주는 결과)에는 개수와 판독 결과만 담고, 객체 전체는 파일로만 남긴다
"""
import json
import math
from collections import Counter
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

import ezdxf
from pydantic import BaseModel, ConfigDict, Field

from .results import Checked, EngineResult, OutOfScope, ResultIds, Scope, Status, Unchecked
from .snapshot import SnapshotMeta
from .standards import Standard

# $INSUNITS → (이름, 1 단위의 mm)
UNITS: Dict[int, Tuple[str, Optional[float]]] = {
    0: ("미정", None), 1: ("inch", 25.4), 2: ("feet", 304.8), 4: ("mm", 1.0), 5: ("cm", 10.0), 6: ("m", 1000.0)}
SNAPSHOT_VERSIONS = ("AC1027", "AC1032")
GRID_LAYER = "CEN"
ROOM_LAYER = "ROM"
BUBBLES = {"축열버블-상": "X", "축열버블-좌": "Y"}   # 블록 이름 → 이름을 붙일 축 방향
BUBBLE_TAG = "축열"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Axis(_Model):
    direction: Literal["X", "Y"]
    name: Optional[str] = None
    position: float
    start: float
    end: float
    handles: List[str] = Field(default_factory=list)


class Room(_Model):
    text: str
    position: List[float]
    height: float
    handle: str


class LayerCount(_Model):
    name: str
    count: int
    standard: bool


class BlockCount(_Model):
    name: str
    count: int


class Xref(_Model):
    name: str
    path: str


class ReadMeta(_Model):
    source: str
    dxf_path: str
    snapshot_id: Optional[str] = None
    drawing_id: Optional[str] = None
    dxfversion: str
    insunits: int
    units: str
    mm_per_unit: Optional[float] = None
    extents: Optional[List[List[float]]] = None
    modelspace_count: int
    paperspace_count: int


class ReadDrawingResult(EngineResult):
    """read_drawing 결과: D2 공통 항목 + D4 출력"""
    meta: Optional[ReadMeta] = None
    layers: List[LayerCount] = Field(default_factory=list)
    nonstandard_layers: List[str] = Field(default_factory=list)
    grid: List[Axis] = Field(default_factory=list)
    rooms: List[Room] = Field(default_factory=list)
    blocks: List[BlockCount] = Field(default_factory=list)
    xrefs: List[Xref] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    full_path: Optional[str] = None


def _xy(v) -> List[float]:
    return [round(float(v[0]), 6), round(float(v[1]), 6)]


def _segment(e) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """선 또는 두 점 폴리선이면 두 끝점"""
    t = e.dxftype()
    if t == "LINE":
        return (e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)
    if t == "LWPOLYLINE" and len(e) == 2 and not e.closed:
        (x1, y1), (x2, y2) = [(p[0], p[1]) for p in e.get_points("xy")]
        return (x1, y1), (x2, y2)
    if t == "POLYLINE" and len(e.vertices) == 2 and not e.is_closed:
        a, b = e.vertices
        return (a.dxf.location.x, a.dxf.location.y), (b.dxf.location.x, b.dxf.location.y)
    return None


def _read_grid(lines, bubbles, tol: float):
    """축선 조각을 방향·좌표로 묶고 버블 속성으로 이름을 붙인다. (축 목록, 미검사 항목)"""
    groups = {"X": [], "Y": []}   # [position, start, end, handles]
    skewed = 0
    for handle, (p, q) in lines:
        dx, dy = abs(q[0] - p[0]), abs(q[1] - p[1])
        if dx <= tol and dy > tol:
            direction, pos, lo, hi = "X", (p[0] + q[0]) / 2, min(p[1], q[1]), max(p[1], q[1])
        elif dy <= tol and dx > tol:
            direction, pos, lo, hi = "Y", (p[1] + q[1]) / 2, min(p[0], q[0]), max(p[0], q[0])
        else:
            if math.hypot(dx, dy) > tol:
                skewed += 1
            continue
        for g in groups[direction]:
            if abs(g[0] - pos) <= tol:
                g[1], g[2] = min(g[1], lo), max(g[2], hi)
                g[3].append(handle)
                break
        else:
            groups[direction].append([pos, lo, hi, [handle]])
    axes = [Axis(direction=d, position=round(g[0], 6), start=round(g[1], 6), end=round(g[2], 6), handles=g[3])
            for d in ("X", "Y") for g in sorted(groups[d], key=lambda g: g[0])]
    stray = 0
    for direction, pos, name in bubbles:
        axis = next((a for a in axes if a.direction == direction and abs(a.position - pos) <= tol), None)
        if axis is None:
            stray += 1
        elif axis.name is None:
            axis.name = name
    unchecked = []
    unnamed = sum(1 for a in axes if a.name is None)
    if unnamed:
        unchecked.append(Unchecked(item="이름 없는 축", reason="축열 버블이 없는 축선", count=unnamed))
    if stray:
        unchecked.append(Unchecked(item="축에 맞지 않는 버블", reason="같은 좌표의 CEN 축선이 없음", count=stray))
    if skewed:
        unchecked.append(Unchecked(item="사선 축선", reason="0단계는 수직·수평 축선만 판독", count=skewed))
    return axes, unchecked


def _room(e) -> Optional[Room]:
    if e.dxftype() == "TEXT":
        aligned = e.dxf.get("halign", 0) or e.dxf.get("valign", 0)
        pos = e.dxf.align_point if aligned and e.dxf.hasattr("align_point") else e.dxf.insert
        return Room(text=e.dxf.text, position=_xy(pos), height=round(e.dxf.height, 6), handle=e.dxf.handle)
    if e.dxftype() == "MTEXT":
        return Room(text=e.plain_text(), position=_xy(e.dxf.insert), height=round(e.dxf.char_height, 6),
                    handle=e.dxf.handle)
    return None


def _record(e) -> dict:
    """추출 파일용 객체 정보"""
    t = e.dxftype()
    rec = {"handle": e.dxf.handle, "type": t, "layer": e.dxf.layer, "color": e.dxf.get("color", 256),
           "linetype": e.dxf.get("linetype", "BYLAYER")}
    try:
        if t == "LINE":
            rec.update(start=_xy(e.dxf.start), end=_xy(e.dxf.end))
        elif t == "LWPOLYLINE":
            rec.update(points=[_xy(p) for p in e.get_points("xy")], closed=e.closed)
        elif t in ("CIRCLE", "ARC"):
            rec.update(center=_xy(e.dxf.center), radius=e.dxf.radius)
            if t == "ARC":
                rec.update(start_angle=e.dxf.start_angle, end_angle=e.dxf.end_angle)
        elif t == "TEXT":
            rec.update(text=e.dxf.text, insert=_xy(e.dxf.insert), height=e.dxf.height,
                       style=e.dxf.get("style"), halign=e.dxf.get("halign", 0), valign=e.dxf.get("valign", 0))
            if e.dxf.hasattr("align_point"):
                rec["align_point"] = _xy(e.dxf.align_point)
        elif t == "MTEXT":
            rec.update(text=e.plain_text(), insert=_xy(e.dxf.insert), height=e.dxf.char_height)
        elif t == "INSERT":
            rec.update(block=e.dxf.name, insert=_xy(e.dxf.insert), xscale=e.dxf.get("xscale", 1),
                       yscale=e.dxf.get("yscale", 1), rotation=e.dxf.get("rotation", 0),
                       attribs={a.dxf.tag: a.dxf.text for a in e.attribs})
        elif t == "DIMENSION":
            rec["dimstyle"] = e.dxf.get("dimstyle")
            try:
                rec["measurement"] = round(e.get_measurement(), 6)
            except Exception:  # noqa: BLE001
                rec["measurement"] = None
        elif t == "HATCH":
            rec.update(pattern=e.dxf.pattern_name, loops=len(e.paths))
    except Exception as ex:  # noqa: BLE001  (추출 실패는 그 객체만 표시)
        rec["error"] = str(ex)
    return rec


def read_drawing(dxf_path: Path, std: Standard, snapshot: Optional[SnapshotMeta] = None,
                 full_out: Optional[Path] = None) -> ReadDrawingResult:
    """DXF 사본을 읽어 D4 형식으로 요약한다. full_out을 주면 객체 전체를 JSON으로 쓴다"""
    dxf_path = Path(dxf_path)
    scope = Scope(target=str(dxf_path), supported=["추출", "그리드(CEN·축열 버블)", "실명(ROM)", "블록", "Xref", "단위"])
    ids = ResultIds(standard=std.ref, snapshot_id=snapshot.snapshot_id if snapshot else None)
    try:
        doc = ezdxf.readfile(str(dxf_path))
    except Exception as e:  # noqa: BLE001  (깨진 파일·없는 파일·형식 오류)
        return ReadDrawingResult(status=Status.FAILED, summary=f"DXF를 읽지 못함: {e}", scope=scope, ids=ids)

    insunits = int(doc.header.get("$INSUNITS", 0))
    units, mm_per_unit = UNITS.get(insunits, (f"코드 {insunits}", None))
    tol = 1.0 / mm_per_unit if mm_per_unit else 1.0   # 1mm
    msp = list(doc.modelspace())
    paperspace_count = sum(len(layout) for layout in doc.layouts if not layout.is_modelspace)
    extmin, extmax = doc.header.get("$EXTMIN"), doc.header.get("$EXTMAX")
    extents = None
    if extmin and extmax and abs(extmin[0]) < 1e19 and extmax[0] >= extmin[0]:
        extents = [_xy(extmin), _xy(extmax)]
    drawing_id = (snapshot.drawing_id if snapshot else None) or doc.header.get("$FINGERPRINTGUID")
    ids.drawing_id = drawing_id
    meta = ReadMeta(source=snapshot.source_path if snapshot else str(dxf_path), dxf_path=str(dxf_path),
                    snapshot_id=ids.snapshot_id, drawing_id=drawing_id, dxfversion=doc.dxfversion,
                    insunits=insunits, units=units, mm_per_unit=mm_per_unit, extents=extents,
                    modelspace_count=len(msp), paperspace_count=paperspace_count)

    # 레이어·블록 집계
    by_layer = Counter(e.dxf.layer for e in msp)
    layers = [LayerCount(name=name, count=n, standard=std.layer(name) is not None)
              for name, n in sorted(by_layer.items(), key=lambda kv: kv[0].casefold())]
    nonstandard = [x.name for x in layers if not x.standard]
    inserts = [e for e in msp if e.dxftype() == "INSERT"]
    blocks = [BlockCount(name=n, count=c) for n, c in sorted(Counter(e.dxf.name for e in inserts).items())]
    xrefs = [Xref(name=b.name, path=b.block.dxf.get("xref_path", "")) for b in doc.blocks
             if b.block is not None and b.block.is_xref]

    # 판독: 그리드와 실명
    grid_lines = [(e.dxf.handle, seg) for e in msp if e.dxf.layer.casefold() == GRID_LAYER.casefold()
                  and (seg := _segment(e)) is not None]
    bubbles = []
    for e in inserts:
        direction = BUBBLES.get(e.dxf.name)
        if direction:
            name = next((a.dxf.text for a in e.attribs if a.dxf.tag == BUBBLE_TAG), None)
            pos = e.dxf.insert.x if direction == "X" else e.dxf.insert.y
            bubbles.append((direction, pos, name))
    grid, unchecked = _read_grid(grid_lines, bubbles, tol)
    rooms = [r for e in msp if e.dxf.layer.casefold() == ROOM_LAYER.casefold() and (r := _room(e)) is not None]

    # 경고
    warnings = []
    if mm_per_unit is None:
        warnings.append(f"도면 단위가 정해지지 않음($INSUNITS {insunits}). 좌표를 mm로 가정하지 말고 확인할 것")
    elif insunits != 4:
        warnings.append(f"도면 단위가 mm가 아님: {units}(1 단위 = {mm_per_unit:g} mm)")
    odd = Counter(e.dxf.name for e in inserts
                  if any(abs(s) > 0 and abs(math.log10(abs(s))) >= 2
                         for s in (e.dxf.get("xscale", 1), e.dxf.get("yscale", 1))))
    for name, n in sorted(odd.items()):
        warnings.append(f"블록 배율로 단위 섞임 의심: {name} {n}개(배율이 100배 이상 다름)")
    if doc.dxfversion not in SNAPSHOT_VERSIONS:
        warnings.append(f"스냅샷 버전이 R2013·R2018이 아님: {doc.dxfversion}")

    out_of_scope = [
        OutOfScope(item="레이아웃", reason=f"종이 공간 객체 {paperspace_count}개는 읽지 않음"),
        OutOfScope(item="Xref 내부 객체", reason=f"Xref {len(xrefs)}개는 목록과 경로만"),
        OutOfScope(item="블록 정의 내부", reason="표준 블록 정의 비교는 check_template·같은 이름 정의 검사(D3)"),
        OutOfScope(item="벽체 인식", reason="0단계 범위 밖(D4)"),
        OutOfScope(item="창호·문 종류", reason="0단계 범위 밖(D4)"),
        OutOfScope(item="비표준 레이어의 의미 해석",
                   reason=f"비표준 레이어 객체 {sum(by_layer[n] for n in nonstandard)}개는 추출만 함"),
    ]
    checked = [Checked(item="모형 공간 객체", count=len(msp)), Checked(item="축선", count=len(grid)),
               Checked(item="실명", count=len(rooms))]

    full_path = None
    if full_out is not None:
        full_out = Path(full_out)
        try:
            full_out.parent.mkdir(parents=True, exist_ok=True)
            full_out.write_text(json.dumps({"meta": meta.model_dump(), "entities": [_record(e) for e in msp]},
                                           ensure_ascii=False), encoding="utf-8")
            full_path = str(full_out)
        except OSError as e:
            # 요약은 돌려주고, 객체 전체 목록 파일만 못 썼음을 알린다
            warnings.append(f"객체 전체 목록 파일을 쓰지 못함({full_out}): {e}")

    nx = sum(1 for a in grid if a.direction == "X")
    summary = (f"객체 {len(msp)}개, 축 X {nx}·Y {len(grid) - nx}, 실명 {len(rooms)}개, "
               f"비표준 레이어 {len(nonstandard)}개, 경고 {len(warnings)}개")
    return ReadDrawingResult(status=Status.SUCCESS, summary=summary, scope=scope, checked=checked,
                             unchecked=unchecked, out_of_scope=out_of_scope, ids=ids, meta=meta, layers=layers,
                             nonstandard_layers=nonstandard, grid=grid, rooms=rooms, blocks=blocks, xrefs=xrefs,
                             warnings=warnings, full_path=full_path)
