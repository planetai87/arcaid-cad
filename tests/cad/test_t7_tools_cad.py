"""T7 새 툴 CAD 연동 테스트(GstarCAD 실행 중, ARCAID_CAD_TESTS=1)

new_from_template, ensure_standard_layers(보호된 경로), get_selection, plot(사본에서 출력), add_dimension 기본 문자 높이.
"""
from pathlib import Path

import ezdxf
import pytest

from src import apply_service, tools_service
from src.apply_service import Hooks, draw_batch, ensure_layers
from src.engine.check import check_standard
from src.engine.definitions import compare_definitions
from src.engine.results import Status
from src.engine.standards import load_standard
from test_apply_cad import SPEC as BATCH_SPEC

LAYOUT_PROPS = ("ConfigName", "StyleSheet", "CanonicalMediaName", "PlotType", "CenterPlot", "PaperUnits")


@pytest.fixture(autouse=True)
def reset_guard():
    apply_service.GUARD.reason = None
    yield
    apply_service.GUARD.reason = None


@pytest.fixture
def std():
    return load_standard()


def snap(ctl, folder, name):
    r = ctl.export_dxf(str(folder / name))
    assert r["ok"], r
    return ezdxf.readfile(r["path"])


def definition_problems(std, doc, layers):
    return [v for v in compare_definitions(std, doc, layers=layers, dimstyles=[], blocks=[], text_style=False)
            if not v.code.endswith("_MISSING")]


def test_new_from_template(cad, tmp_path, std):
    ctl, _ = cad
    path = tmp_path / "새 도면.dwg"
    r = tools_service.new_from_template(ctl, std, str(path))
    assert r.status is Status.SUCCESS and path.exists()
    assert Path(ctl.doc_state(ctl.doc)["full_name"]).resolve() == path.resolve()
    doc_path = tmp_path / "check"
    check = check_standard(Path(ctl.export_dxf(str(doc_path))["path"]), std, k=100)
    assert check.violations == []
    again = tools_service.new_from_template(ctl, std, str(path))
    assert again.status is Status.FAILED and "덮어쓰지 않음" in again.summary


def test_ensure_layers_creates_conflicts_and_rolls_back(cad, tmp_path, std):
    ctl, _ = cad
    ctl.add_document_and_save(str(tmp_path / "blank.dwg"))
    work = tmp_path / "work"
    r = ensure_layers(ctl, work, std, ["cen", "LIM", "WAL"])
    assert r.status is Status.SUCCESS and r.applied[0].count == 3, r.summary
    doc = snap(ctl, tmp_path, "s1")
    assert definition_problems(std, doc, ["CEN", "LIM", "WAL"]) == []
    assert ensure_layers(ctl, work, std, ["CEN"]).summary.endswith("이미 있음")

    def boom(_):
        raise RuntimeError("일부러 낸 실패")

    r = ensure_layers(ctl, work, std, ["DOR"], hooks=Hooks(after_change=boom))
    assert r.status is Status.RECOVERED, r.summary
    assert not snap(ctl, tmp_path, "s2").layers.has_entry("DOR")

    assert ctl.send_command("_.-LAYER\n_C\n1\nWAL\n\n")["ok"]
    r = ensure_layers(ctl, work, std, ["WAL", "DOR"])
    assert r.status is Status.CONFLICT and [v.code for v in r.violations] == ["LAYER_COLOR"]
    assert not snap(ctl, tmp_path, "s3").layers.has_entry("DOR")     # 충돌이면 아무것도 만들지 않음


def test_get_selection_counts_and_engine_objects(cad, synthetic_dwg, tmp_path, std):
    ctl, _ = cad
    assert ctl.open_drawing(str(synthetic_dwg))
    work = tmp_path / "work"
    assert ctl.run_lisp('(progn (sssetfirst nil (ssget "_X" \'((8 . "CEN")))) T)')["ok"]
    r = tools_service.selection(ctl, work)
    assert r.status is Status.SUCCESS and r.count == 5 and r.by_layer == {"CEN": 5} and r.engine_objects == []
    assert draw_batch(ctl, work, std, BATCH_SPEC).status is Status.SUCCESS
    assert ctl.run_lisp('(progn (sssetfirst nil (ssget "_X" \'((-3 ("ARCAID"))))) T)')["ok"]
    r = tools_service.selection(ctl, work)
    assert r.count == 9 and len(r.engine_objects) == 9
    assert all(o.oid.startswith(f"{BATCH_SPEC['op']}:") for o in r.engine_objects)


def test_plot_sheet_and_window_without_touching_drawing(cad, synthetic_dwg, tmp_path, std):
    ctl, _ = cad
    assert ctl.open_drawing(str(synthetic_dwg))
    sheet = ctl._safe_attr(ctl.insert_block("HEOarchitects_sheet", [-3000, -4000]), "Handle")
    ctl.set_props(sheet, layer="TIT")
    layout = ctl._call(getattr, ctl.doc, "ActiveLayout")
    before = {k: ctl._safe_attr(layout, k) for k in LAYOUT_PROPS}
    state = ctl.doc_state(ctl.doc)
    work = tmp_path / "work"
    r = tools_service.plot(ctl, work, std)
    assert r.status is Status.SUCCESS and r.trial is False and r.k == 100 and r.sheet_handle == sheet, r.summary
    assert Path(r.pdf_path).exists() and sorted(r.media_mm) == [297.0, 420.0]
    print(f"\n도곽 출력: {r.summary} {r.pdf_path}")
    r = tools_service.plot(ctl, work, std, window=[[-3000, -4000], [39000, 25700]], scale=200,
                           out_path=str(tmp_path / "창.pdf"))
    assert r.status is Status.SUCCESS and r.k == 200 and Path(r.pdf_path).name == "창.pdf"
    layout = ctl._call(getattr, ctl.doc, "ActiveLayout")
    assert {k: ctl._safe_attr(layout, k) for k in LAYOUT_PROPS} == before
    assert ctl.doc_state(ctl.doc) == state


def test_add_dimension_without_text_height_has_no_override(cad, synthetic_dwg, tmp_path):
    ctl, _ = cad
    assert ctl.open_drawing(str(synthetic_dwg))
    dim = ctl.add_dimension((0, -3000), (6000, -3000), None, None, "DIM")
    handle = ctl._safe_attr(dim, "Handle")
    doc = snap(ctl, tmp_path, "dim")
    assert "dimtxt" not in doc.entitydb[handle].override().dimstyle_attribs
