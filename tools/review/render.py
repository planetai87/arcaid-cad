"""미리보기 DXF 검토 도구(CAD 없음): 영역 렌더, 기준 도면과의 추가·삭제 비교, 영역 안 객체 목록.

사용(저장소 루트):
  python tools/review/render.py render NEW.dxf -o out.png --window X0 Y0 X1 Y1 [--base BASE.dxf] [--frame X,Y,DEG]
  python tools/review/render.py diff BASE.dxf NEW.dxf [--gap 1000] [--ignore-layers TIT]
  python tools/review/render.py list NEW.dxf --window X0 Y0 X1 Y1 [--frame X,Y,DEG] [--layers WAL,DOR]
  python tools/review/render.py overlay NEW.dxf --image IMG --calib calib.json -o out.png --window U0 V0 U1 V1
                                [--frame X,Y,DEG] [--layers CON,WAL] [--compare [--tol MM] [--cell MM] [--min-len MM]]

render는 --base가 있으면 공통 객체를 회색, 새 쪽에만 있는 객체를 빨강, 기준 쪽에만 있는 객체를 파랑으로 그린다.
diff·list는 표준 출력에 UTF-8 JSON 하나를 쓴다. 단위는 모두 mm(WCS)이고, --frame을 주면 지역 좌표도 함께 낸다.
비교는 T10 도구의 형상 서명(핸들·ARCAID 태그 제외, 소수 6자리 반올림)과 개수(Counter)로 한다.

overlay는 원본 이미지(measure.py calib 보정 파일로 WCS에 맞춤)를 연하게 깔고 DXF를 빨강으로 겹친다. --frame을 주면
window는 그 지역 좌표이고 그림은 지역 좌표로 펴진다. 이미지는 출력 px 중심 → WCS → 이미지 px 아핀 하나로 옮긴다
(Pillow AFFINE은 출력 (i+.5, j+.5)에 적용하고 입력 픽셀 중심을 i+.5로 본다 = measure.py 규약).
--compare는 원본 잉크(바탕보다 0.2 이상 어두움)와 DXF 선(해치 제외)을 허용오차만큼 넓혀 서로 대조해, 원본에만 있는
잉크(파랑, 누락 후보)와 DXF에만 있는 선(빨강, 추가 후보)을 칸(--cell)으로 모아 영역 목록으로 낸다. 결과는 '불일치
후보'다: 원본 문자·해치·치수 문자는 원본에만으로 나오고, 원본 굵은 획이 DXF 선을 가릴 수 있다. 이미지 밖은 비교하지 않는다.
"""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys

import ezdxf
from ezdxf import bbox
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.backend import Backend
from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration, LinePolicy
from ezdxf.npshapes import single_paths
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.acceptance.t10_dangsan import signature  # noqa: E402

CALIB_SCHEMA = "arcaid-raster-calib/1"     # tools/raster/measure.py와 같은 값(시험이 확인)
GRAY, RED, BLUE, BLACK = (150, 150, 150, 255), (220, 0, 0, 255), (0, 70, 220, 255), (0, 0, 0, 255)
GRID_COLOR, FRAME_COLOR = (0, 150, 0, 110), (190, 0, 190, 140)
FONT_FILES = ("malgun.ttf", "C:/Windows/Fonts/malgun.ttf")
MAX_SIDE = 2400


# ---------- 좌표 ----------

class Frame:
    """지역 좌표계: 원점(WCS)과 X축 각도(도). 회전동처럼 기울어진 구역을 재는 데 쓴다"""

    def __init__(self, x: float, y: float, deg: float):
        self.x, self.y, self.deg = x, y, deg
        self.c, self.s = math.cos(math.radians(deg)), math.sin(math.radians(deg))

    @classmethod
    def parse(cls, text):
        if not text:
            return None
        x, y, deg = (float(v) for v in text.split(","))
        return cls(x, y, deg)

    def to_local(self, p):
        dx, dy = p[0] - self.x, p[1] - self.y
        return [dx * self.c + dy * self.s, -dx * self.s + dy * self.c]

    def to_wcs(self, u, v):
        return [self.x + u * self.c - v * self.s, self.y + u * self.s + v * self.c]


