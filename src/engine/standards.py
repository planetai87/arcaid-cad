"""회사 표준 프로파일(구조 결정 D3): standards/<id>/의 YAML을 읽고 검증한다

프로파일 폴더 하나가 표준 하나다(profile·layers·text·dimensions·blocks.yaml + template.dxf).
형식은 pydantic으로 검증하고, 파일 사이의 참조(문자 높이·블록·치수가 가리키는 레이어)도 확인한다.
표준 버전(profile.version)과 스키마 버전(profile.schema_version)은 따로 둔다.
"""
import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCHEMA_VERSION = 1
DEFAULT_PROFILE_DIR = Path(__file__).resolve().parents[2] / "standards" / "heo-2026"
SECTIONS = ("summary", "layers", "text", "dimensions", "blocks")


class StandardError(Exception):
    """표준 프로파일을 읽거나 검증하지 못했다(이유 목록을 담는다)"""

    def __init__(self, reasons: List[str]):
        self.reasons = reasons
        super().__init__("표준 프로파일 오류: " + "; ".join(reasons))


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- profile.yaml ----------

class ScaleRules(_Model):
    basis: Literal["A3"]
    default_k: int = 100
    symbol_per_k: float = 0.01
    dimscale_per_k: float = 0.5
    ltscale_per_k: float = 1.0

    def factors(self, k: int) -> Dict[str, float]:
        """A3 축척 분모 k에서 쓸 배율: s(문자·기호·간격), DIMSCALE, LTSCALE, 도곽 배율"""
        s = k * self.symbol_per_k
        return {"k": k, "s": s, "dimscale": k * self.dimscale_per_k, "ltscale": k * self.ltscale_per_k,
                "sheet_scale": s}


class TemplateRef(_Model):
    dwg: str
    dwg_sha256: str
    dxf: str
    dxf_sha256: str


class Profile(_Model):
    schema_version: int
    id: str
    name: str
    version: str
    units: Literal["mm"]
    insunits: Literal[4]
    annotative: Literal[False]
    scale: ScaleRules
    template: TemplateRef


# ---------- layers.yaml ----------

class LayerDef(_Model):
    name: str
    color: int = Field(ge=1, le=255)
    linetype: str
    plot: bool = True
    purpose: str
    object_linetypes: List[str] = Field(default_factory=list)
    drawable: bool = True


class LayersFile(_Model):
    color_lineweights_mm: Dict[int, Tuple[float, float]]
    layers: List[LayerDef]


# ---------- text.yaml ----------

class TextStyleDef(_Model):
    name: str
    font_family: str
    width: float
    fixed_height: float = 0


class TextHeight(_Model):
    id: str
    height: float = Field(gt=0)
    use: str
    layers: List[str]


class SheetText(_Model):
    id: str
    height: float = Field(gt=0)
    insert: Tuple[float, float]
    layers: List[str]
    format: Optional[str] = None


class TextSpec(_Model):
    style: TextStyleDef
    heights: List[TextHeight]
    sheet_title: List[SheetText] = Field(default_factory=list)
    special_codes: Dict[str, str] = Field(default_factory=dict)


# ---------- dimensions.yaml ----------

class DimStyleDef(_Model):
    name: str
    use: str
    layer: str
    text_style: str
    annotative: Literal[False]
    vars: Dict[str, Union[int, float, str]]


class DimSpacing(_Model):
    cen_overhang: float
    cen_overhang_beyond_wall_min: float
    extension_offset: float
    first_row_from_cen_end: float
    row_gap: float
    bubble_from_outer_dim: float
    text_above_dimline: float


class DimensionSpec(_Model):
    styles: List[DimStyleDef]
    spacing: DimSpacing
    rules: List[str] = Field(default_factory=list)


# ---------- blocks.yaml ----------

class AttribDef(_Model):
    tag: str
    height: float = Field(gt=0)
    layer: str


