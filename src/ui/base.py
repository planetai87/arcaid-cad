"""기준 DXF의 모형 공간을 화면용 WCS 기본 객체로 바꾼다."""
import math

import ezdxf
from ezdxf import path as dxf_path
from ezdxf.lldxf.const import DXFError


def _xy(point):
    return [float(point[0]), float(point[1])]


def _primitive(entity):
    kind = entity.dxftype()
    d = entity.dxf
    if kind == "LINE":
        return {"kind": "line", "points": [_xy(d.start), _xy(d.end)]}
    if kind == "LWPOLYLINE":
        # vertices_in_wcs는 OCS 돌출 방향을 포함한다. bulge 부호도 WCS에 맞춘다.
        sign = 1 if entity.ocs().uz.z >= 0 else -1
        return {"kind": "polyline", "points": [_xy(p) for p in entity.vertices_in_wcs()],
                "bulges": [float(p[4]) * sign for p in entity], "closed": entity.closed}
    if kind == "POLYLINE":
        sign = 1 if entity.ocs().uz.z >= 0 else -1
        return {"kind": "polyline", "points": [_xy(p) for p in entity.points_in_wcs()],
                "bulges": [float(v.dxf.get("bulge", 0)) * sign for v in entity.vertices],
                "closed": entity.is_closed}
    if kind == "ELLIPSE":
        # 비균등 INSERT의 원·호가 virtual_entities에서 타원으로 바뀐다.
        return {"kind": "polyline", "points": [_xy(p) for p in entity.flattening(distance=0.5)],
                "closed": math.isclose((d.end_param - d.start_param) % math.tau, 0, abs_tol=1e-9)}
    if kind in ("CIRCLE", "ARC"):
        center = entity.ocs().to_wcs(d.center)
        result = {"kind": kind.lower(), "center": _xy(center), "radius": float(d.radius)}
        if kind == "ARC":
            a, b = float(d.start_angle), float(d.end_angle)
            if entity.ocs().uz.z < 0:
                a, b = 180 - b, 180 - a
            result.update(start_angle=a, end_angle=b)
        return result
    if kind in ("TEXT", "ATTRIB", "MTEXT"):
        if kind == "MTEXT":
            align = {1: "TL", 2: "TC", 3: "TR", 4: "ML", 5: "MC", 6: "MR",
                     7: "BL", 8: "BC", 9: "BR"}.get(d.attachment_point, "TL")
            return {"kind": "text", "at": _xy(d.insert), "text": entity.plain_text(),
                    "height": float(d.char_height), "rotation": entity.get_rotation(), "align": align}
        alignment, at, _ = entity.get_placement()
        align = alignment.name
        if align in ("LEFT", "CENTER", "RIGHT"):
            align = {"LEFT": "L", "CENTER": "C", "RIGHT": "R"}[align]
        elif "_" in align:
            v, h = align.split("_")
            align = v[0] + h[0]
        return {"kind": "text", "at": _xy(entity.ocs().to_wcs(at)), "text": entity.plain_text(),
                "height": float(d.height), "rotation": float(d.get("rotation", 0)), "align": align}
    if kind == "HATCH":
        loops = [[_xy(p) for p in boundary.flattening(distance=1)]
                 for boundary in dxf_path.from_hatch(entity)]
        return {"kind": "hatch", "loops": loops, "pattern": d.pattern_name}
    return None


def _extents(prims):
    points = []
    for prim in prims:
        points.extend(prim.get("points", []))
        for loop in prim.get("loops", []):
            points.extend(loop)
        if prim.get("at"):
            x, y = prim["at"]
            height = prim.get("height", 0)
            width = height * max(1, len(prim.get("text", "")))
            points.extend([[x - width, y - height], [x + width, y + height]])
        if prim.get("center"):
            x, y = prim["center"]
            r = prim["radius"]
            points.extend([[x - r, y - r], [x + r, y + r]])
        # 큰 bulge도 화면 맞춤에서 잘리지 않게 원 전체를 보수적으로 포함한다.
        vertices = prim.get("points", [])
        count = len(vertices) if prim.get("closed") else len(vertices) - 1
        for i in range(max(0, count)):
            bulge = (prim.get("bulges") or [0] * len(vertices))[i]
            if abs(bulge) < 1e-12:
                continue
            a, b = vertices[i], vertices[(i + 1) % len(vertices)]
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
            if not length:
                continue
            offset = length * (1 - bulge * bulge) / (4 * bulge)
            cx = (a[0] + b[0]) / 2 - dy / length * offset
            cy = (a[1] + b[1]) / 2 + dx / length * offset
            radius = length * (1 + bulge * bulge) / (4 * abs(bulge))
            points.extend([[cx - radius, cy - radius], [cx + radius, cy + radius]])
    finite = [p for p in points if all(math.isfinite(v) for v in p)]
    if not finite:
        return None
    return {"minX": min(p[0] for p in finite), "minY": min(p[1] for p in finite),
            "maxX": max(p[0] for p in finite), "maxY": max(p[1] for p in finite)}


def read_base(path, limit=50000):
    try:
        doc = ezdxf.readfile(str(path))
    except DXFError as error:
        raise ValueError(f"올바른 DXF가 아닙니다: {error}") from error
    prims, warnings = [], []
    truncated = False
    visited = 0

    def walk(entities, parent=None, inherited_layer=None, depth=0):
        nonlocal truncated, visited
        for entity in entities:
            visited += 1
            if len(prims) >= limit or visited > max(limit * 10, 1000):
                truncated = True
                return
            handle = parent or entity.dxf.get("handle", "")
            layer = entity.dxf.get("layer", "0")
            if layer == "0" and inherited_layer:
                layer = inherited_layer
            kind = entity.dxftype()
            try:
                if kind in ("INSERT", "DIMENSION"):
                    if depth >= 32:
                        warnings.append(f"중첩 깊이 초과: {handle}")
                        continue
                    if kind == "INSERT" and entity.mcount > 1:
                        walk(entity.multi_insert(), handle, layer, depth + 1)
                    else:
                        walk(entity.virtual_entities(), handle, layer, depth + 1)
                        if kind == "INSERT":
                            walk(entity.attribs, handle, layer, depth + 1)
                    if truncated:
                        return
                    continue
                prim = _primitive(entity)
                if prim:
                    prim.update(handle=handle, layer=layer)
                    prims.append(prim)
            except (DXFError, ValueError, TypeError, AttributeError, ZeroDivisionError) as error:
                if len(warnings) < 100:
                    warnings.append(f"객체 {handle}({kind}) 표시 생략: {error}")
    walk(doc.modelspace())
    if truncated:
        warnings.append(f"기준 도면이 커서 {len(prims):,}개까지만 표시합니다")
    return {"prims": prims, "extents": _extents(prims), "truncated": truncated, "warnings": warnings}
