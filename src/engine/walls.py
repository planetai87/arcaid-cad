"""직교 벽 조각 합치기(표준 라이브러리만 사용).

같은 레이어를 가장 긴 변의 각도 mod 90도로 묶는다. 방향 허용오차는 1e-6도이며
0/90도 경계도 같은 방향이다. 역회전한 좌표는 정렬한 묶음의 최솟값에서 1e-3 mm
이내인 값끼리 그 최솟값으로 맞춘다(연쇄 맞춤은 하지 않는다). 압축 격자는 bytearray이고
묶음당 4,000,000칸까지다. 칸 중심의 다각형 내부 판정을 행별 교차 구간으로 계산하며,
모든 조각을 칠한 뒤 개구부를 지운다. 개구부에 대응하는 레이어·방향의 조각이 없으면 거부한다.

경계는 내부가 왼쪽인 유향 변을 잇고 대각 접촉에서는 좌회전을 우선해 영역을 나눈다.
일직선 중간 점을 지운 뒤 면적 부호로 바깥(반시계)·섬(시계)을 구별한다. 섬은 자신을
포함하는 바깥 중 면적이 가장 작은 고리에 배정한다. 입력 WCS는 중간 반올림 없이 유지하고,
복귀한 최종 고리만 호출자가 _r로 소수 여섯 자리 반올림한다. 영역에는 기여한 조각을 돌려줘
호출자가 path·해치 각도를 결정하게 한다. 다른 방향 영역의 WCS bbox가 닿거나 겹치면 경고한다.
"""
import math
from dataclasses import dataclass
from typing import List, Optional

ANGLE_TOL = 1e-6
SNAP_TOL = 1e-3
MAX_CELLS = 4_000_000


class WallError(Exception):
    def __init__(self, code, path, message):
        self.code, self.path = code, path
        super().__init__(message)


@dataclass
class Fragment:
    layer: str
    polygon: list
    opening: bool
    path: str
    pattern_angle: float = 0.0
    source: Optional[dict] = None  # 호출자의 원본 참조를 그대로 전달하며 합치기에는 쓰지 않는다.


@dataclass
class Region:
    layer: str
    outer: list
    holes: list
    parts: List[Fragment]
    angle: float


def _edges(loop):
    return zip(loop, loop[1:] + loop[:1])


def _area(loop):
    # 큰 WCS 원점 때문에 면적의 유효 숫자가 줄어들지 않도록 평행 이동한다.
    ox, oy = loop[0]
    return sum((x1 - ox) * (y2 - oy) - (x2 - ox) * (y1 - oy)
               for (x1, y1), (x2, y2) in _edges(loop)) / 2


def _angle(loop):
    a, b = max(_edges(loop), key=lambda edge: math.dist(*edge))
    angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 90
    return 0.0 if min(angle, 90 - angle) <= ANGLE_TOL else angle


def _same_angle(a, b):
    diff = abs(a - b)
    return min(diff, 90 - diff) <= ANGLE_TOL


def _rotate(loop, angle):
    cos, sin = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return [(x * cos - y * sin, x * sin + y * cos) for x, y in loop]


def _snap(values):
    result = {}
    for value in sorted(set(values)):
        if not result or value - anchor > SNAP_TOL:
            anchor = value
        result[value] = anchor
    coords = sorted(set(result.values()))
    indices = {value: i for i, value in enumerate(coords)}
    return coords, {value: indices[snapped] for value, snapped in result.items()}


def valid_polygon(loop):
    """면적이 있는 단순 직교 다각형인지 확인한다. 끝에 반복한 첫 점은 허용한다."""
    if len(loop) > 1 and loop[0] == loop[-1]:
        loop = loop[:-1]
    if len(loop) < 4 or any(a == b for a, b in _edges(loop)) or not _area(loop):
        return False
    angle = _angle(loop)
    for (x1, y1), (x2, y2) in _edges(loop):
        if not _same_angle(math.degrees(math.atan2(y2 - y1, x2 - x1)) % 90, angle):
            return False
    local = _rotate(loop, -angle)
    _, xs = _snap([p[0] for p in local])
    _, ys = _snap([p[1] for p in local])
    points = [(xs[x], ys[y]) for x, y in local]
    edges = list(_edges(points))
    for i, (a, b) in enumerate(edges):
        for j in range(i + 1, len(edges)):
            c, d = edges[j]
            if j == i + 1 or (i == 0 and j == len(edges) - 1):
                continue
            if (max(min(a[0], b[0]), min(c[0], d[0])) <= min(max(a[0], b[0]), max(c[0], d[0])) and
                    max(min(a[1], b[1]), min(c[1], d[1])) <= min(max(a[1], b[1]), max(c[1], d[1]))):
                return False
    return True


def _spans(loop, row):
    crosses = sorted(a[0] for a, b in _edges(loop) if min(a[1], b[1]) <= row < max(a[1], b[1]))
    return list(zip(crosses[::2], crosses[1::2]))


def _inside(point, loop):
    x, y = point
    return sum(1 for (x1, y1), (x2, y2) in _edges(loop)
               if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1)) % 2 == 1


def _simplify(loop):
    return [b for a, b, c in zip(loop[-1:] + loop[:-1], loop, loop[1:] + loop[:1])
            if (b[0] - a[0]) * (c[1] - b[1]) != (b[1] - a[1]) * (c[0] - b[0])]


