"""B5 당산동 준비·독립 판정·공개 MCP stdio 시험.

prepare는 CAD 없이 실행한다. mcp는 사람 확인 해시와 새 C:/arcaid/work 폴더가
필요하다. 판정에는 changes.py의 상태 계산·비교·지문·기록 판독을 사용하지 않는다.
SEQEND는 GstarCAD Export 때 재발급되는 것으로 실측되어 비교에서 제외한다.
"""
import argparse
import asyncio
from collections import Counter
import copy
from datetime import timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import uuid

import ezdxf
from ezdxf import bbox
from ezdxf.entities import DXFGraphic
from ezdxf.lldxf.tagwriter import TagCollector

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.state import arcaid_tags, state_hash
from tools.acceptance.t10_dangsan import file_identity, write_json

SNAPSHOT_PLACEHOLDER = "B5_RUNTIME_SNAPSHOT"
OPS = ("b5-room-move", "b5-annot-edit", "b5-human-edit")
KINDS = {"LINE", "LWPOLYLINE", "CIRCLE", "ARC", "TEXT", "INSERT"}
TOL = .01


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _digest(value, size=16):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()[:size]


def _raw(value):
    if isinstance(value, bytes):
        return value.hex()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return [_raw(v) for v in value]


def graphics(doc):
    """블록 정의·종속 속성·지원 밖 종류까지 포함한다."""
    return {e.dxf.handle: e for e in doc.entitydb.values()
            if e.is_alive and isinstance(e, DXFGraphic) and e.dxftype() != "SEQEND"}


def _tags(e, *, omit=(), own=False, semantic=False, ignore_state=False):
    result, app = [], None
    omit = set(omit) | {code + offset for code in omit if 10 <= code <= 18 for offset in (10, 20)}
    for tag in TagCollector.dxftags(e):
        if tag.code == 0 and result:
            if own or tag.value == "SEQEND":
                break
        if tag.code == 1001:
            app = tag.value
        if ignore_state and app == "ARCAID" and tag.code == 1000 and str(tag.value).startswith("state="):
            result.append((tag.code, "state=<runtime>"))
            continue
        if tag.code in omit or (semantic and tag.code in (5, 105, 330, 360)):
            continue
        result.append((tag.code, _raw(tag.value)))
    return result


def fingerprint(e):
    """전체 XDATA를 포함한 정확한 DXF 지문. LEADER 등도 읽는다."""
    return _digest(_tags(e), 64)


def records(doc):
    """ARCAID 사전의 원시 XRECORD. 모르는 기존 기록도 손실 없이 비교한다."""
    root = doc.rootdict.get("ARCAID")
    if root is None:
        return {}
    if root.dxftype() != "DICTIONARY":
        raise ValueError("ARCAID 적용 기록 사전이 아님")
    out = {}
    for op, entry in root.items():
        if not hasattr(entry, "dxftype") or entry.dxftype() != "XRECORD":
            raise ValueError(f"적용 기록이 XRECORD가 아님: {op}")
        out[op] = [(t.code, _raw(t.value)) for t in entry.tags]
    return out


def _record_json(raw):
    strings = [v for c, v in raw if c == 1]
    if not strings or strings[0] != "arcaid-change-record/1":
        raise ValueError("적용 기록 머리말 불일치")
    return json.loads("".join(strings[1:]))


def select(doc, change):
    """명세 선택을 독립적으로 해석한다. 순서는 명세·모형 공간 순서다."""
    selector = change["select"]
    msp = {e.dxf.handle: e for e in doc.modelspace()}
    if "handles" in selector:
        found = [msp[h.upper()] for h in selector["handles"]]
    elif "oids" in selector:
        found = [e for oid in selector["oids"] for e in msp.values()
                 if (arcaid_tags(e) or {}).get("oid") == oid]
        if len(found) != len(selector["oids"]):
            raise ValueError("oid 선택이 없거나 중복됨")
    else:
        found = [e for e in msp.values() if (arcaid_tags(e) or {}).get("op") == selector["op"]
                 and (arcaid_tags(e) or {}).get("path", "").startswith(selector["path_prefix"])]
    if not found:
        raise ValueError("변경 대상이 없음")
    return found


def targets(doc, request):
    result = {}
    for change in request["changes"]:
        for e in select(doc, change):
            if e.dxf.handle in result:
                raise ValueError("중복 변경 대상")
            if e.dxftype() not in KINDS:
                raise ValueError(f"지원 밖 변경 대상: {e.dxftype()}")
            for item in [e] + (list(e.attribs) if e.dxftype() == "INSERT" else []):
                if tuple(item.dxf.get("extrusion", (0, 0, 1))) != (0, 0, 1):
                    raise ValueError("extrusion=(0,0,1)만 허용")
            result[e.dxf.handle] = change
    return result


def _clone(doc):
    # DXF 재저장·재읽기는 명시된 const_width=0 등을 생략한다. 지문 기준은 그대로 보존한다.
    return copy.deepcopy(doc)


