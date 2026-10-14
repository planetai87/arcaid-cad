"""표준 프로파일 로더(src/engine/standards.py) 테스트"""
import shutil
from pathlib import Path

import pytest

from src.engine.standards import DEFAULT_PROFILE_DIR, SCHEMA_VERSION, StandardError, get_standard, load_standard


def copy_profile(tmp_path: Path) -> Path:
    """표준 폴더를 임시 폴더로 복사한다(템플릿 경로는 상대 경로라 DXF만 같이 옮기면 된다)"""
    dst = tmp_path / "heo-2026"
    shutil.copytree(DEFAULT_PROFILE_DIR, dst)
    return dst


def test_loads_default_profile():
    std = load_standard()
    assert std.ref == "heo-2026@2026.1"
    assert std.profile.schema_version == SCHEMA_VERSION == 1
    assert len(std.layers) == 41
    assert std.layer("cen").plot is False
    assert std.layer("PAT").name.lower() == "pat"
    assert std.layer("0").drawable is False


def test_scale_factors():
    f = load_standard().profile.scale.factors(200)
    assert (f["s"], f["dimscale"], f["ltscale"], f["sheet_scale"]) == (2.0, 100.0, 200.0, 2.0)


def test_lim_allows_phantom_and_center():
    assert set(load_standard().layer("LIM").object_linetypes) == {"PHANTOM", "CENTER"}


def test_dimension_styles_are_non_annotative_with_k100_dimscale():
    dims = {d.name: d for d in load_standard().dimensions.styles}
    assert set(dims) == {"DIM330", "DIM270"}
    assert dims["DIM330"].vars["dimtxt"] == 3.3 and dims["DIM330"].vars["dimscale"] == 50
    assert dims["DIM270"].layer == "DIM1"


def test_get_standard_scales_text_heights():
    d = get_standard(load_standard(), "text", k=200)
    general = next(h for h in d["heights"] if h["id"] == "general")
    assert general["height"] == 135 and general["scaled"] == 270
    assert d["factors"]["s"] == 2.0


def test_get_standard_single_layer_by_name():
    d = get_standard(load_standard(), "layers", name="lim")
    assert [layer["name"] for layer in d["layers"]] == ["LIM"]
    assert "대지경계선" in d["layers"][0]["purpose"]


def test_get_standard_unknown_name_raises():
    with pytest.raises(KeyError):
        get_standard(load_standard(), "blocks", name="없는블록")


def test_rejects_duplicate_layer(tmp_path):
    p = copy_profile(tmp_path)
    layers = p / "layers.yaml"
    text = layers.read_text(encoding="utf-8")
    layers.write_text(text + "\n  - name: cen\n    color: 1\n    linetype: CEN\n    purpose: 중복\n", encoding="utf-8")
    with pytest.raises(StandardError, match="중복"):
        load_standard(p)


def test_rejects_dangling_layer_reference(tmp_path):
    p = copy_profile(tmp_path)
    text_yaml = p / "text.yaml"
    text_yaml.write_text(text_yaml.read_text(encoding="utf-8").replace("[TIT1]", "[XYZ]", 1), encoding="utf-8")
    with pytest.raises(StandardError, match="XYZ"):
        load_standard(p)


def test_wrong_schema_version_rejected(tmp_path):
    p = copy_profile(tmp_path)
    prof = p / "profile.yaml"
    prof.write_text(prof.read_text(encoding="utf-8").replace("schema_version: 1", "schema_version: 2"), encoding="utf-8")
    with pytest.raises(StandardError, match="스키마"):
        load_standard(p)