class View:
    """그림 창: window(frame 지역 좌표, 없으면 WCS) U0 V0 U1 V1, 배율 scale(px/mm, 기본 긴 변 2400 px).
    px는 왼쪽 위 (0, 0), 아래로 증가. render·overlay가 같이 쓴다"""

    def __init__(self, window, frame=None, scale=None):
        u0, v0, u1, v1 = (float(v) for v in window)
        if u1 <= u0 or v1 <= v0:
            raise ValueError("window는 X0 Y0 X1 Y1(X0<X1, Y0<Y1)")
        self.window, self.frame = (u0, v0, u1, v1), frame
        self.scale = scale or MAX_SIDE / max(u1 - u0, v1 - v0)
        self.size = (max(1, round((u1 - u0) * self.scale)), max(1, round((v1 - v0) * self.scale)))

    def to_px(self, p):
        u, v = self.frame.to_local(p) if self.frame else (float(p[0]), float(p[1]))
        return (u - self.window[0]) * self.scale, (self.window[3] - v) * self.scale

    def to_wcs(self, px):
        u = self.window[0] + float(px[0]) / self.scale
        v = self.window[3] - float(px[1]) / self.scale
        return self.frame.to_wcs(u, v) if self.frame else [u, v]

    def wcs_box(self, px_box=None):
        """px 상자(기본 그림 전체)의 네 꼭짓점을 WCS로 옮긴 외접 창"""
        x0, y0, x1, y1 = px_box or (0, 0, self.size[0], self.size[1])
        pts = [self.to_wcs(c) for c in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
        return [min(q[0] for q in pts), min(q[1] for q in pts), max(q[0] for q in pts), max(q[1] for q in pts)]


def _r(v, digits=1):
    return round(float(v), digits) + 0.0


def _pt(v):
    return [_r(v[0]), _r(v[1])]


# ---------- 비교 ----------

def _sig(e):
    try:
        return signature(e)
    except (ValueError, AttributeError):
        return None


def compare(base, new):
    """(공통 핸들 집합(새 쪽), 추가 핸들(새 쪽), 삭제 핸들(기준 쪽), 비교 못 한 종류 개수)"""
    unsupported = Counter()

    def index(doc):
        by_sig = defaultdict(list)
        for e in doc.modelspace():
            s = _sig(e)
            if s is None:
                unsupported[e.dxftype()] += 1
            else:
                by_sig[s].append(e.dxf.handle)
        return by_sig

    a, b = index(base), index(new)
    common, added, removed = set(), [], []
    for s in set(a) | set(b):
        ha, hb = a.get(s, []), b.get(s, [])
        n = min(len(ha), len(hb))
        common.update(hb[:n])
        added += hb[n:]
        removed += ha[n:]
    return common, added, removed, unsupported


def _box(e, cache):
    b = bbox.extents([e], fast=True, cache=cache)
    return [b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y] if b.has_data else None


def clusters(boxes, gap):
    """가까운(간격 gap 이하) 상자를 묶은 영역 목록. 큰 영역부터"""
    groups = [[list(b), 1] for b in boxes if b]
    merged = True
    while merged:
        merged = False
        out = []
        for box, n in groups:
            for g in out:
                o = g[0]
                if box[0] <= o[2] + gap and o[0] <= box[2] + gap and box[1] <= o[3] + gap and o[1] <= box[3] + gap:
                    g[0] = [min(o[0], box[0]), min(o[1], box[1]), max(o[2], box[2]), max(o[3], box[3])]
                    g[1] += n
                    merged = True
                    break
            else:
                out.append([box, n])
        groups = out
    groups.sort(key=lambda g: -(g[0][2] - g[0][0]) * (g[0][3] - g[0][1]))
    return [{"window": [_r(v, 0) for v in g[0]], "count": g[1]} for g in groups]


def diff(base_path, new_path, gap=1000.0, ignore_layers=("TIT",)):
    base, new = ezdxf.readfile(base_path), ezdxf.readfile(new_path)
    common, added, removed, unsupported = compare(base, new)
    ignore = {name.upper() for name in ignore_layers}
    out = {"base": str(base_path), "new": str(new_path), "common": len(common), "added": len(added),
           "removed": len(removed), "unsupported": dict(unsupported)}
    for key, doc, handles in (("added", new, added), ("removed", base, removed)):
        cache = bbox.Cache()
        ents = [doc.entitydb[h] for h in handles]
        out[f"{key}_by_layer"] = dict(Counter(e.dxf.layer for e in ents).most_common())
        boxes = [_box(e, cache) for e in ents if e.dxf.layer.upper() not in ignore]
        out[f"{key}_regions"] = clusters(boxes, gap)
    return out


# ---------- 목록 ----------

def _tags(e):
    if not e.has_xdata("ARCAID"):
        return {}
    tags = {}
    for code, value in e.get_xdata("ARCAID"):
        if code == 1000 and "=" in str(value):
            k, v = str(value).split("=", 1)
            tags[k] = v
    return {k: tags[k] for k in ("op", "oid", "path") if k in tags}


def describe(e):
    """객체 하나의 위치 정보(WCS). 꼭짓점은 빠짐없이 낸다"""
    t = e.dxftype()
    d = {"handle": e.dxf.handle, "type": t, "layer": e.dxf.layer, **_tags(e)}
    if t == "LINE":
        d["points"] = [_pt(e.dxf.start), _pt(e.dxf.end)]
    elif t == "LWPOLYLINE":
        ocs = e.ocs()
        d["points"] = [_pt(ocs.to_wcs((x, y, e.dxf.elevation))) for x, y in e.get_points("xy")]
        bulges = [_r(b, 4) for *_, b in e.get_points("xyb")]
        if any(bulges):
            d["bulges"] = bulges
        d["closed"] = bool(e.closed)
    elif t in ("ARC", "CIRCLE"):
        d["center"], d["radius"] = _pt(e.ocs().to_wcs(e.dxf.center)), _r(e.dxf.radius)
        if t == "ARC":
            d["angles"] = [_r(e.dxf.start_angle, 2), _r(e.dxf.end_angle, 2)]
            if e.dxf.extrusion.z < 0:
                d["mirrored_ocs"] = True
    elif t == "TEXT":
        d.update(text=e.dxf.text, insert=_pt(e.dxf.insert), height=_r(e.dxf.height),
                 rotation=_r(e.dxf.get("rotation", 0), 2), halign=e.dxf.get("halign", 0), valign=e.dxf.get("valign", 0))
        if e.dxf.get("halign", 0) or e.dxf.get("valign", 0):
            d["align_point"] = _pt(e.dxf.align_point)
    elif t == "MTEXT":
        d.update(text=e.plain_text(), insert=_pt(e.dxf.insert), height=_r(e.dxf.char_height))
    elif t == "INSERT":
        d.update(name=e.dxf.name, insert=_pt(e.dxf.insert), rotation=_r(e.dxf.get("rotation", 0), 2),
                 scale=[_r(e.dxf.get("xscale", 1), 3), _r(e.dxf.get("yscale", 1), 3)])
        if e.attribs:
            d["attribs"] = {a.dxf.tag: a.dxf.text for a in e.attribs}
    elif t == "HATCH":
        d["pattern"] = e.dxf.pattern_name
        d["loops"] = [[_pt(v) for v in p.vertices] if hasattr(p, "vertices") else "edge-path" for p in e.paths]
    elif t == "DIMENSION":
        d.update(p1=_pt(e.dxf.defpoint2), p2=_pt(e.dxf.defpoint3), line=_pt(e.dxf.defpoint),
                 measurement=_r(e.get_measurement()) if hasattr(e, "get_measurement") else None)
    return d


def _localize(value, frame):
    """describe 결과의 좌표 필드를 지역 좌표로 바꾼 사본"""
    keys = ("points", "center", "insert", "align_point", "p1", "p2", "line")
    out = {}
    for k in keys:
        if k in value:
            v = value[k]
            out[k] = [_pt(frame.to_local(p)) for p in v] if k == "points" else _pt(frame.to_local(v))
    if "loops" in value:
        out["loops"] = [[_pt(frame.to_local(p)) for p in loop] if isinstance(loop, list) else loop
                        for loop in value["loops"]]
    return out


def _hits(box, window):
    return box and box[0] <= window[2] and window[0] <= box[2] and box[1] <= window[3] and window[1] <= box[3]


def list_entities(path, window, frame=None, layers=None):
    doc = ezdxf.readfile(path)
    wanted = {name.upper() for name in layers} if layers else None
    cache = bbox.Cache()
    items = []
    for e in doc.modelspace():
        if wanted and e.dxf.layer.upper() not in wanted:
            continue
        if not _hits(_box(e, cache), window):
            continue
        d = describe(e)
        if frame:
            d["local"] = _localize(d, frame)
        items.append(d)
    out = {"dxf": str(path), "window": window, "count": len(items), "entities": items}
    if frame:
        out["frame"] = {"origin": [frame.x, frame.y], "deg": frame.deg}
    return out


# ---------- 렌더 ----------

class PilBackend(Backend):
    """창(WCS mm) → 그림 픽셀. 선 색은 패스마다 정한다(DXF 색은 쓰지 않음)"""

    def __init__(self, canvas, view):
        super().__init__()
        self.canvas, self.view, self.scale = canvas, view, view.scale
        self.draw = ImageDraw.Draw(canvas)
        self.color = BLACK

    def xy(self, p):
        return self.view.to_px(p)

    def set_background(self, color):
        pass

    def draw_point(self, pos, properties):
        self.draw.point(self.xy(pos), fill=self.color)

    def draw_line(self, start, end, properties):
        self.draw.line([self.xy(start), self.xy(end)], fill=self.color, width=1)

    def draw_path(self, path, properties):
        for p in single_paths([path]):
            pts = [self.xy(v) for v in p.flattening(max(0.5 / self.scale, 1))]
            if len(pts) > 1:
                self.draw.line(pts, fill=self.color, width=1)

    def _fill(self, pts):
        if len(pts) >= 3:
            layer = Image.new("RGBA", self.canvas.size, (0, 0, 0, 0))
            ImageDraw.Draw(layer).polygon(pts, fill=self.color[:3] + (60,))
            self.canvas.alpha_composite(layer)

    def draw_filled_paths(self, paths, properties):
        for p in single_paths(paths):
            self._fill([self.xy(v) for v in p.flattening(max(0.5 / self.scale, 1))])

    def draw_filled_polygon(self, points, properties):
        self._fill([self.xy(v) for v in points.vertices()])

    def draw_image(self, image_data, properties):
        pass

    def clear(self):
        pass


def _font(size):
    for name in FONT_FILES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _korean_fonts(doc):
    """미리보기용으로만 문자 스타일 글꼴을 맑은 고딕으로 바꾼다(원본 파일은 바꾸지 않음)"""
    if not any(Path(f).exists() for f in FONT_FILES[1:]):
        return
    for style in doc.styles:
        style.dxf.font = "malgun.ttf"


def _grid_values(lo, hi, step):
    return [k * step for k in range(math.floor(lo / step), math.ceil(hi / step) + 1)]


def _draw_grid(canvas, window, scale, step, frame, names=("x", "y")):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw, font = ImageDraw.Draw(layer), _font(14)
    to_px = lambda p: ((p[0] - window[0]) * scale, (window[3] - p[1]) * scale)  # noqa: E731
    w, h = canvas.size
    for x in _grid_values(window[0], window[2], step):
        draw.line([to_px((x, window[1])), to_px((x, window[3]))], fill=GRID_COLOR)
        draw.text((to_px((x, window[3]))[0] + 2, 2), f"{names[0]} {x:g}", fill=GRID_COLOR[:3] + (255,), font=font)
    for y in _grid_values(window[1], window[3], step):
        draw.line([to_px((window[0], y)), to_px((window[2], y))], fill=GRID_COLOR)
        draw.text((2, to_px((window[0], y))[1] + 2), f"{names[1]} {y:g}", fill=GRID_COLOR[:3] + (255,), font=font)
    if frame:
        corners = [frame.to_local(c) for c in ((window[0], window[1]), (window[2], window[1]),
                                               (window[0], window[3]), (window[2], window[3]))]
        umin, umax = min(c[0] for c in corners), max(c[0] for c in corners)
        vmin, vmax = min(c[1] for c in corners), max(c[1] for c in corners)
        color = FRAME_COLOR[:3] + (255,)
        for u in _grid_values(umin, umax, step):
            a, b = to_px(frame.to_wcs(u, vmin)), to_px(frame.to_wcs(u, vmax))
            draw.line([a, b], fill=FRAME_COLOR)
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            draw.text((min(max(mid[0], 0), w - 60), min(max(mid[1], 0), h - 16)), f"u {u:g}", fill=color, font=font)
        for v in _grid_values(vmin, vmax, step):
            a, b = to_px(frame.to_wcs(umin, v)), to_px(frame.to_wcs(umax, v))
            draw.line([a, b], fill=FRAME_COLOR)
            mid = ((a[0] + b[0]) / 2 + 30, (a[1] + b[1]) / 2)
            draw.text((min(max(mid[0], 0), w - 60), min(max(mid[1], 0), h - 16)), f"v {v:g}", fill=color, font=font)
    canvas.alpha_composite(layer)


def render(new_path, out_path, window, base_path=None, scale=None, grid=1000.0, frame=None, hide_layers=("TIT",)):
    x0, y0, x1, y1 = window
    view = View(window, scale=scale)
    scale, size = view.scale, view.size
    canvas = Image.new("RGBA", size, (255, 255, 255, 255))
    if grid:
        _draw_grid(canvas, window, scale, grid, frame)
    hide = {name.upper() for name in hide_layers}
    new = ezdxf.readfile(new_path)
    _korean_fonts(new)
    cfg = Configuration(color_policy=ColorPolicy.BLACK, background_policy=BackgroundPolicy.WHITE,
                        line_policy=LinePolicy.ACCURATE, max_flattening_distance=max(0.5 / scale, 1))
    passes = []
    counts = {}
    if base_path:
        base = ezdxf.readfile(base_path)
        _korean_fonts(base)
        common, added, removed, _ = compare(base, new)
        passes = [(new, common, GRAY), (base, set(removed), BLUE), (new, set(added), RED)]
        counts = {"common": len(common), "added": len(added), "removed": len(removed)}
    else:
        passes = [(new, None, BLACK)]
    cache = bbox.Cache()
    for doc, handles, color in passes:
        backend = PilBackend(canvas, view)
        backend.color = color

        def visible(e, handles=handles):
            if e.dxf.layer.upper() in hide or (handles is not None and e.dxf.handle not in handles):
                return False
            return bool(_hits(_box(e, cache), window))
        Frontend(RenderContext(doc), backend, cfg).draw_layout(doc.modelspace(), filter_func=visible)
    draw, font = ImageDraw.Draw(canvas), _font(16)
    legend = f"window {x0:g},{y0:g} – {x1:g},{y1:g} mm  |  {1 / scale:.1f} mm/px  |  grid {grid:g}"
    if base_path:
        legend += "  |  회색 공통 · 빨강 추가 · 파랑 삭제"
    if frame:
        legend += f"  |  frame ({frame.x:g},{frame.y:g}) {frame.deg:g}°"
    draw.rectangle([0, size[1] - 22, size[0], size[1]], fill=(255, 255, 255, 230))
    draw.text((4, size[1] - 20), legend, fill=BLACK, font=font)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_path)
    return {"png": str(out_path), "size": list(size), "mm_per_px": _r(1 / scale, 3), **counts}