def expected_document(before, request):
    """ezdxf에 직접 이동·삭제·값 대입. 제품의 after 계산을 호출하지 않는다."""
    chosen = targets(before, request)
    doc = _clone(before)
    for handle, change in chosen.items():
        e = doc.entitydb[handle]
        if change["action"] == "delete":
            doc.modelspace().delete_entity(e)
            continue
        if change["action"] == "move":
            dx, dy = change["delta"]
            e.translate(dx, dy, 0)
        else:
            for key, value in change["props"].items():
                if key == "attribs":
                    values = {k.casefold(): v for k, v in value.items()}
                    for a in e.attribs:
                        if a.dxf.tag.casefold() in values:
                            a.dxf.text = values[a.dxf.tag.casefold()]
                elif key == "color":
                    if isinstance(value, dict):
                        e.rgb = tuple(value["rgb"])
                    else:
                        e.dxf.color = value
                        e.dxf.discard("true_color")
                else:
                    setattr(e.dxf, key, value)
        if e.has_xdata("ARCAID"):
            e.set_xdata("ARCAID", [(t.code, "state=" + state_hash(e) if t.code == 1000
                                   and str(t.value).startswith("state=") else t.value)
                                  for t in e.get_xdata("ARCAID")])
    return doc


def _geometry(e):
    """점은 3D, 원·호는 OCS에서 WCS로 옮긴 중심·끝점, 나머지 DXF는 정확히 비교."""
    kind = e.dxftype()
    # translate()가 기본 법선을 명시적으로 쓸 수 있다. 생략 여부 대신 값을 정확히 비교한다.
    out, omit = {"extrusion": list(e.dxf.get("extrusion", (0, 0, 1)))}, {210, 220, 230}
    def point(name, value, code):
        out["point:" + name] = list(value)
        omit.add(code)
    def length(name, default, code):
        out["length:" + name] = float(e.dxf.get(name, default))
        omit.add(code)
    if kind == "LINE":
        point("start", e.dxf.start, 10)
        point("end", e.dxf.end, 11)
    elif kind == "LWPOLYLINE":
        out["vertices"] = [list(p) for p in e.vertices_in_wcs()]
        out["widths"] = [list(p[2:4]) for p in e.get_points("xyseb")]
        out["bulges"] = [p[4] for p in e.get_points("xyseb")]
        omit.update((10, 20, 38, 40, 41, 42))
        length("const_width", 0, 43)
    elif kind in ("ARC", "CIRCLE"):
        point("center", e.ocs().to_wcs(e.dxf.center), 10)
        length("radius", 0, 40)
        length("thickness", 0, 39)
        if kind == "ARC":
            point("start", e.start_point, 50)
            point("end", e.end_point, 51)
    elif kind in ("TEXT", "ATTRIB"):
        h, v = e.dxf.get("halign", 0), e.dxf.get("valign", 0)
        if h in (3, 5) or (h, v) == (0, 0):
            point("insert", e.ocs().to_wcs(e.dxf.insert), 10)
        if h in (3, 5) or (h, v) != (0, 0):
            point("align", e.ocs().to_wcs(e.dxf.get("align_point", (0, 0, 0))), 11)
        # CAD가 재계산하는 문자 삽입점은 의미 있는 정렬점으로 검사한다.
        omit.update((10, 11))
        length("height", 0, 40)
    elif kind == "INSERT":
        point("insert", e.ocs().to_wcs(e.dxf.insert), 10)
    return out, omit


def _geometry_equal(a, b):
    if a.keys() != b.keys():
        return False
    for key, value in a.items():
        other = b[key]
        if key == "extrusion":
            ok = value == other
        elif key.startswith("point:"):
            ok = math.dist(value, other) <= TOL + 1e-9
        elif key == "vertices":
            ok = len(value) == len(other) and all(math.dist(p, q) <= TOL + 1e-9 for p, q in zip(value, other))
        elif key == "widths":
            ok = len(value) == len(other) and all(abs(x-y) <= TOL + 1e-9 for p, q in zip(value, other) for x, y in zip(p, q))
        elif key == "bulges":
            ok = len(value) == len(other) and all(abs(x-y) <= 1e-9 for x, y in zip(value, other))
        else:
            ok = abs(value-other) <= TOL + 1e-9
        if not ok:
            return False
    return True


def _text_record(e):
    """공개 기록 형식의 원시 상태 직렬화(반올림 없음)."""
    h, v = e.dxf.get("halign", 0), e.dxf.get("valign", 0)
    s = dict(text=e.dxf.text, height=float(e.dxf.height), rotation=float(e.dxf.get("rotation", 0)),
             width=float(e.dxf.get("width", 1)), oblique=float(e.dxf.get("oblique", 0)), halign=h, valign=v,
             style=e.dxf.get("style", "Standard").casefold(), text_generation_flag=e.dxf.get("text_generation_flag", 0),
             extrusion=list(map(float, e.dxf.get("extrusion", (0, 0, 1)))))
    if h in (3, 5) or (h, v) == (0, 0):
        s["insert"] = list(map(float, e.dxf.insert))
    if h in (3, 5) or (h, v) != (0, 0):
        s["align_point"] = list(map(float, e.dxf.get("align_point", (0, 0, 0))))
    if e.dxftype() == "ATTRIB":
        s.update(flags=e.dxf.get("flags", 0), multiline=e.has_embedded_mtext_entity)
    return s


