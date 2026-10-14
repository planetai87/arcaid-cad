"""엔진 객체의 태그와 기준 상태(구조 결정 D5)

- 태그: XDATA ARCAID의 "키=값" 문자열(op, oid, path, ver, in, state)
- 기준 상태(state): 객체 종류별 비교 필드를 0.01mm로 반올림한 정준 JSON의 sha256 앞 16자.
  사람이 손으로 고쳤는지(현재 상태 ≠ state) 판단하는 데 쓴다.
  공통: 종류·레이어·색(트루컬러 포함)·선종류·선굵기 / 선·폴리선·원·호: 형상 /
  문자: 위치(정렬이면 정렬점)·높이·회전·정렬·스타일·내용 / 블록 참조: 이름·삽입점·배율·회전·속성값 /
  치수: 정의점·스타일·재정의 값·문자 / 해치: 패턴·배율·루프 수
- 이름(레이어·스타일·블록)은 CAD에서 대소문자를 가리지 않으므로 소문자로 비교한다.
"""
import hashlib
import json
from typing import Dict, Optional

APP = "ARCAID"
TAG_KEYS = ("op", "oid", "path", "ver", "in", "state")


def _f(x, nd: int = 2) -> float:
    return round(float(x), nd) + 0.0   # -0.0 → 0.0


def _p(v) -> list:
    return [_f(v[0]), _f(v[1])]


def arcaid_tags(e) -> Optional[Dict[str, str]]:
    """ARCAID 태그를 dict로. 태그가 없으면 None"""
    if not e.has_xdata(APP):
        return None
    out = {}
    for code, value in e.get_xdata(APP):
        if code == 1000 and "=" in str(value):
            key, _, val = str(value).partition("=")
            out[key] = val
    return out


def entity_state(e, verify: bool = False) -> dict:
    """비교 필드. verify=True면 CAD가 넣으면서 다시 계산할 수 있는 값(치수선 위치)을 뺀다"""
    t = e.dxftype()
    s = {"type": t, "layer": e.dxf.get("layer", "0").casefold(), "color": e.dxf.get("color", 256),
         "true_color": e.dxf.get("true_color"), "linetype": e.dxf.get("linetype", "BYLAYER").casefold(),
         "lineweight": e.dxf.get("lineweight", -1)}
    if t == "LINE":
        s.update(start=_p(e.dxf.start), end=_p(e.dxf.end))
    elif t == "LWPOLYLINE":
        s.update(points=[[_f(x), _f(y), _f(b, 4)] for x, y, b in e.get_points("xyb")], closed=bool(e.closed))
    elif t in ("CIRCLE", "ARC"):
        s.update(center=_p(e.dxf.center), radius=_f(e.dxf.radius))
        if t == "ARC":
            s.update(start_angle=_f(e.dxf.start_angle % 360), end_angle=_f(e.dxf.end_angle % 360))
    elif t == "TEXT":
        halign, valign = e.dxf.get("halign", 0), e.dxf.get("valign", 0)
        aligned = (halign or valign) and e.dxf.hasattr("align_point")
        s.update(text=e.dxf.text, height=_f(e.dxf.height), rotation=_f(e.dxf.get("rotation", 0) % 360),
                 style=e.dxf.get("style", "Standard").casefold(), halign=halign, valign=valign,
                 position=_p(e.dxf.align_point if aligned else e.dxf.insert))
    elif t == "MTEXT":
        s.update(text=e.text, height=_f(e.dxf.char_height), insert=_p(e.dxf.insert),
                 rotation=_f(e.dxf.get("rotation", 0) % 360), style=e.dxf.get("style", "Standard").casefold())
    elif t == "INSERT":
        s.update(name=e.dxf.name.casefold(), insert=_p(e.dxf.insert), xscale=_f(e.dxf.get("xscale", 1), 4),
                 yscale=_f(e.dxf.get("yscale", 1), 4), rotation=_f(e.dxf.get("rotation", 0) % 360),
                 attribs=sorted([a.dxf.tag.casefold(), a.dxf.text] for a in e.attribs))
    elif t == "DIMENSION":
        try:
            overrides = {k: (_f(v, 4) if isinstance(v, (int, float)) else str(v))
                         for k, v in sorted(e.override().dimstyle_attribs.items())}
        except Exception:  # noqa: BLE001  (재정의를 읽지 못하면 비교에서 뺀다)
            overrides = None
        s.update(dimstyle=e.dxf.get("dimstyle", "Standard").casefold(), p1=_p(e.dxf.defpoint2),
                 p2=_p(e.dxf.defpoint3), angle=_f(e.dxf.get("angle", 0) % 180), overrides=overrides,
                 text=e.dxf.get("text", ""))
        if not verify:
            s["line"] = _p(e.dxf.defpoint)
    elif t == "HATCH":
        s.update(pattern=e.dxf.pattern_name.upper(), solid=e.dxf.solid_fill, loops=len(e.paths),
                 scale=_f(e.dxf.get("pattern_scale", 1), 4))
    return s


def state_hash(e) -> str:
    text = json.dumps(entity_state(e), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
