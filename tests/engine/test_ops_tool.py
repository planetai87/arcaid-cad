"""ops 폴더 검사·묶기 도구(tools/ops/ops.py): 합성 ops 폴더로 확인한다(CAD 없음)"""
import copy
import json

import pytest

from src.engine.spec import parse_spec, spec_hash
from tools.ops import ops as tool


def _spec(op, y=0.0, **extra):
    return dict(schema_version=1, standard="heo-2026@2026.1", op=op, units="mm", scale=100,
                items=[dict(kind="line", id="wall", layer="WAL", start=[-0.0, y], end=[1000, y])], **extra)


def _write(folder, specs, order=None, hashes=None):
    folder.mkdir(parents=True, exist_ok=True)
    for s in specs:
        (folder / f"{s['op']}.json").write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    listed = hashes or [{"op": s["op"], "spec_hash": spec_hash(parse_spec(s))} for s in specs]
    if order:
        listed = [next(r for r in listed if r["op"] == name) for name in order]
    (folder / "hashes.json").write_text(json.dumps(listed), encoding="utf-8")
    return folder


@pytest.fixture
def good(tmp_path):
    return _write(tmp_path / "ops", [_spec("a-walls"), _spec("b-more", 500)])


def test_verify_passes_and_reports_order(good):
    r = tool.verify(good)
    assert r["ok"] and r["problems"] == []
    assert [o["op"] for o in r["ops"]] == ["a-walls", "b-more"]
    assert r["objects"] == 2 and r["target"] is None


@pytest.mark.parametrize("case, expected", [
    ("hash", "해시 불일치"), ("duplicate", "op 중복"), ("unlisted", "목록에 없는 명세 파일"),
    ("inner-op", "파일 안의 op가 다름"), ("bad-name", "폴더 밖 경로"), ("missing", "파일 읽기 실패"),
    ("spec-id", "SPEC_ID"), ("scale", "scale 값이 다름"), ("standard", "SPEC_STANDARD"), ("target", "target 값이 다름"),
])
def test_verify_catches_problems(tmp_path, good, case, expected):
    folder = good
    listed = json.loads((good / "hashes.json").read_text(encoding="utf-8"))
    if case == "hash":
        listed[0]["spec_hash"] = "0" * 16
    elif case == "duplicate":
        listed.append(dict(listed[0]))
    elif case == "unlisted":
        (good / "c-extra.json").write_text(json.dumps(_spec("c-extra")), encoding="utf-8")
    elif case == "inner-op":
        (good / "a-walls.json").write_text(json.dumps(_spec("other")), encoding="utf-8")
    elif case == "bad-name":
        listed[0]["op"] = "../a-walls"
    elif case == "missing":
        (good / "b-more.json").unlink()
    elif case == "spec-id":
        s = _spec("b-more", 500)
        s["items"].append(copy.deepcopy(s["items"][0]))   # 같은 단계에 같은 id
        folder = _write(tmp_path / "ops2", [_spec("a-walls"), s])
        listed = None
    elif case == "scale":
        folder = _write(tmp_path / "ops2", [_spec("a-walls"), {**_spec("b-more", 500), "scale": 200}])
        listed = None
    elif case == "standard":
        folder = _write(tmp_path / "ops2", [_spec("a-walls"), {**_spec("b-more", 500), "standard": "heo-2026@0"}])
        listed = None
    elif case == "target":
        folder = _write(tmp_path / "ops2", [_spec("a-walls", target={"drawing_id": "D", "snapshot_id": "S"}),
                                            _spec("b-more", 500)])
        listed = None
    if listed is not None:
        (good / "hashes.json").write_text(json.dumps(listed), encoding="utf-8")
    r = tool.verify(folder)
    assert not r["ok"]
    assert any(expected in p for p in r["problems"]), r["problems"]


def test_verify_rejects_bad_hashes_file(tmp_path):
    folder = tmp_path / "ops"
    folder.mkdir()
    (folder / "hashes.json").write_text('{"op": "x"}', encoding="utf-8")
    assert not tool.verify(folder)["ok"]
    assert not tool.verify(tmp_path / "없음")["ok"]


def test_bind_writes_run_copy_only(tmp_path, good):
    before = {p.name: p.read_bytes() for p in good.iterdir()}
    run = tmp_path / "run 01"
    r = tool.bind(good, run, "{DRAWING}", "snap-1")
    assert r["ok"], r["problems"]
    assert {p.name: p.read_bytes() for p in good.iterdir()} == before          # 원본은 그대로
    assert r["target"] == {"drawing_id": "{DRAWING}", "snapshot_id": "snap-1"}
    bound = json.loads((run / "a-walls.json").read_text(encoding="utf-8"))
    assert bound["items"][0]["start"][0] == 0 and str(bound["items"][0]["start"][0]) == "-0.0"   # -0.0 유지
    new = json.loads((run / "hashes.json").read_text(encoding="utf-8"))
    old = json.loads((good / "hashes.json").read_text(encoding="utf-8"))
    assert [n["op"] for n in new] == [o["op"] for o in old]
    assert all(n["spec_hash"] != o["spec_hash"] for n, o in zip(new, old))     # target은 해시에 들어간다
    assert new[0]["spec_hash"] == spec_hash(parse_spec(bound))
    record = json.loads((run / "bind.json").read_text(encoding="utf-8"))
    assert record["source_hashes"] == [{"op": o["op"], "spec_hash": o["spec_hash"]} for o in old]
    assert tool.verify(run)["ok"]                                               # bind.json은 목록 밖 파일이 아님

    again = tool.bind(good, run, "{DRAWING}", "snap-2")                         # 이미 묶인 사본은 다시 쓰지 않음
    assert not again["ok"] and "비어 있지 않음" in again["problems"][0]
    assert json.loads((run / "hashes.json").read_text(encoding="utf-8")) == new


def test_bind_refuses_failed_source_and_same_folder(tmp_path, good):
    listed = json.loads((good / "hashes.json").read_text(encoding="utf-8"))
    listed[0]["spec_hash"] = "0" * 16
    (good / "hashes.json").write_text(json.dumps(listed), encoding="utf-8")
    r = tool.bind(good, tmp_path / "run", "D", "S")
    assert not r["ok"] and r["problems"][0] == "원본 verify 실패"
    assert not (tmp_path / "run").exists()
    empty = tmp_path / "same"
    empty.mkdir()
    assert not tool.bind(empty, empty, "D", "S")["ok"]


def test_cli_exit_codes(good, tmp_path, capsys):
    assert tool.main(["verify", str(good)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]
    (good / "a-walls.json").unlink()
    assert tool.main(["verify", str(good)]) == 1
    assert not json.loads(capsys.readouterr().out)["ok"]
