"""같은 파일 바이트에서 명세·전개 결과와 편집 기준 해시를 읽는다."""
import hashlib
import json
import math
import re
from ezdxf.colors import aci2rgb
from src.engine.layout import expand, source_transform
from src.engine.spec import SpecError, parse_coord, parse_spec, validate_spec
from src.engine.standards import load_standard

def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("좌표와 수치는 유한한 숫자여야 합니다")
    return number


def _handles(raw, spec, prims):
    """합쳐진 벽도 원본 조각의 실제 꼭짓점만 편집하도록 좌표를 따로 제공한다."""
    out = {}
    for prim in prims:
        handles = []
        for source in prim.sources:
            item = raw
            for index in re.findall(r'items\[(\d+)\]', source.item):
                item = item['items'][int(index)]
            transform = source_transform(spec, source)
            def add(field, point, index=None):
                local = []
                for value, axis in zip(point, ('X', 'Y')):
                    name, offset = parse_coord(value)
                    local.append(offset + (getattr(spec.grid, axis)[name] if name else 0))
                handle = {'field': field, 'world': transform.pt(local), 'source': source.model_dump(mode='json')}
                if index is not None:
                    handle['index'] = index
                handles.append(handle)
            for field in ('start', 'end', 'center', 'at', 'p1', 'p2'):
                if field in item:
                    add(field, item[field])
            for field in ('points', 'boundary'):
                points = item.get(field, [])
                indices = range(len(points))
                if item['kind'] == 'dim_chain' and field == 'points':
                    # 각 치수 구간은 그 구간의 두 끝점만 수정한다(긴 체인의 중복 전개 방지).
                    first = int(prim.oid.rsplit('.', 1)[1]) - 1
                    indices = (first, first + 1)
                for i in indices:
                    add(field, points[i], i)
            for field in ('holes', 'parts', 'openings'):
                for loop_index, loop in enumerate(item.get(field, [])):
                    for i, point in enumerate(loop):
                        add(f'{field}[{loop_index}]', point, i)
        out[prim.oid] = handles
    return out


def read_spec(path, content=None):
    result = {"spec": None, "warnings": [], "violations": [], "grid": {}, "layers": {}}
    try:
        content = path.read_bytes() if content is None else content
        result["sha256"] = hashlib.sha256(content).hexdigest()
        text = content.decode("utf-8-sig")
        raw = json.loads(text, parse_float=_finite_float, parse_constant=_finite_float)
        result["spec"] = raw
        spec = parse_spec(raw)
        # load_standard와 validate_spec을 함께 써서 다른 표준 참조를 조용히 대체하지 않는다.
        std = load_standard()
        violations = validate_spec(spec, std)
        if violations:
            raise SpecError(violations)
        k = spec.scale if spec.scale is not None else std.profile.scale.default_k
        prims = expand(spec, std, k, warnings=result["warnings"])
        # 치수 문자는 CAD와 같은 실제 높이로 그린다: dimtxt × DIMSCALE(write.py와 같은 k 환산)
        dimscale = k * std.profile.scale.dimscale_per_k
        dimstyles = {d.name: {"text_height": float(d.vars.get("dimtxt", 2.5)) * dimscale,
                              "gap": float(d.vars.get("dimgap", 1.0)) * dimscale}
                     for d in std.dimensions.styles}
        result["handles"] = _handles(raw, spec, prims)
        result.update(prims=[p.model_dump(mode="json") for p in prims], grid=spec.grid.model_dump(),
                      layers={layer.name: {"color_rgb": list(aci2rgb(abs(layer.color))),
                                           "linetype": layer.linetype} for layer in std.layers},
                      dimstyles=dimstyles)
        if spec.scale is None:
            result["warnings"].append(f"축척이 없어 A3 1/{k}로 가정했습니다")
        # 문자열 좌표도 수치로 전개되므로 저장 전에 응답 전체의 유한성을 확인한다.
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    except SpecError as error:
        result["violations"] = [v.model_dump(mode="json") for v in error.violations]
    except (OSError, UnicodeError, ValueError) as error:
        for key in ("prims", "handles", "dimstyles"):
            result.pop(key, None)
        result.update(grid={}, layers={})
        result["violations"] = [{"code": "UI_READ", "message": f"명세 읽기 실패: {error}"}]
    return result


