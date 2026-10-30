"""미리보기 DXF 검토 도구(CAD 없음): 영역 렌더, 기준 도면과의 추가·삭제 비교, 영역 안 객체 목록.

사용(저장소 루트):
  python tools/review/render.py render NEW.dxf -o out.png --window X0 Y0 X1 Y1 [--base BASE.dxf] [--frame X,Y,DEG]
  python tools/review/render.py diff BASE.dxf NEW.dxf [--gap 1000] [--ignore-layers TIT]
  python tools/review/render.py list NEW.dxf --window X0 Y0 X1 Y1 [--frame X,Y,DEG] [--layers WAL,DOR]

render는 --base가 있으면 공통 객체를 회색, 새 쪽에만 있는 객체를 빨강, 기준 쪽에만 있는 객체를 파랑으로 그린다.
diff·list는 표준 출력에 UTF-8 JSON 하나를 쓴다. 단위는 모두 mm(WCS)이고, --frame을 주면 지역 좌표도 함께 낸다.
비교는 T10 도구의 형상 서명(핸들·ARCAID 태그 제외, 소수 6자리 반올림)과 개수(Counter)로 한다.
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
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.acceptance.t10_dangsan import signature  # noqa: E402

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

    def __init__(self, canvas, window, scale):
        super().__init__()
        self.canvas, self.window, self.scale = canvas, window, scale
        self.draw = ImageDraw.Draw(canvas)
        self.color = BLACK

    def xy(self, p):
        return ((float(p[0]) - self.window[0]) * self.scale, (self.window[3] - float(p[1])) * self.scale)

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


def _draw_grid(canvas, window, scale, step, frame):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw, font = ImageDraw.Draw(layer), _font(14)
    to_px = lambda p: ((p[0] - window[0]) * scale, (window[3] - p[1]) * scale)  # noqa: E731
    w, h = canvas.size
    for x in _grid_values(window[0], window[2], step):
        draw.line([to_px((x, window[1])), to_px((x, window[3]))], fill=GRID_COLOR)
        draw.text((to_px((x, window[3]))[0] + 2, 2), f"x {x:g}", fill=GRID_COLOR[:3] + (255,), font=font)
    for y in _grid_values(window[1], window[3], step):
        draw.line([to_px((window[0], y)), to_px((window[2], y))], fill=GRID_COLOR)
        draw.text((2, to_px((window[0], y))[1] + 2), f"y {y:g}", fill=GRID_COLOR[:3] + (255,), font=font)
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
    if x1 <= x0 or y1 <= y0:
        raise ValueError("window는 X0 Y0 X1 Y1(X0<X1, Y0<Y1)")
    scale = scale or MAX_SIDE / max(x1 - x0, y1 - y0)
    size = (max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale)))
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
        backend = PilBackend(canvas, window, scale)
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
    args = parser.parse_args(argv)
    split = lambda s: [v.strip() for v in s.split(",") if v.strip()] if s else []  # noqa: E731
    if args.command == "render":
        result = render(args.dxf, args.out, args.window, args.base, args.scale, args.grid,
                        Frame.parse(args.frame), split(args.hide_layers))
    elif args.command == "diff":
        result = diff(args.base, args.dxf, args.gap, split(args.ignore_layers))
    else:
        result = list_entities(args.dxf, args.window, Frame.parse(args.frame), split(args.layers))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