def _record_state(e):
    s = dict(type=e.dxftype(), layer=e.dxf.get("layer", "0").casefold(), color=e.dxf.get("color", 256),
             true_color=e.dxf.get("true_color"), linetype=e.dxf.get("linetype", "BYLAYER").casefold(),
             lineweight=e.dxf.get("lineweight", -1), extrusion=list(e.dxf.get("extrusion", (0, 0, 1))))
    # Vec3는 float지만 생략된 extrusion은 정수 튜플이므로 계약대로 float로 통일한다.
    s["extrusion"] = list(map(float, s["extrusion"]))
    if s["true_color"] is not None:
        s["color"] = None
    kind = e.dxftype()
    if kind == "LINE":
        s.update(start=list(e.dxf.start), end=list(e.dxf.end))
    elif kind == "LWPOLYLINE":
        s.update(points=[list(map(float, p)) for p in e.get_points("xyseb")], closed=bool(e.closed),
                 const_width=float(e.dxf.get("const_width", 0)), elevation=float(e.dxf.get("elevation", 0)))
    elif kind in ("ARC", "CIRCLE"):
        s.update(center=list(e.dxf.center), radius=float(e.dxf.radius), thickness=float(e.dxf.get("thickness", 0)))
        if kind == "ARC":
            s.update(start_angle=float(e.dxf.start_angle), end_angle=float(e.dxf.end_angle))
    elif kind == "TEXT":
        s.update(_text_record(e))
    elif kind == "INSERT":
        s.update(name=e.dxf.name.casefold(), insert=list(e.dxf.insert),
                 **{k: float(e.dxf.get(k, default)) for k, default in
                    (("xscale", 1), ("yscale", 1), ("zscale", 1), ("rotation", 0), ("row_spacing", 0), ("column_spacing", 0))},
                 row_count=e.dxf.get("row_count", 1), column_count=e.dxf.get("column_count", 1),
                 attribs=sorted([dict(tag=a.dxf.tag, **_text_record(a)) for a in e.attribs], key=lambda a: a["tag"]))
    tags = arcaid_tags(e) or {}
    s["tags"] = {k: tags[k] for k in ("op", "oid", "path", "ver", "in") if k in tags}
    return s


def _input_hash(request):
    """ChangeSpec 공개 JSON 계약의 생략 기본값을 채운다. 제품 모델은 사용하지 않는다."""
    normalized = copy.deepcopy(request)
    normalized["target"].setdefault("drawing_id", None)
    for c in normalized["changes"]:
        for key in ("handles", "oids", "op", "path_prefix"):
            c["select"].setdefault(key, None)
        if c["select"]["handles"]:
            c["select"]["handles"] = [h.upper() for h in c["select"]["handles"]]
        c.setdefault("delta", None)
        if c["delta"] is not None:
            c["delta"] = list(map(float, c["delta"]))
        c.setdefault("props", None)
        if c["props"] is not None:
            for key in ("layer", "color", "linetype", "lineweight", "text", "attribs"):
                c["props"].setdefault(key, None)
    return _digest(normalized)


def expected_record(request, before, after):
    return {"op": request["op"], "in": _input_hash(request), "result": "applied", "targets": {
        h: {"action": c["action"], "after_hash": None if c["action"] == "delete" else _digest(_record_state(after.entitydb[h]))}
        for h, c in targets(before, request).items()}}


def _proof(problems):
    return {"equal": not problems, "problem_count": len(problems), "problems": problems}


def _float_tag_difference(a, b, rel_tol):
    """태그 구조·타입을 보존한 실수 오차의 최댓값. 허용 밖 차이는 None이다."""
    a, b = _tags(a), _tags(b)
    if len(a) != len(b):
        return None
    max_diff = 0.0
    for (code, value), (other_code, other) in zip(a, b):
        if code != other_code:
            return None
        if isinstance(value, float) and isinstance(other, float):
            if not math.isclose(value, other, rel_tol=rel_tol, abs_tol=1e-12):
                return None
            max_diff = max(max_diff, abs(value - other))
        elif type(value) is not type(other) or value != other:
            return None
    return max_diff


def compare_unchanged(before, after, *, float_rel_tol=None):
    a, b = graphics(before), graphics(after)
    problems = [f"핸들 집합 차이: {h}" for h in sorted(set(a) ^ set(b))]
    tolerated = dict(handles=0, max_abs_diff=0.0)
    for h in sorted(set(a) & set(b)):
        if fingerprint(a[h]) == fingerprint(b[h]):
            continue
        if float_rel_tol is not None:
            diff = _float_tag_difference(a[h], b[h], float_rel_tol)
            if diff is not None:
                tolerated["handles"] += 1
                tolerated["max_abs_diff"] = max(tolerated["max_abs_diff"], diff)
                continue
        problems.append(f"{h}: DXF 지문 변경")
    if records(before) != records(after):
        problems.append("적용 기록 변경")
    proof = _proof(problems)
    if float_rel_tol is not None:
        proof["float_tolerated"] = tolerated
    return proof


