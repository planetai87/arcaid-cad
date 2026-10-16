"""스킬 references 문서의 표를 표준 프로파일에서 생성한다(구조 결정 D3: 표는 YAML에서 생성, 손으로 고치지 않음)

문서 안의 생성 구역만 바꾸고 나머지 설명은 그대로 둔다.
    <!-- arcaid:generated layers -->
    (생성된 표)
    <!-- arcaid:end -->
"""
import re
from typing import Callable, Dict, List

from .standards import Standard

START = "<!-- arcaid:generated {name} -->"
END = "<!-- arcaid:end -->"
_REGION = re.compile(r"<!-- arcaid:generated (?P<name>[\w-]+) -->\n(?P<body>.*?)<!-- arcaid:end -->", re.S)

COLOR_NAMES = {1: "RED", 2: "YELLOW", 3: "GREEN", 4: "CYAN", 5: "BLUE", 6: "MAGENTA", 7: "WHITE", 8: "GRAY", 9: "GRAY"}


def _num(v: float) -> str:
    return f"{v:g}"


def _pt(p) -> str:
    return f"({_num(p[0])},{_num(p[1])})"


def render_layers(std: Standard) -> str:
    rows = ["| 레이어 | 색(ACI) | 선종류 | 용도 |", "|---|---|---|---|"]
    for layer in std.layers:
        if layer.name == "0":
            continue
        color = f"{layer.color} {COLOR_NAMES.get(layer.color, '')}".strip()
        purpose = layer.purpose + ("(출력 안 함)" if not layer.plot else "")
        if layer.object_linetypes:
            purpose += f". 객체 선종류 {'·'.join(layer.object_linetypes)} 허용"
        rows.append(f"| {layer.name} | {color} | {layer.linetype} | {purpose} |")
    return "\n".join(rows)


def render_color_lineweights(std: Standard) -> str:
    rows = ["| 색 | A1 | A3 |", "|---|---|---|"]
    for color, (a1, a3) in sorted(std.color_lineweights_mm.items()):
        rows.append(f"| {color} {COLOR_NAMES.get(color, '')} | {a1:.2f} | {a3:.2f} |")
    return "\n".join(rows)


def render_text_heights(std: Standard) -> str:
    rows = ["| role | 기준 높이 | 용도 | 허용 레이어 |", "|---|---|---|---|"]
    for h in std.text.heights:
        rows.append(f"| `{h.id}` | {_num(h.height)} | {h.use} | {' / '.join(h.layers)} |")
    return "\n".join(rows)


def render_scale_rules(std: Standard) -> str:
    p, r = std.profile, std.profile.scale
    return "\n".join([
        "| 항목 | 프로파일 기준 |", "|---|---|",
        f"| 표준 | `{std.ref}` |", f"| 단위 / 축척 기준 | {p.units} / {r.basis} |",
        f"| 축척 생략 시 k | {r.default_k} |",
        f"| 문자·기호·간격·도곽 배율 s | k × {_num(r.symbol_per_k)} |",
        f"| 비주석 치수 DIMSCALE | k × {_num(r.dimscale_per_k)} |",
        f"| LTSCALE | k × {_num(r.ltscale_per_k)} |",
    ])


def render_dimension_styles(std: Standard) -> str:
    rows = ["| style | 용도 | 레이어 | 문자 스타일 | 주석 | 기준 DIMSCALE | DIMTXT |",
            "|---|---|---|---|---|---|---|"]
    for d in std.dimensions.styles:
        rows.append(f"| `{d.name}` | {d.use} | {d.layer} | {d.text_style} | 아니오 | "
                    f"{_num(d.vars['dimscale'])} | {_num(d.vars['dimtxt'])} |")
    return "\n".join(rows)


def render_dimension_spacing(std: Standard) -> str:
    labels = {
        "cen_overhang": "가장 바깥 축선 → CEN 끝(가변 기본값)",
        "cen_overhang_beyond_wall_min": "외벽 바깥면·돌출부보다 더 나가는 최소 길이",
        "extension_offset": "CEN 끝 → 치수보조선 시작(스타일에서 자동 적용)",
        "first_row_from_cen_end": "CEN 끝 → 첫 치수선",
        "row_gap": "치수 단 사이",
        "bubble_from_outer_dim": "가장 바깥 치수선 → 버블 중심(블록에 포함)",
        "text_above_dimline": "치수 문자 중심의 치수선 위 거리(확인용)",
    }
    rows = ["| spacing 키 | 의미 | 기준 간격 |", "|---|---|---|"]
    for key, value in std.dimensions.spacing.model_dump().items():
        rows.append(f"| `{key}` | {labels[key]} | {_num(value)} |")
    return "\n".join(rows)


def render_sheet_title(std: Standard) -> str:
    rows = ["| role | 도곽 좌하단 기준 위치 | 기준 높이 | 레이어 | 형식 |", "|---|---|---|---|---|"]
    for t in std.text.sheet_title:
        rows.append(f"| `{t.id}` | {_pt(t.insert)} | {_num(t.height)} | {' / '.join(t.layers)} | {t.format or '도면명'} |")
    return "\n".join(rows)


def render_blocks(std: Standard) -> str:
    rows = ["| name | 형상 범위 | 엔진 배율 | 엔진 레이어 | attribs 키 | 비고 |", "|---|---|---|---|---|---|"]
    for b in std.blocks:
        if b.status != "standard":
            continue
        rng = f"{_pt(b.extents[0])}–{_pt(b.extents[1])}"
        tags = ", ".join(f"`{a.tag}`" for a in b.attributes) or "—"
        scale = {"s": "s", "0.5s": "**0.5·s**", "1": "1 (실제 크기)"}[b.scale]
        rows.append(f"| `{b.name}` | {rng} | {scale} | {b.layer} | {tags} | {b.note} |")
    return "\n".join(rows)


def render_unused_blocks(std: Standard) -> str:
    rows = ["| 블록 | 쓰지 않는 이유 |", "|---|---|"]
    for b in std.blocks:
        if b.status == "unused":
            rows.append(f"| `{b.name}` | {b.reason} |")
    return "\n".join(rows)


RENDERERS: Dict[str, Callable[[Standard], str]] = {
    "layers": render_layers,
    "color-lineweights": render_color_lineweights,
    "text-heights": render_text_heights,
    "scale-rules": render_scale_rules,
    "dimension-styles": render_dimension_styles,
    "dimension-spacing": render_dimension_spacing,
    "sheet-title": render_sheet_title,
    "blocks": render_blocks,
    "unused-blocks": render_unused_blocks,
}

# 생성 구역이 있어야 하는 문서와 그 구역(순서대로)
TARGETS: Dict[str, List[str]] = {
    "layers.md": ["layers", "color-lineweights"],
    "annotation.md": ["scale-rules", "text-heights", "dimension-styles", "dimension-spacing",
                      "blocks", "unused-blocks", "sheet-title"],
}


def region_names(text: str) -> List[str]:
    return [m.group("name") for m in _REGION.finditer(text)]


def apply_regions(text: str, std: Standard) -> str:
    """문서의 생성 구역을 표준에서 다시 만든 내용으로 바꾼 문서를 돌려준다"""
    def repl(m):
        name = m.group("name")
        if name not in RENDERERS:
            raise ValueError(f"알 수 없는 생성 구역: {name}")
        return f"{START.format(name=name)}\n{RENDERERS[name](std)}\n{END}"
    return _REGION.sub(repl, text)
