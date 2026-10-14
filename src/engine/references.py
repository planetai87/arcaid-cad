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
    rows = ["| 높이 | 용도 | 레이어 |", "|---|---|---|"]
    for h in std.text.heights:
        rows.append(f"| {_num(h.height)} | {h.use} | {' / '.join(h.layers)} |")
    return "\n".join(rows)


def render_blocks(std: Standard) -> str:
    rows = ["| 블록 | 범위 | 배율 | 레이어(현재 레이어로 지정) | 삽입 방법 | 비고 |", "|---|---|---|---|---|---|"]
    for b in std.blocks:
        if b.status != "standard":
            continue
        rng = f"{_pt(b.extents[0])}–{_pt(b.extents[1])}"
        if b.attributes:
            rng += ", 속성 " + ", ".join(f"`{a.tag}`" for a in b.attributes)
        scale = {"s": "s", "0.5s": "**0.5·s**", "1": "1 (실제 크기)"}[b.scale]
        method = "`-INSERT` + 속성값" if b.insert == "-INSERT" else "`insert_block`"
        rows.append(f"| `{b.name}` | {rng} | {scale} | {b.layer} | {method} | {b.note} |")
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
    "blocks": render_blocks,
    "unused-blocks": render_unused_blocks,
}

# 생성 구역이 있어야 하는 문서와 그 구역(순서대로)
TARGETS: Dict[str, List[str]] = {
    "layers.md": ["layers", "color-lineweights"],
    "annotation.md": ["text-heights", "blocks", "unused-blocks"],
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