def compare_transition(before, after, request, *, records="applied"):
    """같은 사본의 전후. 대상 밖은 정확 지문, 대상은 3D 형상+나머지 DXF+전체 XDATA."""
    wanted = expected_document(before, request)
    a, b = graphics(wanted), graphics(after)
    original = graphics(before)
    changed = targets(before, request)
    affected = set(changed)
    for h in changed:
        e = original[h]
        if e.dxftype() == "INSERT":
            affected.update(x.dxf.handle for x in e.attribs)
    problems = [f"핸들 집합 차이: {h}" for h in sorted(set(a) ^ set(b))]
    for h in sorted(set(a) & set(b)):
        if h not in affected:
            if fingerprint(original[h]) != fingerprint(b[h]):
                problems.append(f"{h}: 대상 밖 DXF 지문 변경")
            continue
        ga, omit = _geometry(a[h])
        gb, _ = _geometry(b[h])
        if not _geometry_equal(ga, gb):
            problems.append(f"{h}: 기대 3D 형상 불일치")
        ignore = h in changed and bool(arcaid_tags(a[h]))
        if _tags(a[h], omit=omit, own=True, ignore_state=ignore) != _tags(b[h], omit=omit, own=True, ignore_state=ignore):
            problems.append(f"{h}: DXF 속성·전체 XDATA 불일치")
        if ignore and (arcaid_tags(b[h]) or {}).get("state") != state_hash(b[h]):
            problems.append(f"{h}: state 태그 미갱신")
    if records != "ignore":
        old, now = globals()["records"](before), globals()["records"](after)
        if any(now.get(k) != v for k, v in old.items()):
            problems.append("기존 적용 기록 변경·소실")
        if set(now) != set(old) | {request["op"]}:
            problems.append("새 적용 기록 집합 불일치")
        try:
            if _record_json(now.get(request["op"], [])) != expected_record(request, before, after):
                problems.append("새 적용 기록 내용 불일치")
        except (ValueError, TypeError, KeyError):
            problems.append("새 적용 기록 부재·손상")
    return _proof(problems)


def _semantic_keys(doc):
    keys = {}
    for h, e in graphics(doc).items():
        t = arcaid_tags(e) or {}
        key = ("engine", t["op"], t["path"]) if "op" in t and "path" in t else ("handle", h)
        if key in keys:
            raise ValueError(f"중복 op·의미 경로: {key}")
        keys[key] = e
    # 속성 핸들도 부모 의미 경로와 속성 태그로 대응한다.
    for e in doc.modelspace().query("INSERT"):
        t = arcaid_tags(e) or {}
        if "op" in t and "path" in t:
            for a in e.attribs:
                keys.pop(("handle", a.dxf.handle), None)
                key = ("attrib", t["op"], t["path"], a.dxf.tag)
                if key in keys:
                    raise ValueError("중복 속성 의미 경로")
                keys[key] = a
    return keys


def compare_copies(a, b, *, baselines=None):
    """A/B는 op·의미 경로로 대응한다. 요청별 snapshot 입력 해시는 서로 달라도 된다."""
    left, right = _semantic_keys(a), _semantic_keys(b)
    problems = [f"A/B 객체 집합 차이: {k}" for k in sorted(set(left) ^ set(right))]
    for key in sorted(set(left) & set(right)):
        if _tags(left[key], own=True, semantic=True) != _tags(right[key], own=True, semantic=True):
            problems.append(f"A/B DXF 차이: {key}")
    def normalized_records(doc, baseline):
        mapping = {e.dxf.handle: key for key, e in _semantic_keys(baseline).items()}
        out = {}
        for op, raw in records(doc).items():
            if op not in OPS:
                out[op] = raw
                continue
            rec = _record_json(raw)
            rec.pop("in", None)
            rec["targets"] = {str(mapping[h]): v for h, v in rec["targets"].items()}
            out[op] = rec
        return out
    bases = baselines or (a, b)
    if normalized_records(a, bases[0]) != normalized_records(b, bases[1]):
        problems.append("A/B 적용 기록 차이")
    return _proof(problems)


def _request(op, changes, standard):
    return dict(schema_version=1, standard=standard, op=op,
                target=dict(drawing_id=None, snapshot_id=SNAPSHOT_PLACEHOLDER), changes=changes)


def _prefix(path):
    return dict(op="t10-rooms", path_prefix=path)


def describe_request(doc, request):
    chosen = targets(doc, request)
    return dict(op=request["op"], count=len(chosen),
                kinds=dict(sorted(Counter(doc.entitydb[h].dxftype() for h in chosen).items())),
                layers=dict(sorted(Counter(doc.entitydb[h].dxf.layer for h in chosen).items())),
                targets=[dict(handle=h, kind=doc.entitydb[h].dxftype(), layer=doc.entitydb[h].dxf.layer,
                              oid=(arcaid_tags(doc.entitydb[h]) or {}).get("oid"),
                              path=(arcaid_tags(doc.entitydb[h]) or {}).get("path"),
                              change_id=c["id"], action=c["action"]) for h, c in chosen.items()])


def validate_runtime(doc, request, description):
    """새 Export에서 수·관계·extrusion·중복 oid·state를 재확인하며 태그는 쓰지 않는다."""
    from src.engine.changes import parse_changes, resolve
    from src.engine.standards import load_standard
    if doc.header.get("$INSUNITS") != 4:
        raise ValueError("실행 사본 단위가 mm가 아님")
    resolve(parse_changes(request), doc, load_standard(), request["target"]["snapshot_id"])
    actual = describe_request(doc, request)
    for key in ("count", "kinds", "layers"):
        if actual[key] != description[key]:
            raise ValueError(f"실행 사본 대상 {key} 불일치")
    def identities(items):
        return sorted((x["oid"] or x["handle"], x["path"], x["kind"], x["action"], x["change_id"]) for x in items)
    if identities(actual["targets"]) != identities(description["targets"]):
        raise ValueError("실행 사본 대상 정체성 불일치")
    for h in targets(doc, request):
        e = doc.entitydb[h]
        if arcaid_tags(e) and arcaid_tags(e).get("state") != state_hash(e):
            raise ValueError(f"실행 사본 state 불일치: {h}")
    return actual


