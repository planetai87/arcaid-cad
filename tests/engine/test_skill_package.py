"""실제 배포 파일 목록·한글 이름·상대 링크를 패키지 경계에서 검증한다."""
from pathlib import Path
from zipfile import ZipFile

import pytest

from tools.skills.package_skill import SOURCE, package_skill


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "cad-drawing-standard"
    (root / "references").mkdir(parents=True)
    (root / "assets").mkdir()
    (root / "SKILL.md").write_text("[예제](references/example.json)\n", encoding="utf-8")
    (root / "references" / "example.json").write_text("{}", encoding="utf-8")
    (root / "assets" / "회사표준_템플릿.dwg").write_bytes(b"dwg-fixture")
    return root


def test_package_roundtrip_preserves_unicode_and_excludes_backups(source, tmp_path):
    (source / "assets" / "회사표준_템플릿.dwg.bak").write_bytes(b"backup")
    (source / "references" / "scratch.tmp").write_text("temporary")
    (source / "references" / "backup").mkdir()
    (source / "references" / "backup" / "old.md").write_text("backup")
    (source / "references" / "temp").mkdir()
    (source / "references" / "temp" / "scratch.json").write_text("{}")
    output = tmp_path / "skill.zip"
    names = package_skill(source, output)
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        assert sorted(archive.namelist()) == sorted(names)
        assert names == ["cad-drawing-standard/SKILL.md", "cad-drawing-standard/assets/회사표준_템플릿.dwg",
                         "cad-drawing-standard/references/example.json"]
        assert archive.read("cad-drawing-standard/assets/회사표준_템플릿.dwg") == b"dwg-fixture"
        assert archive.getinfo("cad-drawing-standard/assets/회사표준_템플릿.dwg").flag_bits & 0x800


def test_missing_template_refused_before_replacing_output(source, tmp_path):
    (source / "assets" / "회사표준_템플릿.dwg").unlink()
    output = tmp_path / "skill.zip"
    output.write_bytes(b"previous-archive")
    with pytest.raises(ValueError, match="템플릿"):
        package_skill(source, output)
    assert output.read_bytes() == b"previous-archive"


def test_broken_relative_link_is_not_deployed(source, tmp_path):
    (source / "SKILL.md").write_text("[없는 예제](references/missing.json)\n", encoding="utf-8")
    with pytest.raises(ValueError, match="링크"):
        package_skill(source, tmp_path / "skill.zip")


def test_repository_package_includes_change_reference_and_example(tmp_path):
    output = tmp_path / "cad-drawing-standard.zip"
    names = package_skill(SOURCE, output)
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        for relative in ("references/change-spec.md", "references/examples/synthetic-change.json"):
            name = f"cad-drawing-standard/{relative}"
            assert name in names
            assert archive.read(name) == (SOURCE / relative).read_bytes()