# ---------- 원본 겹침 ----------

class ImageCalib:
    """measure.py calib 보정 파일: WCS(mm, y 위) → 이미지 px(v 아래, 픽셀 (i, j) 중심 (i+.5, j+.5)).
    닮음 변환에, 사진 보정(warp)이면 px → WCS 다항식 보정량을 더한다(measure.py Calib와 같은 식, numpy 없이)"""

    def __init__(self, data):
        self.s, self.u0, self.v0 = float(data["px_per_mm"]), *(float(v) for v in data["origin_px"])
        self.err_mm = float(data.get("err_mm", 0.0))
        b = math.radians(float(data["beta_deg"]))
        self.c, self.n = math.cos(b), math.sin(b)
        self.warp = data.get("warp")

    def _sim_px(self, x, y):
        return self.u0 + self.s * (self.c * x - self.n * y), self.v0 - self.s * (self.n * x + self.c * y)

    def to_wcs(self, u, v):
        a, b = (u - self.u0) / self.s, -(v - self.v0) / self.s
        x, y = self.c * a + self.n * b, -self.n * a + self.c * b
        if self.warp:
            w = self.warp
            un, vn = (u - w["center"][0]) / w["scale"], (v - w["center"][1]) / w["scale"]
            basis = [un ** i * vn ** j for i, j in w["terms"]]
            x += sum(c * t for c, t in zip(w["qx"], basis))
            y += sum(c * t for c, t in zip(w["qy"], basis))
        return x, y

    def to_px(self, x, y):
        u, v = self._sim_px(x, y)
        if not self.warp:
            return u, v
        step, best = 1.0, float("inf")
        for _ in range(30):                                  # measure.py Calib.to_px와 같은 감쇠 고정점 반복
            wx, wy = self.to_wcs(u, v)
            ex, ey = x - wx, y - wy
            e = math.hypot(ex, ey) * self.s
            step = step / 2 if e > best else min(step * 1.5, 1.0)
            best = min(best, e)
            if e < 1e-6:
                break
            u, v = u + step * self.s * (self.c * ex - self.n * ey), v - step * self.s * (self.n * ex + self.c * ey)
        return u, v


