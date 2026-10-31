"""읽기 전용 뷰어의 탐색·경계·전개·변경 알림을 확인한다."""
import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("starlette")
from starlette.testclient import TestClient
import ezdxf

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def sample(tmp_path):
    data = json.loads((ROOT / "tests/engine/specs/dangsan_typical.json").read_text(encoding="utf-8"))
    (tmp_path / "plan.json").write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


def client_for(path, **kwargs):
    from src.ui.server import create_app
    return TestClient(create_app(path, **kwargs))


def test_workspace_discovery_and_ops_order(sample):
    ops = sample / "ops"
    ops.mkdir()
    data = json.loads((sample / "plan.json").read_text())
    for op in ["a", "z"]:
        (ops / (op + ".json")).write_text(json.dumps(dict(data, op=op)))
    (ops / "hashes.json").write_text(json.dumps([{"op": "z"}, {"op": "a"}]))
    (sample / "broken.json").write_text("{")
    (sample / "metadata.json").write_text('{"note": "무관한 파일"}', encoding="utf-8")
    deep = ops / "deep"
    deep.mkdir()
    (deep / "hidden.json").write_text(json.dumps(data))
    result = client_for(sample).get("/api/workspace").json()
    rows = result["specs"]
    assert [r["op"] for r in rows if r["path"].startswith("ops/")] == ["z", "a"]
    assert next(r for r in rows if r["path"] == "broken.json")["error"]
    assert all(len(r["sha256"]) == 64 and r["mtime_ns"] for r in rows)
    assert not any("hidden" in r["path"] or "metadata" in r["path"] for r in rows)


def test_spec_expansion_sources_and_colors(sample):
    response = client_for(sample).get("/api/spec", params={"path": "plan.json"})
    assert response.status_code == 200
    data = response.json()
    assert len(data["prims"]) == 107
    assert all(p["sources"] for p in data["prims"])
    assert data["prims"][0]["sources"][0]["item"].startswith("items[")
    assert len(data["layers"]["WAL"]["color_rgb"]) == 3
    assert data["grid"]["X"]["X2"] == 3300
    assert not data["violations"]


@pytest.mark.parametrize("content", ['{"items": []}', '{', '{"schema_version":1,"standard":"missing@1","op":"x","items":[]}'])
def test_invalid_spec_has_no_prims(tmp_path, content):
    (tmp_path / "bad.json").write_text(content)
    result = client_for(tmp_path).get("/api/spec", params={"path": "bad.json"}).json()
    assert result["violations"]
    assert not result.get("prims")


def test_base_nested_insert_parent_handle_and_limits(tmp_path):
    doc = ezdxf.new()
    inner = doc.blocks.new("inner")
    inner.add_line((0, 0), (100, 50))
    outer = doc.blocks.new("outer")
    outer.add_blockref("inner", (10, 20))
    parent = doc.modelspace().add_blockref("outer", (1000, 2000))
    doc.modelspace().add_circle((0, 0), 20)
    doc.saveas(tmp_path / "base.dxf")
    data = client_for(tmp_path).get("/api/base", params={"path": "base.dxf"}).json()
    line = next(p for p in data["prims"] if p["kind"] == "line")
    assert line["handle"] == parent.dxf.handle
    assert line["points"] == [[1010, 2020], [1110, 2070]]
    assert data["extents"]["maxX"] >= 1110
    from src.ui.base import read_base
    assert read_base(tmp_path / "base.dxf", limit=1)["truncated"]


@pytest.mark.parametrize("path", ["../outside.json", "..\\outside.json", "C:/Windows/win.ini", "/etc/passwd", "plan.json/../../outside.json"])
def test_reject_outside_paths(sample, path):
    client = client_for(sample)
    for endpoint in ["spec", "base"]:
        assert client.get("/api/" + endpoint, params={"path": path}).status_code == 403


