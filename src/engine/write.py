"""DXF 작성(구조 결정 D1·D5): 객체 명세 → 검증 → 펼침 → 표준 정의만 담은 R2013 DXF + ARCAID 태그 → 자체 검사

- 정의는 template.dxf에서 쓰는 것만 옮긴다(ezdxf Importer: 레이어·선종류·문자 스타일·치수 스타일·블록과
  중첩 블록·화살표). 템플릿 전체를 담으면 CAD에 넣는 데 6초가 걸렸다(T0 E4).
  Importer는 XDATA를 지우므로 객체는 옮기지 않고 새 문서에 직접 만들며, 문자 스타일 글꼴(XDATA)과
  기본 레이어(0·Defpoints)는 옮긴 뒤 템플릿·표준 값으로 맞춘다.
- 태그: XDATA ARCAID에 op·oid·path·ver(엔진 버전)·in(명세 해시)·state(기준 상태)를 1000번 문자열로 단다.
  state는 여기서 미리 계산한다. CAD에 넣은 뒤 다시 계산해 다른 객체만 고친다(객체마다 쓰면 느림, T6 사전 확인 P5).
- 쓰는 블록 정의는 늘 담는다. 정의를 뺀 DXF를 넣으면 그 블록 참조가 빠진다(T6 사전 확인 P3).
- 저장한 DXF를 check_standard로 검사해 위반이 있으면 실패로 돌려준다(엔진 오류를 숨기지 않는다).
- 도면에 넣지는 않는다. 넣기는 draw_batch(T6)가 한다.
"""
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Union

import ezdxf
from ezdxf.addons import Importer
from ezdxf.enums import TextEntityAlignment
from ezdxf.lldxf import const
from pydantic import BaseModel, ConfigDict, Field

from . import ENGINE_VERSION
from .check import check_standard
from .layout import Prim, expand
from .results import (Applied, Assumption, Checked, EngineResult, OutOfScope, ResultIds, Scope, Status,
                      Violation)
from .spec import Spec, SpecError, Target, parse_spec, spec_hash, validate_spec
from .state import APP, state_hash
from .standards import Standard

DXF_VERSION = "R2013"
ALIGN = {"L": TextEntityAlignment.LEFT, "C": TextEntityAlignment.CENTER, "R": TextEntityAlignment.RIGHT,
         "ML": TextEntityAlignment.MIDDLE_LEFT, "MC": TextEntityAlignment.MIDDLE_CENTER,
         "MR": TextEntityAlignment.MIDDLE_RIGHT, "TL": TextEntityAlignment.TOP_LEFT,
         "TC": TextEntityAlignment.TOP_CENTER, "TR": TextEntityAlignment.TOP_RIGHT,
         "BL": TextEntityAlignment.BOTTOM_LEFT, "BC": TextEntityAlignment.BOTTOM_CENTER,
         "BR": TextEntityAlignment.BOTTOM_RIGHT}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManifestItem(_Model):
    oid: str
    path: str
    kind: str
    layer: str


class BuildMeta(_Model):
    op: str
    spec_hash: str
    standard: str
    k: int
    k_assumed: bool
    out_path: str
    dxfversion: str
    object_count: int
    by_kind: Dict[str, int]
    by_layer: Dict[str, int]
    target: Optional[Target] = None


class BuildResult(EngineResult):
    """build_dxf 결과: D2 공통 항목 + 작성 정보와 객체 목록(manifest)"""
    meta: Optional[BuildMeta] = None
    manifest: List[ManifestItem] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


