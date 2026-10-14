"""도면의 레이어·문자 스타일·치수 스타일·블록 정의를 표준 프로파일과 비교한다(구조 결정 D3)

- 템플릿 정합성(check_template): template.dxf와 YAML이 같은지, 두 파일 해시가 profile.yaml과 같은지
- 같은 이름, 다른 정의(compare_definitions): 대상 도면 스냅샷에 표준과 다른 정의가 있으면 적용 전에 멈추는 데 쓴다(T4·T6)
이름은 대소문자를 무시하고 비교한다. 표준에 없는 정의는 위반이 아니다(extra_definitions로 따로 본다).
"""
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

import ezdxf
from ezdxf import bbox

from .results import Checked, EngineResult, OutOfScope, ResultIds, Scope, Status, Violation
from .standards import Standard

EXTENT_TOL = 1.0     # 블록 범위 허용오차(mm)
NUM_TOL = 1e-6


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= NUM_TOL
    return str(a).casefold() == str(b).casefold()


def _v(code: str, message: str, current: Any = None, expected: Any = None, fixable: bool = False) -> Violation:
    return Violation(code=code, message=message, current=current, expected=expected, fixable=fixable)


def _layer_violations(std: Standard, doc) -> List[Violation]:
    out = []
    for want in std.layers:
        layer = doc.layers.get(want.name) if doc.layers.has_entry(want.name) else None
        if layer is None:
            out.append(_v("LAYER_MISSING", f"표준 레이어 {want.name}가 없음", None, want.name))
            continue
        color = abs(layer.dxf.color)  # 꺼진 레이어는 색이 음수로 저장된다
        if color != want.color:
            out.append(_v("LAYER_COLOR", f"레이어 {want.name} 색이 표준과 다름", color, want.color, True))
        if not _same(layer.dxf.linetype, want.linetype):
            out.append(_v("LAYER_LINETYPE", f"레이어 {want.name} 선종류가 표준과 다름", layer.dxf.linetype,
                          want.linetype, True))
        plot = bool(layer.dxf.get("plot", 1))
        if plot != want.plot:
            out.append(_v("LAYER_PLOT", f"레이어 {want.name} 출력 여부가 표준과 다름", plot, want.plot, True))
    return out


def _textstyle_violations(std: Standard, doc) -> List[Violation]:
    want = std.text.style
    if not doc.styles.has_entry(want.name):
        return [_v("TEXTSTYLE_MISSING", f"표준 문자 스타일 {want.name}가 없음", None, want.name)]
    style = doc.styles.get(want.name)
    family = style.get_extended_font_data()[0] or style.dxf.get("font", "")
    out = []
    if not _same(family, want.font_family):
        out.append(_v("TEXTSTYLE_FONT", f"문자 스타일 {want.name} 글꼴이 표준과 다름", family, want.font_family))
    if not _same(style.dxf.get("width", 1.0), want.width):
        out.append(_v("TEXTSTYLE_FONT", f"문자 스타일 {want.name} 폭이 표준과 다름", style.dxf.get("width"), want.width))
    return out


def is_annotative(entity) -> bool:
    if not entity.has_xdata("AcadAnnotative"):
        return False
    flags = [v for code, v in entity.get_xdata("AcadAnnotative") if code == 1070]
    return len(flags) >= 2 and flags[1] == 1


def _dimstyle_violations(std: Standard, doc) -> List[Violation]:
    out = []
    for want in std.dimensions.styles:
        if not doc.dimstyles.has_entry(want.name):
            out.append(_v("DIMSTYLE_MISSING", f"표준 치수 스타일 {want.name}가 없음", None, want.name))
            continue
        ds = doc.dimstyles.get(want.name)
        if is_annotative(ds):
            out.append(_v("DIMSTYLE_ANNOTATIVE", f"치수 스타일 {want.name}가 주석 스타일임(표준은 비주석)", True, False))
        diffs = {key: (ds.dxf.get(key), value) for key, value in want.vars.items()
                 if not _same(ds.dxf.get(key), value)}
        if diffs:
            out.append(_v("DIMSTYLE_VAR", f"치수 스타일 {want.name} 값이 표준과 다름: {', '.join(diffs)}",
                          {k: c for k, (c, _) in diffs.items()}, {k: e for k, (_, e) in diffs.items()}))
        text_style = ds.dxf.get("dimtxsty")  # ezdxf가 그룹 340 핸들을 이름으로 풀어 둔다
        if not _same(text_style, want.text_style):
            out.append(_v("DIMSTYLE_TEXTSTYLE", f"치수 스타일 {want.name} 문자 스타일이 표준과 다름",
                          text_style, want.text_style))
    return out


def block_geometry_extents(block) -> Optional[List[List[float]]]:
    """블록 정의의 형상 범위(속성 정의 문자 제외)를 기준점 기준으로 돌려준다"""
    shapes = [e for e in block if e.dxftype() != "ATTDEF"]
    ext = bbox.extents(shapes, fast=True)
    if not ext.has_data:
        return None
    base = block.block.dxf.base_point
    return [[ext.extmin.x - base.x, ext.extmin.y - base.y], [ext.extmax.x - base.x, ext.extmax.y - base.y]]