def _sha(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_calib(calib_path, image_path):
    data = json.loads(Path(calib_path).read_text(encoding="utf-8"))
    if data.get("schema") != CALIB_SCHEMA:
        raise ValueError(f"보정 파일 형식이 아님({data.get('schema')})")
    if data.get("status") != "ok":
        raise ValueError("거부된 보정 파일")
    if data.get("sha256") != _sha(image_path):
        raise ValueError("보정 파일의 이미지 sha256이 이 이미지와 다름")
    return ImageCalib(data)


def _gray(path):
    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg
    return im.convert("L")


MESH_CELL_PX = 16


def warp(img, view, calib, fill=255):
    """이미지를 그림 창으로 옮긴다. 닮음 보정이면 출력 px 연속 좌표 → WCS → 이미지 px가 아핀이라 세 점으로 계수를 정한다.
    사진 보정(warp)이면 Image.MESH: 출력 MESH_CELL_PX 칸마다 칸 경계 네 모서리(좌상·좌하·우하·우상)를 이미지 사변형에 대응"""
    if getattr(calib, "warp", None):
        w, h = view.size
        cache = {}

        def src(x, y):
            if (x, y) not in cache:
                cache[(x, y)] = calib.to_px(*view.to_wcs((float(x), float(y))))
            return cache[(x, y)]
        mesh = []
        for y0 in range(0, h, MESH_CELL_PX):
            y1 = min(h, y0 + MESH_CELL_PX)
            for x0 in range(0, w, MESH_CELL_PX):
                x1 = min(w, x0 + MESH_CELL_PX)
                quad = [c for corner in ((x0, y0), (x0, y1), (x1, y1), (x1, y0)) for c in src(*corner)]
                mesh.append(((x0, y0, x1, y1), tuple(quad)))
        return img.transform(view.size, Image.MESH, mesh, resample=Image.BILINEAR, fillcolor=fill)
    o = calib.to_px(*view.to_wcs((0.0, 0.0)))
    ex = calib.to_px(*view.to_wcs((1.0, 0.0)))
    ey = calib.to_px(*view.to_wcs((0.0, 1.0)))
    coeffs = (ex[0] - o[0], ey[0] - o[0], o[0], ex[1] - o[1], ey[1] - o[1], o[1])
    return img.transform(view.size, Image.AFFINE, coeffs, resample=Image.BILINEAR, fillcolor=fill)


def _valid(image_size, view, calib):
    """이미지 안(픽셀 중심 범위, 가장자리 1 px 제외)인 그림 px = 255"""
    w, h = image_size
    mask = Image.new("L", (w, h), 0)
    if w > 2 and h > 2:
        mask.paste(255, (1, 1, w - 1, h - 1))
    return warp(mask, view, calib, fill=0).point(lambda v: 255 if v == 255 else 0)


def _draw_dxf(canvas, doc, view, color, layers, hide, hatches=True):
    cfg = Configuration(color_policy=ColorPolicy.BLACK, background_policy=BackgroundPolicy.WHITE,
                        line_policy=LinePolicy.ACCURATE, max_flattening_distance=max(0.5 / view.scale, 1))
    backend = PilBackend(canvas, view)
    backend.color = color
    box, cache = view.wcs_box(), bbox.Cache()

    def visible(e):
        layer = e.dxf.layer.upper()
        if layer in hide or (layers and layer not in layers) or (not hatches and e.dxftype() == "HATCH"):
            return False
        return bool(_hits(_box(e, cache), box))
    Frontend(RenderContext(doc), backend, cfg).draw_layout(doc.modelspace(), filter_func=visible)


def _dilate(mask, r):
    for _ in range(r):
        mask = mask.filter(ImageFilter.MaxFilter(3))
    return mask


def _count(mask):
    return mask.histogram()[255]


def regions(mask, view, cell, min_len):
    """어긋난 px(255)을 칸으로 세어 잡음 바닥(칸 3 px)을 넘는 칸을 8방향으로 묶고, 묶음 합계가 min_len 길이
    선(배율 × min_len px)보다 큰 것만 영역으로 낸다(큰 것부터)"""
    w, h = mask.size
    c = max(cell * view.scale, 4.0)
    cols, rows = math.ceil(w / c), math.ceil(h / c)
    cells = {}
    for i in range(cols):
        for j in range(rows):
            box = (int(i * c), int(j * c), min(w, int((i + 1) * c)), min(h, int((j + 1) * c)))
            crop = mask.crop(box)
            n = _count(crop)
            if n >= 3:
                b = crop.getbbox()
                cells[(i, j)] = (n, (box[0] + b[0], box[1] + b[1], box[0] + b[2], box[1] + b[3]))
    seen, out = set(), []
    for start in sorted(cells):
        if start in seen:
            continue
        stack, group = [start], []
        seen.add(start)
        while stack:
            i, j = stack.pop()
            group.append(cells[(i, j)])
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    k = (i + di, j + dj)
                    if k in cells and k not in seen:
                        seen.add(k)
                        stack.append(k)
        total = sum(n for n, _ in group)
        if total < min_len * view.scale:
            continue
        x0, y0 = min(b[0] for _, b in group), min(b[1] for _, b in group)
        x1, y1 = max(b[2] for _, b in group), max(b[3] for _, b in group)
        u0, v0, u1, v1 = view.window
        local = [u0 + x0 / view.scale, v1 - y1 / view.scale, u0 + x1 / view.scale, v1 - y0 / view.scale]
        region = {"window": [_r(v, 0) for v in view.wcs_box((x0, y0, x1, y1))], "px": total}
        if view.frame:
            region["window_local"] = [_r(v, 0) for v in local]
        out.append(region)
    return sorted(out, key=lambda r: -r["px"])


def overlay(dxf_path, image_path, calib_path, out_path, window, frame=None, scale=None, grid=1000.0, layers=None,
            hide_layers=("TIT",), fade=0.35, compare=False, tol=None, cell=500.0, min_len=300.0):
    view = View(window, frame, scale)
    calib = load_calib(calib_path, image_path)
    gray = _gray(image_path)
    bg = warp(gray, view, calib, fill=255)
    valid = _valid(gray.size, view, calib)
    n_valid, total = _count(valid), view.size[0] * view.size[1]
    if n_valid == 0:
        raise ValueError("창 전체가 이미지 밖")
    canvas = bg.point(lambda v: 255 - round((255 - v) * fade)).convert("RGBA")
    if grid:
        _draw_grid(canvas, view.window, view.scale, grid, None, ("u", "v") if frame else ("x", "y"))
    doc = ezdxf.readfile(dxf_path)
    _korean_fonts(doc)
    layers = {n.upper() for n in layers} if layers else None
    hide = {n.upper() for n in hide_layers}
    result = {"png": str(out_path), "size": list(view.size), "mm_per_px": _r(1 / view.scale, 3),
              "outside_pct": _r(100.0 * (1 - n_valid / total), 1)}
    if compare:
        tol = calib.err_mm + 2.0 / calib.s if tol is None else float(tol)
        r = max(1, math.ceil(tol * view.scale))
        drawn = Image.new("RGBA", view.size, (255, 255, 255, 255))
        _draw_dxf(drawn, doc, view, BLACK, layers, hide, hatches=False)
        dxf = ImageChops.multiply(drawn.convert("L").point(lambda v: 255 if v < 250 else 0), valid)
        hist = [n for n in ImageChops.multiply(bg, valid).histogram()]
        hist[0] -= total - n_valid            # 이미지 밖(0으로 채운 곳)은 바탕 계산에서 뺀다
        acc, paper = 0, 255
        for level in range(255, -1, -1):      # 밝은 쪽 25% = 어두움 25 백분위
            acc += hist[level]
            if acc >= 0.25 * n_valid:
                paper = level
                break
        ink = ImageChops.multiply(bg.point(lambda v: 255 if v <= paper - 51 else 0), valid)
        only_image = ImageChops.multiply(ink, ImageChops.invert(_dilate(dxf, r)))
        only_dxf = ImageChops.multiply(dxf, ImageChops.invert(_dilate(ink, r)))
        canvas.paste(GRAY, mask=dxf)
        canvas.paste(RED, mask=only_dxf)
        canvas.paste(BLUE, mask=only_image)
        result.update({"tol_mm": _r(tol, 2), "tol_px": r, "effective_tol_mm": _r(r / view.scale, 1),
                       "effective_tol_diag_mm": _r(r * math.sqrt(2) / view.scale, 1),
                       "counts": {"image_px": _count(ink), "dxf_px": _count(dxf), "only_image_px": _count(only_image),
                                  "only_dxf_px": _count(only_dxf)},
                       "only_image": regions(only_image, view, cell, min_len),
                       "only_dxf": regions(only_dxf, view, cell, min_len)})
    else:
        _draw_dxf(canvas, doc, view, RED, layers, hide)
    draw, font = ImageDraw.Draw(canvas), _font(16)
    w = view.window
    legend = f"window {w[0]:g},{w[1]:g} – {w[2]:g},{w[3]:g} mm  |  {1 / view.scale:.1f} mm/px"
    if frame:
        legend += f"  |  frame ({frame.x:g},{frame.y:g}) {frame.deg:g}° 지역 좌표"
    legend += "  |  회색 일치 · 빨강 DXF에만 · 파랑 원본에만" if compare else "  |  빨강 DXF · 회색 원본"
    draw.rectangle([0, view.size[1] - 22, view.size[0], view.size[1]], fill=(255, 255, 255, 230))
    draw.text((4, view.size[1] - 20), legend, fill=BLACK, font=font)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_path)
    return result


