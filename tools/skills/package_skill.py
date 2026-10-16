"""회사 스킬 zip 생성: 상대 링크·템플릿을 확인하고 임시 파일에서 완성한 뒤 교체한다."""
import argparse
import json
import re
import uuid
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "skills" / "cad-drawing-standard"
TEMPLATE = "assets/회사표준_템플릿.dwg"
EXCLUDED_DIRS = {"backup", "backups", "tmp", "temp", "__pycache__"}


def package_skill(source: Path, output: Path) -> list[str]:
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.suffix.lower() != ".zip":
        raise ValueError("출력은 .zip 경로여야 합니다")
    if not (source / "SKILL.md").is_file():
        raise ValueError("SKILL.md가 없습니다")
    if not (source / TEMPLATE).is_file():
        raise ValueError("회사 템플릿이 없습니다")
    files = [source / "SKILL.md", source / TEMPLATE]
    files += [p for p in (source / "references").rglob("*")
              if p.is_file() and p.suffix in {".md", ".json"}
              and not any(part.casefold() in EXCLUDED_DIRS or part.startswith(".")
                          for part in p.relative_to(source / "references").parts[:-1])]
    files.sort(key=lambda p: p.relative_to(source).as_posix())
    paths = {p.resolve() for p in files}
    if any(not p.is_relative_to(source) for p in paths):
        raise ValueError("스킬 폴더 밖을 가리키는 파일이 있습니다")
    for path in files:
        if path.suffix != ".md":
            continue
        for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if "://" in link or link.startswith("#"):
                continue
            target = (path.parent / link.split("#", 1)[0]).resolve()
            if target not in paths:
                raise ValueError(f"패키지에 없는 상대 링크: {path.name}: {link}")
    names = [f"{source.name}/{p.relative_to(source).as_posix()}" for p in files]
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            for path, name in zip(files, names):
                archive.write(path, name)
        with ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("zip 무결성 검사 실패")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description="검증한 회사 스킬 업로드 zip을 만듭니다")
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    names = package_skill(SOURCE, args.output)
    print(json.dumps({"zip": str(args.output.resolve()), "files": names}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