class BlockDef(_Model):
    name: str
    status: Literal["standard", "unused"]
    layer: Optional[str] = None
    scale: Optional[Literal["s", "0.5s", "1"]] = None
    insert: Optional[Literal["-INSERT", "insert_block"]] = None
    attributes: List[AttribDef] = Field(default_factory=list)
    extents: Tuple[Tuple[float, float], Tuple[float, float]]
    note: str = ""
    reason: str = ""

    def scale_factor(self, s: float) -> float:
        return {"s": s, "0.5s": 0.5 * s, "1": 1.0}[self.scale or "1"]


class BlocksFile(_Model):
    blocks: List[BlockDef]


# ---------- 전체 ----------

class Standard(_Model):
    profile_dir: str
    profile: Profile
    layers: List[LayerDef]
    color_lineweights_mm: Dict[int, Tuple[float, float]]
    text: TextSpec
    dimensions: DimensionSpec
    blocks: List[BlockDef]

    @property
    def ref(self) -> str:
        return f"{self.profile.id}@{self.profile.version}"

    def layer(self, name: str) -> Optional[LayerDef]:
        key = name.casefold()
        return next((x for x in self.layers if x.name.casefold() == key), None)

    def block(self, name: str) -> Optional[BlockDef]:
        key = name.casefold()
        return next((b for b in self.blocks if b.name.casefold() == key), None)

    def path(self, relative: str) -> Path:
        """프로파일 폴더 기준 상대 경로를 절대 경로로"""
        return (Path(self.profile_dir) / relative).resolve()


def _read_yaml(path: Path, reasons: List[str]) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        reasons.append(f"파일 없음: {path.name}")
    except yaml.YAMLError as e:
        reasons.append(f"YAML 형식 오류: {path.name}: {e}")
    return None


def _parse(model, data, filename: str, reasons: List[str]):
    if data is None:
        return None
    try:
        return model.model_validate(data)
    except ValidationError as e:
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            reasons.append(f"{filename}: {loc}: {err['msg']}")
    return None


def _cross_check(layers: List[LayerDef], text: TextSpec, dims: DimensionSpec, blocks: List[BlockDef]) -> List[str]:
    reasons = []
    seen = {}
    for layer in layers:
        key = layer.name.casefold()
        if key in seen:
            reasons.append(f"layers.yaml: 레이어 이름 중복: {seen[key]} / {layer.name}")
        seen[key] = layer.name

    def need_layer(where: str, name: Optional[str]):
        if name is not None and name.casefold() not in seen:
            reasons.append(f"{where}: 표준에 없는 레이어 {name}")

    for h in text.heights + text.sheet_title:
        for name in h.layers:
            need_layer(f"text.yaml {h.id}", name)
    for d in dims.styles:
        need_layer(f"dimensions.yaml {d.name}", d.layer)
        if d.text_style.casefold() != text.style.name.casefold():
            reasons.append(f"dimensions.yaml {d.name}: 문자 스타일 {d.text_style}가 표준 문자 스타일이 아님")
    names = set()
    for b in blocks:
        if b.name.casefold() in names:
            reasons.append(f"blocks.yaml: 블록 이름 중복: {b.name}")
        names.add(b.name.casefold())
        if b.status == "standard":
            need_layer(f"blocks.yaml {b.name}", b.layer)
            if b.scale is None or b.insert is None:
                reasons.append(f"blocks.yaml {b.name}: 쓰는 블록은 scale과 insert가 필요")
        for a in b.attributes:
            need_layer(f"blocks.yaml {b.name} 속성 {a.tag}", a.layer)
    return reasons