# ---------- CLI ----------

def main(argv=None):
    parser = argparse.ArgumentParser(description="미리보기 DXF 검토 도구(렌더·비교·목록)")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("render", help="창 영역을 PNG로")
    r.add_argument("dxf")
    r.add_argument("-o", "--out", required=True)
    r.add_argument("--window", nargs=4, type=float, required=True, metavar=("X0", "Y0", "X1", "Y1"))
    r.add_argument("--base", help="기준 DXF(주면 공통 회색·추가 빨강·삭제 파랑)")
    r.add_argument("--scale", type=float, help="px/mm(기본: 긴 변 2400 px)")
    r.add_argument("--grid", type=float, default=1000.0, help="눈금 간격 mm(0이면 없음)")
    r.add_argument("--frame", help="지역 좌표 눈금: X,Y,DEG")
    r.add_argument("--hide-layers", default="TIT")
    d = sub.add_parser("diff", help="기준 대비 추가·삭제 개수와 영역")
    d.add_argument("base")
    d.add_argument("dxf")
    d.add_argument("--gap", type=float, default=1000.0, help="영역으로 묶는 간격 mm")
    d.add_argument("--ignore-layers", default="TIT", help="영역 계산에서 뺄 레이어(쉼표)")
    lst = sub.add_parser("list", help="창 안 객체의 좌표(JSON)")
    lst.add_argument("dxf")
    lst.add_argument("--window", nargs=4, type=float, required=True, metavar=("X0", "Y0", "X1", "Y1"))
    lst.add_argument("--frame", help="지역 좌표도 함께: X,Y,DEG")
    lst.add_argument("--layers", help="이 레이어만(쉼표)")
    o = sub.add_parser("overlay", help="원본 이미지에 DXF를 겹친 PNG(--compare면 어긋난 영역 목록)")
    o.add_argument("dxf")
    o.add_argument("--image", required=True)
    o.add_argument("--calib", required=True, help="measure.py calib 보정 파일")
    o.add_argument("-o", "--out", required=True)
    o.add_argument("--window", nargs=4, type=float, required=True, metavar=("U0", "V0", "U1", "V1"),
                   help="--frame이 있으면 지역 좌표")
    o.add_argument("--frame", help="지역 좌표로 펴 보기: X,Y,DEG")
    o.add_argument("--scale", type=float, help="px/mm(기본: 긴 변 2400 px)")
    o.add_argument("--grid", type=float, default=1000.0)
    o.add_argument("--layers", help="이 레이어만(쉼표)")
    o.add_argument("--hide-layers", default="TIT")
    o.add_argument("--fade", type=float, default=0.35, help="원본 진하기(0~1)")
    o.add_argument("--compare", action="store_true", help="원본에만·DXF에만 있는 곳을 찾는다")
    o.add_argument("--tol", type=float, help="허용오차 mm(기본: 보정 오차 + 이미지 2 px)")
    o.add_argument("--cell", type=float, default=500.0, help="집계 칸 mm")
    o.add_argument("--min-len", type=float, default=300.0, help="이보다 짧은 선 길이만큼의 어긋남은 내지 않음(mm)")
    args = parser.parse_args(argv)
    split = lambda s: [v.strip() for v in s.split(",") if v.strip()] if s else []  # noqa: E731
    if args.command == "render":
        result = render(args.dxf, args.out, args.window, args.base, args.scale, args.grid,
                        Frame.parse(args.frame), split(args.hide_layers))
    elif args.command == "diff":
        result = diff(args.base, args.dxf, args.gap, split(args.ignore_layers))
    elif args.command == "overlay":
        try:
            result = overlay(args.dxf, args.image, args.calib, args.out, args.window, Frame.parse(args.frame),
                             args.scale, args.grid, split(args.layers), split(args.hide_layers), args.fade,
                             args.compare, args.tol, args.cell, args.min_len)
        except (ValueError, OSError) as e:
            result = {"status": "error", "message": str(e)}
    else:
        result = list_entities(args.dxf, args.window, Frame.parse(args.frame), split(args.layers))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("status") == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