def _block_violations(std: Standard, doc) -> List[Violation]:
    out = []
    names = {b.name.casefold(): b.name for b in doc.blocks}
    for want in std.blocks:
        if want.status != "standard":
            continue
        name = names.get(want.name.casefold())
        if name is None:
            out.append(_v("BLOCK_MISSING", f"표준 블록 {want.name}가 없음", None, want.name))
            continue
        block = doc.blocks.get(name)
        have = sorted((a.dxf.tag.casefold(), round(a.dxf.height, 3), a.dxf.layer.casefold())
                      for a in block.query("ATTDEF"))
        expect = sorted((a.tag.casefold(), round(a.height, 3), a.layer.casefold()) for a in want.attributes)
        if have != expect:
            out.append(_v("BLOCK_ATTRIB", f"블록 {want.name} 속성 정의가 표준과 다름",
                          [list(x) for x in have], [list(x) for x in expect]))
        ext = block_geometry_extents(block)
        target = [list(want.extents[0]), list(want.extents[1])]
        if ext is None or any(abs(a - b) > EXTENT_TOL for pa, pb in zip(ext, target) for a, b in zip(pa, pb)):
            out.append(_v("BLOCK_EXTENTS", f"블록 {want.name} 형상 범위가 표준과 다름",
                          None if ext is None else [[round(v, 1) for v in p] for p in ext], target))
    return out


def compare_definitions(std: Standard, doc) -> List[Violation]:
    """도면(ezdxf 문서)의 표준 이름 정의가 표준 프로파일과 같은지 비교해 위반 목록을 돌려준다"""
    return (_layer_violations(std, doc) + _textstyle_violations(std, doc)
            + _dimstyle_violations(std, doc) + _block_violations(std, doc))


def extra_definitions(std: Standard, doc) -> Dict[str, List[str]]:
    """표준에 없는 정의(위반 아님, 보고용)"""
    layers = {x.name.casefold() for x in std.layers}
    dims = {d.name.casefold() for d in std.dimensions.styles} | {"standard"}
    blocks = {b.name.casefold() for b in std.blocks}
    return {
        "layers": sorted(x.dxf.name for x in doc.layers if x.dxf.name.casefold() not in layers),
        "text_styles": sorted(s.dxf.name for s in doc.styles
                              if s.dxf.name.casefold() not in (std.text.style.name.casefold(), "standard")),
        "dimension_styles": sorted(d.dxf.name for d in doc.dimstyles if d.dxf.name.casefold() not in dims),
        "blocks": sorted(b.name for b in doc.blocks if not b.name.startswith("*") and b.name.casefold() not in blocks),
    }


def _sha256(path: Path) -> Optional[str]:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def check_template(std: Standard) -> EngineResult:
    """표준 프로파일의 template.dxf가 YAML과 같은지, 두 파일 해시가 profile.yaml과 같은지 확인한다"""
    tpl = std.profile.template
    dwg, dxf = std.path(tpl.dwg), std.path(tpl.dxf)
    scope = Scope(target=str(dxf), supported=["layers", "text_style", "dimension_styles", "blocks", "hashes"])
    ids = ResultIds(standard=std.ref)
    out_of_scope = [OutOfScope(item="해치 패턴", reason="0단계 표준 파일 범위 밖(D3)"),
                    OutOfScope(item="출력 스타일(CTB)", reason="0단계 표준 파일 범위 밖(D3)"),
                    OutOfScope(item="쓰지 않는 블록의 정의", reason="blocks.yaml status: unused")]
    violations: List[Violation] = []
    for code, path, recorded in (("TEMPLATE_DWG_HASH", dwg, tpl.dwg_sha256), ("TEMPLATE_DXF_HASH", dxf, tpl.dxf_sha256)):
        actual = _sha256(path)
        if actual != recorded:
            violations.append(_v(code, f"{path.name} 해시가 profile.yaml과 다름(템플릿을 고쳤으면 "
                                       f"tools/template/export_template_dxf.py를 다시 실행)", actual or "파일 없음", recorded))
    if not dxf.exists():
        return EngineResult(status=Status.FAILED, summary=f"template.dxf가 없음: {dxf}", scope=scope,
                            violations=violations, out_of_scope=out_of_scope, ids=ids)
    doc = ezdxf.readfile(str(dxf))
    violations += compare_definitions(std, doc)
    extra = extra_definitions(std, doc)
    checked = [Checked(item="layers", count=len(std.layers)),
               Checked(item="text_style", count=1),
               Checked(item="dimension_styles", count=len(std.dimensions.styles)),
               Checked(item="standard_blocks", count=sum(1 for b in std.blocks if b.status == "standard")),
               Checked(item="hashes", count=2)]
    extra_text = ", ".join(f"{k} {len(v)}" for k, v in extra.items() if v) or "없음"
    if violations:
        summary = f"템플릿이 표준과 다름: 위반 {len(violations)}건(표준 외 정의: {extra_text})"
        status = Status.CONFLICT
    else:
        summary = f"템플릿과 표준 일치(표준 외 정의: {extra_text})"
        status = Status.SUCCESS
    return EngineResult(status=status, summary=summary, scope=scope, checked=checked, violations=violations,
                        out_of_scope=out_of_scope, ids=ids)