def _units_copy(source, dest):
    data = Path(source).read_bytes()
    # 재저장은 GUID·시각 등도 바꾼다. 원시 ASCII DXF의 이 헤더 값 하나만 고친다.
    changed, n = re.subn(rb"(?m)(^[ \t]*9\r?\n\$INSUNITS\r?\n[ \t]*70\r?\n[ \t]*)[+-]?\d+",
                         lambda m: m[1] + b"4", data, count=1)
    if n != 1:
        raise ValueError("ASCII DXF $INSUNITS 헤더가 필요함")
    Path(dest).write_bytes(changed)


def _preview(doc, request, path):
    chosen = targets(doc, request)
    # 색만 바꾼 메모리 사본을 렌더한다. 원본 DXF에는 쓰지 않는다.
    view = _clone(doc)
    for e in view.modelspace():
        e.dxf.discard("true_color")
        e.dxf.color = 1 if e.dxf.handle in chosen else 8
    from ezdxf.addons.drawing import Frontend, RenderContext, config, layout, svg
    backend = svg.SVGBackend()
    Frontend(RenderContext(view), backend, config=config.Configuration(background_policy=config.BackgroundPolicy.WHITE)).draw_layout(view.modelspace())
    text = backend.get_string(layout.Page(300, 180))
    Path(path).write_text(text, encoding="utf-8")


