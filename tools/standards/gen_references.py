"""스킬 references 문서의 표를 표준 프로파일(standards/heo-2026)에서 다시 만든다

사용법:
  python tools/standards/gen_references.py --write   생성 구역을 고쳐 쓴다
  python tools/standards/gen_references.py --check   다르면 파일 이름을 출력하고 종료 코드 1
표 밖의 설명은 손으로 고치고, 표는 YAML을 고친 뒤 이 도구로 만든다.
"""
import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.engine.references import TARGETS, apply_regions  # noqa: E402
from src.engine.standards import load_standard  # noqa: E402

REFS = REPO / "skills" / "cad-drawing-standard" / "references"


def main() -> int:
    ap = argparse.ArgumentParser(description="스킬 references 표를 표준 YAML에서 생성한다")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = ap.parse_args()
    std = load_standard()
    stale = []
    for name in TARGETS:
        path = REFS / name
        text = path.read_text(encoding="utf-8")
        new = apply_regions(text, std)
        if new != text:
            stale.append(name)
            if args.write:
                path.write_text(new, encoding="utf-8", newline="\n")
    if args.check:
        for name in stale:
            print("다름:", name)
        return 1 if stale else 0
    print("고쳐 씀:", ", ".join(stale) or "없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