def test_explicit_base_outside_workspace(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    base = tmp_path / "explicit.dxf"
    ezdxf.new().saveas(base)
    client = client_for(work, base=base)
    rows = client.get("/api/workspace").json()["bases"]
    assert len(rows) == 1
    assert client.get("/api/base", params={"path": rows[0]["path"]}).status_code == 200
    assert client.get("/api/spec", params={"path": rows[0]["path"]}).status_code == 403


def test_sse_change_event_with_timeout(sample):
    from src.ui.workspace import Workspace
    from src.ui.server import change_events
    workspace = Workspace(sample)

    async def check():
        events = change_events(workspace, interval=0.01)
        assert "connected" in await anext(events)
        data = json.loads((sample / "plan.json").read_text())
        data["items"][3]["points"][0][0] = 10
        (sample / "plan.json").write_text(json.dumps(data))
        event = await asyncio.wait_for(anext(events), timeout=2)
        await events.aclose()
        payload = json.loads(event.split("data: ")[1])
        assert payload["type"] == "changed"
        assert payload["path"] == "plan.json"
        assert len(payload["sha256"]) == 64

    asyncio.run(check())


def test_static_fallback_and_import_boundaries(sample):
    assert client_for(sample, dist=sample / "missing").get("/").status_code == 200
    import ast
    forbidden = ("mcp", "win32com", "pythoncom", "src.server", "src.cad_controller")
    for path in (ROOT / "src/ui").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                assert not any(n.name.startswith(forbidden) for n in node.names)
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(forbidden)


def test_base_all_supported_entities(tmp_path):
    from src.ui.base import read_base
    doc = ezdxf.new("R2013")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0, 1), (100, 0, 0)], format="xyb")
    msp.add_polyline2d([(200, 0), (300, 100)])
    msp.add_arc((400, 0), 50, 0, 90)
    msp.add_text("문자", dxfattribs={"height": 10})
    msp.add_mtext("첫째\\P둘째", dxfattribs={"char_height": 10})
    hatch = msp.add_hatch()
    hatch.paths.add_polyline_path([(0, 0), (100, 0), (100, 100), (0, 100)], is_closed=True)
    dim = msp.add_linear_dim(base=(0, 150), p1=(0, 0), p2=(100, 0))
    dim.render()
    path = tmp_path / "all.dxf"
    doc.saveas(path)
    data = read_base(path)
    assert not data["warnings"]
    assert {"polyline", "arc", "text", "hatch", "line"} <= {p["kind"] for p in data["prims"]}
    assert any(p.get("text") == "첫째\n둘째" for p in data["prims"])
    assert any(p["handle"] == dim.dimension.dxf.handle for p in data["prims"])
    assert any(p.get("bulges") == [1, 0] for p in data["prims"])


def test_base_nonuniform_insert_keeps_curves(tmp_path):
    from src.ui.base import read_base
    doc = ezdxf.new()
    block = doc.blocks.new("curves")
    block.add_circle((0, 0), 10)
    block.add_arc((30, 0), 5, 0, 90)
    parent = doc.modelspace().add_blockref("curves", (100, 200), dxfattribs={"xscale": 2, "yscale": 1})
    path = tmp_path / "scaled.dxf"
    doc.saveas(path)
    data = read_base(path)
    assert len(data["prims"]) == 2
    assert all(p["handle"] == parent.dxf.handle for p in data["prims"])
    assert data["extents"]["minX"] == pytest.approx(80)
    assert data["extents"]["maxY"] == pytest.approx(210)


def test_base_reflected_polyline_bulge(tmp_path):
    from src.ui.base import read_base
    doc = ezdxf.new()
    doc.modelspace().add_polyline2d([(0, 0, 1), (10, 0, 0)], format="xyb",
                                   dxfattribs={"extrusion": (0, 0, -1)})
    path = tmp_path / "reflected.dxf"
    doc.saveas(path)
    prim = read_base(path)["prims"][0]
    assert prim["points"] == [[0, 0], [-10, 0]]
    assert prim["bulges"] == [-1, 0]


@pytest.mark.parametrize("number", [float("nan"), float("inf"), -float("inf")])
def test_invalid_nonfinite_spec_returns_violations(sample, number):
    data = json.loads((sample / "plan.json").read_text())
    data["items"][0]["items"][0]["start"][0] = number
    (sample / "plan.json").write_text(json.dumps(data))
    response = client_for(sample).get("/api/spec", params={"path": "plan.json"})
    assert response.status_code == 200
    assert response.json()["violations"]
    assert not response.json().get("prims")