def prepare(inputs, out, *, engine_dwg=None):
    """고정 입력에서 결정적으로 선택한다. 출력 폴더는 반드시 새 폴더여야 한다."""
    from src.engine.changes import parse_changes, resolve
    from src.engine.standards import load_standard
    inputs, out = Path(inputs).resolve(), Path(out)
    if out.exists():
        raise FileExistsError(f"새 출력 폴더 필요: {out}")
    files = {name: file_identity(inputs / name) for name in
             ("engine-A-snapshot.dxf", "engine-A-snapshot.json", "human-source-snapshot.dxf", "t10-rooms.json", "t10-annotations.json")}
    meta = load_json(inputs / "engine-A-snapshot.json")
    if meta["dxf_sha256"] != files["engine-A-snapshot.dxf"]["sha256"] or meta["method"] != "open_document":
        raise ValueError("COM Export 입력 메타·해시 불일치")
    dwg = Path(engine_dwg) if engine_dwg else ROOT / "output/t10/dangsan-A.dwg"
    files["engine-dwg"] = file_identity(dwg)
    std = load_standard()
    engine = ezdxf.readfile(inputs / "engine-A-snapshot.dxf")
    human = ezdxf.readfile(inputs / "human-source-snapshot.dxf")
    if engine.header.get("$INSUNITS") != 4:
        raise ValueError("입력 엔진 단위가 mm가 아님")
    for key, value in (("drawing_id", engine.header.get("$FINGERPRINTGUID")), ("dxfversion", engine.dxfversion), ("insunits", 4)):
        if key in meta and meta[key] != value:
            raise ValueError(f"입력 엔진 메타 불일치: {key}")
    all_oids = []
    for e in graphics(engine).values():
        t = arcaid_tags(e)
        if t:
            if not all(k in t for k in ("op", "oid", "path", "ver", "in", "state")) or t["state"] != state_hash(e):
                raise ValueError(f"입력 엔진 태그·state 불일치: {e.dxf.handle}")
            all_oids.append(t["oid"])
    if len(set(all_oids)) != len(all_oids):
        raise ValueError("입력 중복 oid")
    rooms = {}
    for e in engine.modelspace():
        t = arcaid_tags(e) or {}
        if t.get("op") == "t10-rooms" and re.match(r"^room/\d+/", t.get("path", "")):
            rooms.setdefault(int(t["path"].split("/")[1]), []).append(e)
    candidates = sorted(n for n, es in rooms.items() if len(es) == 637)
    if len(candidates) < 2:
        raise ValueError("637개 객실 두 실이 필요함")
    move_room, edit_room = candidates[:2]
    room_box = bbox.extents(rooms[move_room])
    all_box = bbox.extents(engine.modelspace())
    # 도면 오른쪽 빈 쪽으로 보낸다. 다른 도형의 전체 경계와 1m 간격을 둔다.
    delta = [float(math.ceil((all_box.extmax.x + 1000 - room_box.extmin.x) / 100) * 100), 0.0]
    move = _request(OPS[0], [dict(id="whole-room", select=_prefix(f"room/{move_room}/"), action="move", delta=delta)], std.ref)
    deletes = []
    for group, count in (("shape3[0]", 43), ("shape5[0]", 16)):
        path = f"room/{edit_room}/{group}/"
        c = dict(id="bed" if count == 43 else "window", select=_prefix(path), action="delete")
        es = select(engine, c)
        if len(es) != count or {e.dxf.layer for e in es} != ({"FUR"} if count == 43 else {"WIN"}):
            raise ValueError(f"침대·창 경로·기대 개수 불일치: {path}")
        deletes.append(dict(path=path, expected=count))
    edit_box = bbox.extents(rooms[edit_room])
    annotations = [e for e in engine.modelspace() if (arcaid_tags(e) or {}).get("op") == "t10-annotations"]
    texts = []
    for e in annotations:
        if e.dxftype() == "TEXT" and e.dxf.layer == "ROM" and "객실" in e.dxf.text:
            p = e.dxf.align_point if e.dxf.get("halign", 0) or e.dxf.get("valign", 0) else e.dxf.insert
            if edit_box.extmin.x <= p.x <= edit_box.extmax.x and edit_box.extmin.y <= p.y <= edit_box.extmax.y:
                texts.append(e)
    if len(texts) != 1:
        raise ValueError("공간상 대응하는 객실 실명 문자가 정확히 하나여야 함")
    text = texts[0]
    bubbles = sorted((e for e in annotations if e.dxftype() == "INSERT" and e.attribs),
                     key=lambda e: arcaid_tags(e)["oid"])
    if not bubbles:
        raise ValueError("축열 버블이 없음")
    bubble = bubbles[0]
    attr = sorted(bubble.attribs, key=lambda a: a.dxf.tag)[0]
    new_text, new_attr = f"객실(B5-{edit_room})", attr.dxf.text + "-B5"
    edit_changes = [dict(id="bed" if d["expected"] == 43 else "window", select=_prefix(d["path"]), action="delete") for d in deletes]
    edit_changes += [dict(id="room-name", select=dict(oids=[arcaid_tags(text)["oid"]]), action="set", props=dict(text=new_text)),
                     dict(id="bubble", select=dict(oids=[arcaid_tags(bubble)["oid"]]), action="set", props=dict(attribs={attr.dxf.tag: new_attr}))]
    edit = _request(OPS[1], edit_changes, std.ref)
    human_changes = []
    for kind, action in (("LWPOLYLINE", "move"), ("LINE", "delete"), ("TEXT", "set")):
        eligible = [e for e in human.modelspace() if e.dxftype() == kind
                    and not any((human.layers.get(e.dxf.layer).is_locked(), human.layers.get(e.dxf.layer).is_frozen(),
                                 human.layers.get(e.dxf.layer).is_off())) and not arcaid_tags(e)]
        eligible.sort(key=lambda e: int(e.dxf.handle, 16))
        chosen = None
        for e in eligible:
            c = dict(id="human-" + kind.lower(), select=dict(handles=[e.dxf.handle]), action=action)
            if action == "move":
                c["delta"] = [600.0, -300.0]
            elif action == "set":
                c["props"] = dict(text=e.dxf.text + " B5")
            try:
                resolve(parse_changes(_request(OPS[2], [c], std.ref)), human, std, SNAPSHOT_PLACEHOLDER)
            except ValueError:
                continue
            except Exception as error:
                from src.engine.changes import ChangeError
                if not isinstance(error, ChangeError):
                    raise
                continue
            chosen = c
            break
        if chosen is None:
            raise ValueError(f"사람 대상 선택 불가: {kind}")
        human_changes.append(chosen)
    human_request = _request(OPS[2], human_changes, std.ref)
    requests = [move, edit, human_request]
    descriptions = []
    for doc, request in zip((engine, engine, human), requests):
        resolve(parse_changes(request), doc, std, SNAPSHOT_PLACEHOLDER)
        descriptions.append(describe_request(doc, request))
    manifest = dict(schema_version=1, status="prepared-awaiting-human-review", inputs=files,
                    provenance=dict(engine_snapshot=meta, engine_dwg_binding="사용자 제공 T10 최종 A의 로컬 대응 DWG; 원본 메타와 양쪽 해시를 보존",
                                    human_units="좌표 mm 보존, $INSUNITS 6 → 4만 변경"),
                    engine_counts=dict(modelspace=len(engine.modelspace()), tagged=len(all_oids)),
                    selection=dict(move_room=move_room, edit_room=edit_room, delta=delta, move_bounds=[list(room_box.extmin), list(room_box.extmax)],
                                   edit_bounds=[list(edit_box.extmin), list(edit_box.extmax)], deletes=deletes,
                                   text=dict(oid=arcaid_tags(text)["oid"], old=text.dxf.text, new=new_text),
                                   bubble=dict(oid=arcaid_tags(bubble)["oid"], tag=attr.dxf.tag, old=attr.dxf.text, new=new_attr),
                                   human=human_changes), requests=descriptions)
    out.mkdir(parents=True, exist_ok=False)
    _units_copy(inputs / "human-source-snapshot.dxf", out / "human-base.dxf")
    # 입력 사본을 함께 묶어 승인 후 다른 위치로 옮겨도 같은 DXF를 사용한다.
    (out / "engine-base.dxf").write_bytes((inputs / "engine-A-snapshot.dxf").read_bytes())
    for doc, request in zip((engine, engine, human), requests):
        write_json(out / f"{request['op']}.json", request)
        _preview(doc, request, out / f"{request['op']}-before.svg")
    lines = ["# B5 사람 확인용 준비 결과", "", "CAD·MCP 미실행. 빨간색은 변경 전 대상, 회색은 나머지 도형.", "",
             f"객실 {move_room} 전체 637개 이동: delta={delta} mm (전체 도형 경계 오른쪽 빈 공간).",
             f"다른 객실 {edit_room}: 침대 43개 + 창 16개 삭제, 실명·축열 속성 각 1개 변경.",
             f"실명: {arcaid_tags(text)['oid']} / {text.dxf.text} → {new_text}",
             f"축열: {arcaid_tags(bubble)['oid']} / {attr.dxf.tag}: {attr.dxf.text} → {new_attr}", ""]
    lines += [f"- {r['op']}: {r['count']}개 / {json.dumps(r['kinds'], ensure_ascii=False)} / [변경 전]({r['op']}-before.svg)" for r in descriptions]
    lines += ["", "사람 대상(블록 정의 내부·잠김·동결·꺼짐 레이어 제외):", "", *[f"- {c}" for c in human_changes], "",
              "실행 전 manifest SHA256를 확인해 ARCAID_B5_APPROVED_MANIFEST_SHA256에 고정한다.",
              "원·호 1단계 CAD 사전 관문 통과 후에만 CAD 시험, 그 뒤 MCP를 직렬 실행한다."]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest["artifact_hashes"] = {p.name: file_identity(p)["sha256"] for p in sorted(out.iterdir())}
    write_json(out / "manifest.json", manifest)
    return manifest