def load_standard(profile_dir: Optional[Path] = None) -> Standard:
    """표준 프로파일 폴더를 읽어 검증한다. 문제가 있으면 StandardError(이유 목록)"""
    if profile_dir is None:
        profile_dir = Path(os.environ.get("ARCAID_STANDARD_DIR") or DEFAULT_PROFILE_DIR)
    profile_dir = Path(profile_dir)
    reasons: List[str] = []
    profile = _parse(Profile, _read_yaml(profile_dir / "profile.yaml", reasons), "profile.yaml", reasons)
    if profile is not None and profile.schema_version != SCHEMA_VERSION:
        raise StandardError([f"스키마 버전이 다름: 파일 {profile.schema_version}, 엔진 {SCHEMA_VERSION}"])
    layers = _parse(LayersFile, _read_yaml(profile_dir / "layers.yaml", reasons), "layers.yaml", reasons)
    text = _parse(TextSpec, _read_yaml(profile_dir / "text.yaml", reasons), "text.yaml", reasons)
    dims = _parse(DimensionSpec, _read_yaml(profile_dir / "dimensions.yaml", reasons), "dimensions.yaml", reasons)
    blocks = _parse(BlocksFile, _read_yaml(profile_dir / "blocks.yaml", reasons), "blocks.yaml", reasons)
    if reasons:
        raise StandardError(reasons)
    reasons = _cross_check(layers.layers, text, dims, blocks.blocks)
    if reasons:
        raise StandardError(reasons)
    return Standard(profile_dir=str(profile_dir.resolve()), profile=profile, layers=layers.layers,
                    color_lineweights_mm=layers.color_lineweights_mm, text=text, dimensions=dims,
                    blocks=blocks.blocks)


def _pick(items: List[BaseModel], name: Optional[str], key: str = "name") -> List[BaseModel]:
    if name is None:
        return list(items)
    found = [x for x in items if str(getattr(x, key)).casefold() == name.casefold()]
    if not found:
        raise KeyError(f"표준에 없음: {name}")
    return found


def get_standard(std: Standard, section: str = "summary", name: Optional[str] = None,
                 k: Optional[int] = None) -> Dict[str, Any]:
    """표준 프로파일을 조회한다. k(A3 축척 분모)를 주면 그 축척의 실제 값(scaled)을 함께 준다

    section: summary | layers | text | dimensions | blocks. name으로 레이어·블록·치수 스타일·문자 용도 하나만 고른다.
    """
    if section not in SECTIONS:
        raise ValueError(f"알 수 없는 section: {section}(가능: {', '.join(SECTIONS)})")
    f = std.profile.scale.factors(k) if k else None
    out: Dict[str, Any] = {"standard": std.ref, "section": section}
    if f:
        out["factors"] = f
    if section == "summary":
        p = std.profile
        out.update({"name": p.name, "units": p.units, "annotative": p.annotative,
                    "scale_rules": "s = k/100, DIMSCALE = k/2, LTSCALE = k, 도곽 배율 = s (k = A3 축척 분모)",
                    "default_k": p.scale.default_k, "text_style": std.text.style.name,
                    "dimension_styles": [d.name for d in std.dimensions.styles],
                    "layer_count": len(std.layers),
                    "standard_blocks": [b.name for b in std.blocks if b.status == "standard"]})
    elif section == "layers":
        out["layers"] = [x.model_dump() for x in _pick(std.layers, name)]
        out["color_lineweights_mm"] = {c: {"A1": v[0], "A3": v[1]} for c, v in std.color_lineweights_mm.items()}
    elif section == "text":
        heights = []
        for h in _pick(std.text.heights, name, "id"):
            row = h.model_dump()
            if f:
                row["scaled"] = round(h.height * f["s"], 3)
            heights.append(row)
        out.update({"style": std.text.style.model_dump(), "heights": heights,
                    "special_codes": std.text.special_codes})
        if name is None:
            out["sheet_title"] = [t.model_dump() for t in std.text.sheet_title]
    elif section == "dimensions":
        out["styles"] = [d.model_dump() for d in _pick(std.dimensions.styles, name)]
        spacing = std.dimensions.spacing.model_dump()
        out["spacing"] = spacing
        if f:
            out["dimscale"] = f["dimscale"]
            out["spacing_scaled"] = {key: round(v * f["s"], 3) for key, v in spacing.items()}
        out["rules"] = std.dimensions.rules
    elif section == "blocks":
        rows = []
        for b in _pick(std.blocks, name):
            row = b.model_dump()
            if f and b.status == "standard":
                row["scale_factor"] = b.scale_factor(f["s"])
            rows.append(row)
        out["blocks"] = rows
    return out
