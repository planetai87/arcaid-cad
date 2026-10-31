"""tools/raster 생성기 공용 도우미: 거부 예외, 출력 반올림, frame, 관측 → 지역 좌표 선, 교점.

generate.py(형상)와 annotate.py(주석·기호)가 같이 쓴다. generate.py를 스크립트로 실행해도 이 모듈은 한 벌만
읽혀서, annotate가 던진 _Refused를 generate가 그대로 잡는다.
"""
import math

SNAP_DEG = 0.5
MIN_CROSS_DEG = 15.0
DIGITS = 3


class _Refused(ValueError):
    pass


def _o(v):
    """출력 좌표: 소수 셋째 자리, -0.0 정리"""
    v = round(float(v), DIGITS)
    return 0.0 if v == 0 else v


def _wrap(a):
    return (a + 90.0) % 180.0 - 90.0


def _yes_no(value, attr):
    """해석 값(record.py interpret --value는 문자열로 저장)을 참·거짓으로"""
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in ("true", "yes", "1", "예"):
        return True
    if text in ("false", "no", "0", "아니오"):
        return False
    raise _Refused(f"{attr}는 true|false(받은 것 {value})")


# ---------- 선·frame ----------

class _Frame:
    def __init__(self, name, x, y, deg):
        self.name, self.x, self.y, self.deg = name, x, y, deg
        self.c, self.s = math.cos(math.radians(deg)), math.sin(math.radians(deg))

    @property
    def identity(self):
        return self.x == 0 and self.y == 0 and self.deg == 0

    def local(self, x, y):
        dx, dy = x - self.x, y - self.y
        return self.c * dx + self.s * dy, -self.s * dx + self.c * dy

    def wcs(self, u, v):
        return self.x + self.c * u - self.s * v, self.y + self.s * u + self.c * v


class _Line:
    """frame 지역 좌표의 직선. axis u면 v=coord인 가로선, v면 u=coord인 세로선, None이면 점 p·방향 d"""
    def __init__(self, axis=None, coord=None, p=None, d=None):
        self.axis, self.coord, self.p, self.d = axis, coord, p, d
        if axis == "u":
            self.p, self.d = (0.0, coord), (1.0, 0.0)
        elif axis == "v":
            self.p, self.d = (coord, 0.0), (0.0, 1.0)


def _line(obs, frame):
    """관측 → frame 지역 좌표의 선. 같은 frame에서 잰 축 선이면 기록 좌표를 그대로 쓴다"""
    if [float(v) for v in obs["frame"]] == [frame.x, frame.y, frame.deg] and \
            abs(_wrap(obs["angle_local_deg"] - (0.0 if obs["axis"] == "u" else 90.0))) <= SNAP_DEG:
        return _Line(obs["axis"], float(obs["coord"]))
    pu, pv = frame.local(*obs["point_wcs"])
    a = obs["angle_wcs_deg"] - frame.deg
    if abs(_wrap(a)) <= SNAP_DEG:
        return _Line("u", pv)
    if abs(_wrap(a - 90.0)) <= SNAP_DEG:
        return _Line("v", pu)
    r = math.radians(a)
    return _Line(p=(pu, pv), d=(math.cos(r), math.sin(r)))


def _cross(a, b):
    """두 선의 교점. 교차각이 MIN_CROSS_DEG 미만이면 거부(교점 오차 ≈ 선 오차 / sin 교차각)"""
    if a.axis == "u" and b.axis == "v":
        return b.coord, a.coord
    if a.axis == "v" and b.axis == "u":
        return a.coord, b.coord
    det = a.d[0] * b.d[1] - a.d[1] * b.d[0]
    if abs(det) < math.sin(math.radians(MIN_CROSS_DEG)):
        raise _Refused(f"두 선이 {MIN_CROSS_DEG:.0f}° 미만으로 만나 교점을 정하지 않음")
    t = ((b.p[0] - a.p[0]) * b.d[1] - (b.p[1] - a.p[1]) * b.d[0]) / det
    return a.p[0] + t * a.d[0], a.p[1] + t * a.d[1]


def _seg(iid, layer, a, b):
    return {"kind": "line", "id": iid, "layer": layer, "start": [_o(a[0]), _o(a[1])], "end": [_o(b[0]), _o(b[1])]}