def load_prepared(path, approved=None):
    path = Path(path).resolve()
    if approved and file_identity(path / "manifest.json")["sha256"] != approved:
        raise ValueError("사람 확인 manifest 해시 불일치")
    manifest = load_json(path / "manifest.json")
    for name, digest in manifest["artifact_hashes"].items():
        if file_identity(path / name)["sha256"] != digest:
            raise ValueError(f"준비 산출물 해시 불일치: {name}")
    for identity in manifest["inputs"].values():
        if file_identity(identity["path"]) != identity:
            raise ValueError(f"입력 해시·mtime 불일치: {identity['path']}")
    return manifest


def bind_request(path, op, meta):
    request = load_json(Path(path) / f"{op}.json")
    request["target"] = dict(drawing_id=meta["drawing_id"], snapshot_id=meta["snapshot_id"])
    return request


def verify_standard_result(kind, result):
    """엔진 전역 관문과 사람 도면의 기존 위반 관찰을 명시적으로 구분한다."""
    if result["status"] != "success":
        raise AssertionError("표준 검사 실패")
    summary = {k: result.get(k, False if k == "truncated" else []) for k in
               ("violation_counts", "unchecked", "truncated")}
    if kind == "engine" and (result["violation_counts"] or result.get("violations") or result["unchecked"] or result.get("truncated")):
        raise AssertionError("엔진 전체 표준 검사 위반·미검사·잘림 발생")
    return summary


def expected_apply_unchecked(kind, description, std):
    """manifest 사람 대상 중 높이 규칙 없는 TEXT 수로 미검사 기대값을 구한다."""
    if kind == "engine":
        return []
    height_layers = {layer.casefold() for rule in list(std.text.heights) + list(std.text.sheet_title)
                     for layer in rule.layers}
    count = sum(t["kind"] == "TEXT" and t["layer"].casefold() not in height_layers
                for t in description["targets"])
    return [dict(item="높이 규칙이 없는 레이어의 문자", reason="text.yaml에 그 레이어의 문자 높이가 없음",
                 count=count)] if count else []


def verify_apply_result(kind, description, std, result):
    """엔진은 미검사 없이, 사람은 대상 TEXT에 해당하는 미검사만 허용한다."""
    expected = expected_apply_unchecked(kind, description, std)
    if result["new_violations"] != {} or result["unchecked"] != expected:
        raise AssertionError(f"대상 새 위반·미검사 불일치: 기대 미검사 {expected}, "
                             f"실제 새 위반 {result['new_violations']}, 미검사 {result['unchecked']}")
    return dict(new_violations=result["new_violations"], unchecked=result["unchecked"])


class Evidence:
    """긴 실행은 단계 시작과 결과를 즉시 기록한다. 예외 뒤 저장을 허용하지 않는다."""
    def __init__(self, path, **initial):
        self.path = Path(path)
        self.data = dict(status="running", steps=[], **initial)
        self.write()

    def write(self):
        write_json(self.path, self.data)

    def add(self, stage, **values):
        self.data["steps"].append(dict(stage=stage, **values))
        self.write()
        print(f"B5 {stage}", flush=True)


def new_work(path):
    path = Path(path).resolve()
    if not path.is_relative_to(Path("C:/arcaid/work").resolve()) or path == Path("C:/arcaid/work").resolve():
        raise ValueError("C:/arcaid/work 아래 새 폴더 필요")
    path.mkdir(parents=True, exist_ok=False)
    return path


def copy_with_new_guid(source, dest):
    if Path(dest).exists():
        raise FileExistsError(dest)
    doc = ezdxf.readfile(source)
    doc.header["$FINGERPRINTGUID"] = "{" + str(uuid.uuid4()).upper() + "}"
    doc.saveas(dest)


