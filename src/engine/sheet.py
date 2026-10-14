"""출력 범위(도곽, T7): 도곽 블록 참조에서 출력 창과 축척 k를 구한다(CAD 없이)

도곽 HEOarchitects_sheet는 배율 1에서 A3 1/100(42000×29700)이고, 배율 s로 넣으면 A3 1/k(k = 100 × s)다(blocks.yaml).
0단계는 회전하지 않은 균등 배율 도곽만 출력 범위로 쓴다.
"""
from typing import List, Tuple

from pydantic import BaseModel, ConfigDict

from .standards import Standard

SHEET_BLOCK = "HEOarchitects_sheet"
TOL = 1e-6


class SheetInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    handle: str
    insert: List[float]
    scale: float
    k: int
    window: List[List[float]]


def find_sheets(doc, std: Standard) -> Tuple[List[SheetInfo], List[str]]:
    """모형 공간의 도곽 참조 → (출력할 수 있는 도곽, 쓸 수 없는 도곽의 이유)"""
    bdef = std.block(SHEET_BLOCK)
    (x0, y0), (x1, y1) = bdef.extents
    sheets, problems = [], []
    for e in doc.modelspace().query("INSERT"):
        if e.dxf.name.casefold() != SHEET_BLOCK.casefold():
            continue
        h = e.dxf.handle
        xs, ys, rot = e.dxf.get("xscale", 1), e.dxf.get("yscale", 1), e.dxf.get("rotation", 0) % 360
        if abs(rot) > TOL and abs(rot - 360) > TOL:
            problems.append(f"도곽 {h}: 회전한 도곽({rot:g}°)은 출력 범위로 쓰지 않음")
            continue
        if xs <= 0 or abs(xs - ys) > TOL:
            problems.append(f"도곽 {h}: 배율이 균등하지 않음({xs:g}, {ys:g})")
            continue
        k = 100 * xs / (std.profile.scale.symbol_per_k * 100)
        if abs(k - round(k)) > 1e-3:
            problems.append(f"도곽 {h}: 배율 {xs:g}로는 축척 1/k를 정할 수 없음(k = {k:g})")
            continue
        ix, iy = e.dxf.insert.x, e.dxf.insert.y
        window = [[round(ix + x0 * xs, 6), round(iy + y0 * xs, 6)], [round(ix + x1 * xs, 6), round(iy + y1 * xs, 6)]]
        sheets.append(SheetInfo(handle=h, insert=[ix, iy], scale=xs, k=int(round(k)), window=window))
    return sheets, problems