def _new_doc(std: Standard, prims: List[Prim], k: int):
    """쓰는 정의만 템플릿에서 옮긴 새 R2013 문서"""
    template = ezdxf.readfile(str(std.path(std.profile.template.dxf)))
    doc = ezdxf.new(DXF_VERSION, setup=False)
    imp = Importer(template, doc)
    imp.import_table("layers", sorted({p.layer for p in prims}))
    linetypes = sorted({p.linetype for p in prims if p.linetype})
    if linetypes:
        imp.import_table("linetypes", linetypes)
    imp.import_table("styles", [std.text.style.name])
    dimstyles = sorted({p.name for p in prims if p.kind == "dim"})
    if dimstyles:
        imp.import_table("dimstyles", dimstyles)
    blocks = sorted({p.name for p in prims if p.kind == "block"})
    if blocks:
        imp.import_blocks(blocks, rename=False)
    imp.finalize()
    for style in doc.styles:   # Importer가 지운 글꼴 XDATA(TTF 글꼴 이름)를 되살린다
        name = style.dxf.name
        if template.styles.has_entry(name):
            family, italic, bold = template.styles.get(name).get_extended_font_data()
            if family:
                style.set_extended_font_data(family, italic=italic, bold=bold)
    for name in ("0", "Defpoints"):   # 새 문서가 만든 기본 레이어를 표준 값으로
        want, layer = std.layer(name), doc.layers.get(name)
        layer.dxf.color, layer.dxf.linetype, layer.dxf.plot = want.color, want.linetype, int(want.plot)
    if not doc.appids.has_entry(APP):
        doc.appids.new(APP)
    doc.header["$INSUNITS"] = std.profile.insunits
    doc.header["$MEASUREMENT"] = 1
    doc.header["$LTSCALE"] = float(k * std.profile.scale.ltscale_per_k)
    return doc


def _add(doc, p: Prim, std: Standard, k: int):
    msp = doc.modelspace()
    attribs = {"layer": p.layer}
    if p.linetype:
        attribs["linetype"] = p.linetype
    if p.kind == "line":
        return msp.add_line(p.points[0], p.points[1], dxfattribs=attribs)
    if p.kind == "polyline":
        pts = [(x, y, 0, 0, b) for (x, y), b in zip(p.points, p.bulges)]
        return msp.add_lwpolyline(pts, format="xyseb", close=p.closed, dxfattribs=attribs)
    if p.kind == "circle":
        return msp.add_circle(p.center, p.radius, dxfattribs=attribs)
    if p.kind == "arc":
        return msp.add_arc(p.center, p.radius, p.start_angle, p.end_angle, dxfattribs=attribs)
    if p.kind == "text":
        t = msp.add_text(p.text, height=p.height, rotation=p.rotation,
                         dxfattribs={**attribs, "style": std.text.style.name})
        t.set_placement(p.at, align=ALIGN[p.align])
        return t
    if p.kind == "block":
        ref = msp.add_blockref(p.name, p.at, dxfattribs={**attribs, "xscale": p.xscale, "yscale": p.yscale,
                                                          "rotation": p.rotation})
        if any(True for _ in doc.blocks.get(p.name).query("ATTDEF")):
            ref.add_auto_attribs(p.attribs)
        return ref
    if p.kind == "hatch":
        h = msp.add_hatch(color=const.BYLAYER, dxfattribs=attribs)
        if p.pattern == "SOLID":
            h.set_solid_fill(color=const.BYLAYER, style=0)
        else:
            h.set_pattern_fill(p.pattern, color=const.BYLAYER, angle=p.pattern_angle, scale=p.pattern_scale, style=0)
        # 섬은 OUTERMOST 플래그 + 바깥과 반대 방향일 때 GstarCAD가 구멍을 만든다(T0 E4)
        for n, loop in enumerate(p.loops):
            h.paths.add_polyline_path(loop, is_closed=True,
                                      flags=const.BOUNDARY_PATH_EXTERNAL if n == 0 else const.BOUNDARY_PATH_OUTERMOST)
        return h
    if p.kind == "dim":
        dimscale = k * std.profile.scale.dimscale_per_k
        d = msp.add_linear_dim(base=p.points[2], p1=p.points[0], p2=p.points[1], angle=p.angle, dimstyle=p.name,
                               override={"dimscale": dimscale}, dxfattribs=attribs)
        d.render()
        return d.dimension
    raise ValueError(f"지원하지 않는 객체 종류: {p.kind}")