async def run_mcp(prepared, work):
    """공개 도구만 직렬 호출한다. COM 접근은 이 경로에 없다."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from src.engine.standards import load_standard
    std = load_standard()
    approved = os.environ.get("ARCAID_B5_APPROVED_MANIFEST_SHA256")
    if not approved:
        raise ValueError("사람 확인 ARCAID_B5_APPROVED_MANIFEST_SHA256 필요")
    prepared = Path(prepared).resolve()
    manifest = load_prepared(prepared, approved)
    work = new_work(work)
    evidence = Evidence(work / "b5-mcp-evidence.json", manifest=file_identity(prepared / "manifest.json"))
    with (work / "mcp-server.log").open("w", encoding="utf-8") as log:
        async with stdio_client(StdioServerParameters(command=sys.executable, args=[str(ROOT / "src/server.py")],
                                                      cwd=str(ROOT), env=dict(os.environ)), errlog=log) as streams:
            async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=600)) as session:
                await session.initialize()
                async def call(name, args=None, *, check=True):
                    evidence.add(name + ":start", arguments=args or {})
                    start = time.perf_counter()
                    result = await session.call_tool(name, arguments=args or {})
                    data = result.structuredContent
                    if data is None:
                        data = json.loads(next(c.text for c in result.content if c.type == "text"))
                    evidence.add(name + ":done", seconds=time.perf_counter()-start, result=data)
                    if check and (result.isError or data.get("status", "success") != "success" or data.get("success") is False):
                        raise RuntimeError(f"MCP 확인 실패: {name}: {data}")
                    return data
                async def snapshot():
                    result = await call("export_snapshot")
                    meta = result["snapshot"]
                    doc = ezdxf.readfile(meta["dxf_path"])
                    if file_identity(meta["dxf_path"])["sha256"] != meta["dxf_sha256"]:
                        raise ValueError("MCP Export 해시 불일치")
                    return meta, doc
                def prove(stage, proof):
                    evidence.add(stage, proof=proof)
                    if not proof["equal"]:
                        raise AssertionError(proof)
                def user_state(data):
                    return {d["full_name"] or d["name"]: (d["saved"], d["is_active"]) for d in data["drawings"]}
                before = user_state(await call("list_drawings"))
                owned = set()
                ids = []
                try:
                    for kind, ops in (("engine", OPS[:2]), ("human", OPS[2:])):
                        dxf, dwg = work / f"{kind}.dxf", work / f"{kind}.dwg"
                        copy_with_new_guid(prepared / f"{kind}-base.dxf", dxf)
                        owned.update((str(dxf).casefold(), str(dwg).casefold()))
                        await call("open_drawing", dict(file_path=str(dxf)))
                        # 첫 DWG 저장도 검증을 거친 뒤에만 한다.
                        meta, initial = await snapshot()
                        ids.append(meta["drawing_id"])
                        requests = []
                        for op in ops:
                            meta, base = await snapshot()
                            request = bind_request(prepared, op, meta)
                            desc = next(r for r in manifest["requests"] if r["op"] == op)
                            validate_runtime(base, request, desc)
                            result = await call("apply_changes", dict(spec=request, scale=200))
                            evidence.add(op + ":apply-scope",
                                         result=verify_apply_result(kind, desc, std, result))
                            _, after = await snapshot()
                            prove(op + ":independent", compare_transition(base, after, request))
                            requests.append(request)
                        _, applied = await snapshot()
                        for request in requests:
                            result = await call("apply_changes", dict(spec=request, scale=200))
                            if result["meta"]["decision"] != "skip":
                                raise AssertionError("재전송 skip 아님")
                        _, replayed = await snapshot()
                        prove(kind + ":replay-unchanged", compare_unchanged(applied, replayed))
                        checked = await call("check_standard", dict(scale=200))
                        evidence.add(kind + ":standard-scope", result=verify_standard_result(kind, checked))
                        await call("save_drawing", dict(file_path=str(dwg)))
                        owned.add(str(dwg).casefold())
                        await call("close_drawing", dict(save=False))
                        await call("open_drawing", dict(file_path=str(dwg)))
                        reopened_meta, reopened = await snapshot()
                        if reopened_meta["drawing_id"] != meta["drawing_id"]:
                            raise AssertionError("저장 후 drawing_id 변경")
                        prove(kind + ":reopen-tags-records", compare_unchanged(replayed, reopened, float_rel_tol=1e-12))
                        evidence.add(kind + ":saved", identity=file_identity(dwg))
                        await call("close_drawing", dict(save=False))
                    if len(set(ids)) != 2:
                        raise AssertionError("엔진·사람 도면 ID 중복")
                    evidence.data["status"] = "checks_passed"
                except BaseException as error:
                    evidence.data.update(status="failed-no-further-save", error=repr(error))
                    evidence.write()
                    raise
                finally:
                    # 현재 활성 문서를 무조건 닫지 않는다. 이름을 대조한 자기 사본만 닫는다.
                    try:
                        current = await call("list_drawings", check=False)
                        for d in current["drawings"]:
                            name = d["full_name"] or d["name"]
                            if name.casefold() in owned:
                                await call("switch_drawing", dict(name_or_path=name))
                                await call("close_drawing", dict(save=False))
                        active = next((name for name, value in before.items() if value[1]), None)
                        if active:
                            await call("switch_drawing", dict(name_or_path=active))
                        after_user = user_state(await call("list_drawings"))
                        evidence.data["user_unchanged"] = before == after_user
                        evidence.data["originals_unchanged"] = all(file_identity(v["path"]) == v for v in manifest["inputs"].values())
                        if evidence.data["status"] == "checks_passed" and before == after_user and evidence.data["originals_unchanged"]:
                            evidence.data["status"] = "passed"
                    finally:
                        evidence.write()
                if evidence.data["status"] != "passed":
                    raise AssertionError(evidence.data["status"])
    return evidence.data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--inputs", type=Path, default=ROOT / "output/b5/inputs")
    prep.add_argument("--out", type=Path, required=True)
    prep.add_argument("--engine-dwg", type=Path)
    mcp = sub.add_parser("mcp")
    mcp.add_argument("prepared", type=Path)
    mcp.add_argument("--work", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        manifest = prepare(args.inputs, args.out, engine_dwg=args.engine_dwg)
        print(json.dumps(dict(selection=manifest["selection"], requests=[{k: r[k] for k in ("op", "count", "kinds")} for r in manifest["requests"]],
                              manifest_sha256=file_identity(args.out / "manifest.json")["sha256"]), ensure_ascii=False))
    else:
        asyncio.run(run_mcp(args.prepared, args.work))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
