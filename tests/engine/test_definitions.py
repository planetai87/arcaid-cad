"""템플릿·도면의 정의를 표준과 비교(src/engine/definitions.py) 테스트"""
import re
import shutil
from pathlib import Path

import ezdxf

from src.engine.definitions import check_template, compare_definitions
from src.engine.results import Status
from src.engine.standards import DEFAULT_PROFILE_DIR, load_standard

TEMPLATE_DXF = DEFAULT_PROFILE_DIR / "template.dxf"


def test_committed_template_matches_yaml():
    r = check_template(load_standard())
    assert r.violations == [], [v.message for v in r.violations]
    assert r.status is Status.SUCCESS and r.is_complete()
    assert {o.item for o in r.out_of_scope} >= {"해치 패턴", "출력 스타일(CTB)"}


def test_mutated_template_reports_each_difference():
    doc = ezdxf.readfile(TEMPLATE_DXF)
    doc.layers.get("CEN").dxf.color = 3
    ds = doc.dimstyles.get("DIM330")
    ds.dxf.dimtxt = 5.0
    ds.set_xdata("AcadAnnotative", [(1000, "AnnotativeData"), (1002, "{"), (1070, 1), (1070, 1), (1002, "}")])
    bubble = doc.blocks.get("축열버블-상")
    bubble.delete_entity(next(iter(bubble.query("ATTDEF"))))
    doc.layers.remove("WIN")
    codes = sorted(v.code for v in compare_definitions(load_standard(), doc))
    assert codes == sorted(["LAYER_COLOR", "DIMSTYLE_VAR", "DIMSTYLE_ANNOTATIVE", "BLOCK_ATTRIB", "LAYER_MISSING"])


def test_violation_carries_current_and_expected():
    doc = ezdxf.readfile(TEMPLATE_DXF)
    doc.layers.get("LIM").dxf.linetype = "Continuous"
    (v,) = compare_definitions(load_standard(), doc)
    assert (v.code, v.current, v.expected) == ("LAYER_LINETYPE", "Continuous", "HID")


def test_moved_block_geometry_reported():
    doc = ezdxf.readfile(TEMPLATE_DXF)
    blk = doc.blocks.get("계단화살표")
    blk.add_line((0, 0), (500, 0), dxfattribs={"layer": "DIM"})
    codes = [v.code for v in compare_definitions(load_standard(), doc)]
    assert codes == ["BLOCK_EXTENTS"]


def test_hash_mismatch_detected(tmp_path):
    p = tmp_path / "heo-2026"
    shutil.copytree(DEFAULT_PROFILE_DIR, p)
    original = load_standard()
    shutil.copyfile(original.path(original.profile.template.dwg), p / "template.dwg")
    prof = p / "profile.yaml"
    text = prof.read_text(encoding="utf-8")
    text = re.sub(r"dwg: \S+", "dwg: template.dwg", text)
    prof.write_text(re.sub(r"dwg_sha256: \S+", 'dwg_sha256: "' + "0" * 64 + '"', text), encoding="utf-8")
    r = check_template(load_standard(p))
    assert "TEMPLATE_DWG_HASH" in {v.code for v in r.violations}
    assert r.status is Status.CONFLICT