def _failed(scope, ids, summary: str, violations: List[Violation]) -> BuildResult:
    return BuildResult(status=Status.FAILED, summary=summary, scope=scope, violations=violations, ids=ids)


def build_dxf(spec: Union[str, dict, Spec], std: Standard, out: Path) -> BuildResult:
    """객체 명세를 검증·전개해 R2013 DXF로 쓴다. 명세가 틀리면 파일을 쓰지 않는다"""
    out = Path(out)
    scope = Scope(target=str(out), supported=["line", "polyline", "circle", "arc", "text", "block", "hatch", "dim",
                                              "dim_chain", "group"])
    ids = ResultIds(standard=std.ref)
    try:
        parsed = parse_spec(spec)
    except SpecError as e:
        return _failed(scope, ids, f"명세 형식 오류 {len(e.violations)}건(쓰지 않음)", e.violations)
    ids.request_id = parsed.op
    if parsed.target:
        ids.drawing_id, ids.snapshot_id = parsed.target.drawing_id, parsed.target.snapshot_id
    problems = validate_spec(parsed, std)
    if problems:
        return _failed(scope, ids, f"명세가 표준과 맞지 않음 {len(problems)}건(쓰지 않음)", problems)
    k_assumed = parsed.scale is None
    k = std.profile.scale.default_k if k_assumed else parsed.scale
    try:
        prims = expand(parsed, std, k)
    except SpecError as e:
        return _failed(scope, ids, f"명세를 펼치지 못함 {len(e.violations)}건(쓰지 않음)", e.violations)

    h = spec_hash(parsed)
    doc = _new_doc(std, prims, k)
    for p in prims:
        entity = _add(doc, p, std, k)
        entity.set_xdata(APP, [(1000, f"op={parsed.op}"), (1000, f"oid={p.oid}"), (1000, f"path={p.path}"),
                               (1000, f"ver={ENGINE_VERSION}"), (1000, f"in={h}"),
                               (1000, f"state={state_hash(entity)}")])
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(out))

    assumptions = [Assumption(name="scale", value=k, reason=f"명세에 축척이 없어 A3 1/{k}로 가정")] if k_assumed else []
    by_kind = dict(sorted(Counter(p.kind for p in prims).items()))
    by_layer = dict(sorted(Counter(p.layer for p in prims).items()))
    meta = BuildMeta(op=parsed.op, spec_hash=h, standard=std.ref, k=k, k_assumed=k_assumed, out_path=str(out),
                     dxfversion=doc.dxfversion, object_count=len(prims), by_kind=by_kind, by_layer=by_layer,
                     target=parsed.target)
    manifest = [ManifestItem(oid=p.oid, path=p.path, kind=p.kind, layer=p.layer) for p in prims]
    warnings: List[str] = []
    check = check_standard(out, std, k=k)
    if check.status is not Status.SUCCESS or check.violations:
        return BuildResult(status=Status.FAILED, summary=f"생성 DXF 자체 검사 실패(엔진 오류): {check.summary}",
                           scope=scope, violations=check.violations, unchecked=check.unchecked, ids=ids, meta=meta,
                           manifest=manifest, assumptions=assumptions, warnings=warnings)
    kinds = ", ".join(f"{kind} {n}" for kind, n in by_kind.items())
    return BuildResult(status=Status.SUCCESS, summary=f"{parsed.op}: 객체 {len(prims)}개 작성({kinds}), 자체 검사 위반 0건",
                       scope=scope, checked=[Checked(item="명세 객체", count=len(prims))] + check.checked,
                       applied=[Applied(action="DXF 작성", count=len(prims))], unchecked=check.unchecked,
                       out_of_scope=[OutOfScope(item="도면에 넣기", reason="build_dxf는 DXF 파일만 쓴다. 넣기는 draw_batch(T6)")],
                       assumptions=assumptions, ids=ids, meta=meta, manifest=manifest, warnings=warnings)