def _trace(grid, nx, ny):
    edges = {}

    def add(a, b):
        edges.setdefault(a, set()).add(b)

    for y in range(ny):
        for x in range(nx):
            i = y * nx + x
            if not grid[i]:
                continue
            if y == 0 or not grid[i - nx]:
                add((x, y), (x + 1, y))
            if x == nx - 1 or not grid[i + 1]:
                add((x + 1, y), (x + 1, y + 1))
            if y == ny - 1 or not grid[i + nx]:
                add((x + 1, y + 1), (x, y + 1))
            if x == 0 or not grid[i - 1]:
                add((x, y + 1), (x, y))
    loops = []
    while edges:
        start = next(iter(edges))
        prev, current = start, min(edges[start])
        loop = [start]
        edges[start].remove(current)
        if not edges[start]:
            del edges[start]
        while current != start:
            loop.append(current)
            dx, dy = current[0] - prev[0], current[1] - prev[1]

            def turn(point):
                vx, vy = point[0] - current[0], point[1] - current[1]
                cross, dot = dx * vy - dy * vx, dx * vx + dy * vy
                return 0 if cross > 0 else 1 if dot > 0 else 2 if cross < 0 else 3

            target = min(edges[current], key=turn)
            edges[current].remove(target)
            if not edges[current]:
                del edges[current]
            prev, current = current, target
        loops.append(_simplify(loop))
    return loops


def _contributes(part, loops):
    low = max(min(y for x, y in part), min(y for x, y in loops[0]))
    high = min(max(y for x, y in part), max(y for x, y in loops[0]))
    for row in range(low, high):
        cuts = sorted(x for loop in loops for span in _spans(loop, row) for x in span)
        for a, b in _spans(part, row):
            if any(max(a, c) < min(b, d) for c, d in zip(cuts[::2], cuts[1::2])):
                return True
    return False


def _merge_group(layer, angle, fragments):
    local = [_rotate(f.polygon[:-1] if f.polygon[0] == f.polygon[-1] else f.polygon, -angle)
             for f in fragments]
    xs, xi = _snap([x for loop in local for x, y in loop])
    ys, yi = _snap([y for loop in local for x, y in loop])
    nx, ny = len(xs) - 1, len(ys) - 1
    if nx * ny > MAX_CELLS:
        raise WallError("SPEC_LIMIT", fragments[0].path,
                        f"벽 격자가 {nx * ny}칸(상한 {MAX_CELLS}), 벽 op를 나눌 것")
    grid = bytearray(nx * ny)
    polygons = [[(xi[x], yi[y]) for x, y in loop] for loop in local]
    for opening in (False, True):
        for f, loop in zip(fragments, polygons):
            if f.opening != opening:
                continue
            for row in range(min(y for x, y in loop), max(y for x, y in loop)):
                for left, right in _spans(loop, row):
                    grid[row * nx + left:row * nx + right] = bytes([0 if opening else 1]) * (right - left)
    loops = _trace(grid, nx, ny)
    outers = [(loop, []) for loop in loops if _area(loop) > 0]
    for hole in (loop for loop in loops if _area(loop) < 0):
        outer, holes = min((region for region in outers if _inside(hole[0], region[0])),
                           key=lambda region: _area(region[0]))
        holes.append(hole)
    result = []
    for outer, holes in outers:
        parts = [f for f, loop in zip(fragments, polygons) if not f.opening and _contributes(loop, [outer] + holes)]
        world = [_rotate([(xs[x], ys[y]) for x, y in loop], angle) for loop in [outer] + holes]
        result.append(Region(layer, world[0], world[1:], parts, angle))
    return result


def _bounds(loop):
    return min(x for x, y in loop), min(y for x, y in loop), max(x for x, y in loop), max(y for x, y in loop)


def merge(fragments):
    """WCS 조각·개구부 → 레이어별 영역과 방향 접합 경고. 실패하면 WallError."""
    groups = []
    for fragment in sorted(fragments, key=lambda f: (f.layer, _angle(f.polygon))):
        angle = _angle(fragment.polygon)
        group = next((g for g in groups if g[0] == fragment.layer and _same_angle(g[1], angle)), None)
        if group is None:
            group = (fragment.layer, angle, [])
            groups.append(group)
        group[2].append(fragment)
    regions = []
    for layer, angle, group in groups:
        if not any(not f.opening for f in group):
            raise WallError("SPEC_WALL", group[0].path, "개구부와 같은 레이어·방향의 벽 조각이 없음")
        regions.extend(_merge_group(layer, angle, group))
    warnings = []
    for i, a in enumerate(regions):
        for b in regions[i + 1:]:
            if a.layer != b.layer or _same_angle(a.angle, b.angle):
                continue
            ax1, ay1, ax2, ay2 = _bounds(a.outer)
            bx1, by1, bx2, by2 = _bounds(b.outer)
            if ax1 <= bx2 + SNAP_TOL and bx1 <= ax2 + SNAP_TOL and ay1 <= by2 + SNAP_TOL and by1 <= ay2 + SNAP_TOL:
                warning = f"{a.layer}: 방향이 다른 벽 접합부는 합치지 않음 — 접합부 선이 남음"
                if warning not in warnings:
                    warnings.append(warning)
    return regions, warnings
