"""T10 실무 사본 전용. 기준 manifest의 사람 확인 후에만 명시적으로 실행한다.

ARCAID_CAD_TESTS=1, ARCAID_T10_PREPARED=<로컬 준비 폴더>,
ARCAID_T10_APPROVED_MANIFEST_SHA256=<사용자가 확인한 manifest 해시> 필요.
pytest --basetemp는 C:/arcaid/work 아래의 존재하지 않는 새 경로를 지정한다.
"""
import copy
import json
import os
from pathlib import Path
import time
import uuid

import ezdxf
import pytest

from src import apply_service
from src.engine.check import check_standard
from src.engine.read import read_drawing
from src.engine.results import Status
from src.engine.standards import load_standard
from src.engine.state import arcaid_tags
from src.snapshot_service import snapshot_active
from tools.acceptance.t10_dangsan import (compare_documents, file_identity, room_counts,
                                         verify_geometry, write_json)


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _user_state(ctl):
    return {d["full_name"] or d["name"]: (d["saved"], d["is_active"]) for d in ctl.list_drawings()}


def _handles(doc):
    return {e.dxf.handle for e in doc.modelspace()}


def _tagged(doc, op):
    out = {}
    for e in doc.modelspace():
        tag = arcaid_tags(e)
        if tag and tag["op"] == op:
            assert tag["path"] not in out, "의미 경로 중복"
            out[tag["path"]] = e
    return out


@pytest.fixture
def prepared(tmp_path):
    folder = os.environ.get("ARCAID_T10_PREPARED")
    approved = os.environ.get("ARCAID_T10_APPROVED_MANIFEST_SHA256")
    if not folder or not approved:
        pytest.skip("실제 T10 입력·사람 확인 해시를 지정하지 않음")
    path = Path(folder).resolve(); manifest = _load(path / "manifest.json")
    assert file_identity(path / "manifest.json")["sha256"] == approved
    assert manifest["baseline_ready"]
    for name, digest in manifest["artifact_hashes"].items():
        assert file_identity(path / name)["sha256"] == digest, f"확인 뒤 입력 변경: {name}"
    assert tmp_path.resolve().is_relative_to(Path("C:/arcaid/work").resolve())
    for key in ("source_file", "reference_file"):
        assert file_identity(manifest[key]["path"]) == manifest[key]
    return path, manifest


def _snap(ctl, work, evidence):
    meta, warnings = snapshot_active(ctl, work)
    evidence.setdefault("warnings", []).extend(warnings)
    evidence.setdefault("snapshots", []).append(meta.model_dump(mode="json"))
    return meta, ezdxf.readfile(meta.dxf_path)


def _open_copy(ctl, source_dxf, dest, unique_id=True):
    assert not dest.exists()
    doc = ezdxf.readfile(source_dxf)
    if unique_id:
        doc.header["$FINGERPRINTGUID"] = "{" + str(uuid.uuid4()).upper() + "}"
    dxf = dest.with_suffix(".dxf"); doc.saveas(dxf)
    assert ctl.open_drawing(str(dxf))
    assert ctl.save_drawing(str(dest))
    return ctl.doc


def test_real_dangsan_reproduction_and_all_protection_gates(prepared, cad, tmp_path):
    """큰 실제 명세의 결정성·재전송·독립 충돌·복구를 모두 검증한다."""
    path, manifest = prepared
    ctl, _ = cad; std = load_standard(); work = tmp_path / "work"
    user_before = _user_state(ctl)
    evidence = {"approved_manifest": file_identity(path / "manifest.json"), "status": "running",
                "copies": {}, "conflicts": {}, "user_before": user_before}
    results, specs, documents = {}, {}, {}
    assert apply_service.GUARD.reason is None, "이전 확인 불가 상태는 사람 확인 없이 해제하지 않음"
    try:
        for label in ("A", "B"):
            dest = tmp_path / f"dangsan-{label}.dwg"
            _open_copy(ctl, path / "baseline.dxf", dest)
            meta, baseline = _snap(ctl, work, evidence)
            read_start = time.perf_counter()
            read = read_drawing(Path(meta.dxf_path), std)
            assert read.status is Status.SUCCESS and len(read.grid) == 20
            checked = check_standard(Path(meta.dxf_path), std, 200)
            assert not checked.violations and not checked.unchecked
            assert compare_documents(ezdxf.readfile(path / "baseline.dxf"), baseline)["equal"]
            evidence["copies"][label] = {"drawing_id": meta.drawing_id, "read_seconds": time.perf_counter() - read_start}
            specs[label] = {}
            for op in ("t10-rooms", "t10-annotations"):
                spec = _load(path / f"{op}.json")
                spec["target"] = {"drawing_id": meta.drawing_id, "snapshot_id": meta.snapshot_id}
                specs[label][op] = spec
                started = time.perf_counter()
                result = apply_service.draw_batch(ctl, work, std, spec)
                evidence["copies"][label][op] = {"seconds": time.perf_counter() - started,
                                                        "result": result.model_dump(mode="json")}
                assert result.status is Status.SUCCESS, result.summary
                assert result.applied[0].count == manifest["specs"][op]["objects"]
                assert not result.new_violations and not result.unchecked
            final_meta, doc = _snap(ctl, work, evidence)
            assert room_counts(doc) == {"A": 25, "B": 2}
            for key, op in (("room_expectation", "t10-rooms"), ("annotation_expectation", "t10-annotations")):
                proof = verify_geometry(manifest[key], _tagged(doc, op))
                evidence["copies"][label][key] = proof
                assert proof["passed"], proof
            # 원래 코어·축의 형상과 속성은 추가 작도 후에도 동일해야 한다.
            remaining = ezdxf.new()
            for e in doc.modelspace():
                if not arcaid_tags(e):
                    remaining.modelspace().add_entity(e.copy())
            assert compare_documents(baseline, remaining)["equal"]
            started = time.perf_counter()
            check = check_standard(Path(final_meta.dxf_path), std, 200)
            evidence["copies"][label]["check"] = check.model_dump(mode="json")
            evidence["copies"][label]["check_seconds"] = time.perf_counter() - started
            assert not check.violations and not check.unchecked
            # 같은 op와 정확히 같은 명세로 재전송한다.
            for op, spec in specs[label].items():
                started = time.perf_counter(); replay = apply_service.draw_batch(ctl, work, std, spec)
                evidence["copies"][label][op + "-replay"] = {
                    "seconds": time.perf_counter() - started, "result": replay.model_dump(mode="json")}
                assert replay.status is Status.SUCCESS and replay.meta.decision == "skip"
            _, after = _snap(ctl, work, evidence)
            assert _handles(after) == _handles(doc)
            assert compare_documents(doc, after)["equal"]
            assert ctl.save_drawing(str(dest))
            assert ctl.zoom_extents()
            (tmp_path / f"dangsan-{label}.png").write_bytes(ctl.screenshot())
            documents[label] = doc; results[label] = Path(final_meta.dxf_path)
            assert ctl.close_drawing(False)
        assert evidence["copies"]["A"]["drawing_id"] != evidence["copies"]["B"]["drawing_id"]
        evidence["determinism"] = compare_documents(documents["A"], documents["B"])
        assert evidence["determinism"]["equal"]

        # 각 충돌은 완료 사본의 별도 복제에서 고장 하나만 주입한다.
        for mode in ("different-input", "partial-delete", "manual-text", "manual-layer", "manual-bubble"):
            # 이 고장 사본들은 원래 op의 target·입력 해시를 보존한다. 독립 A/B와 별개다.
            _open_copy(ctl, results["A"], tmp_path / f"conflict-{mode}.dwg", unique_id=False)
            _, doc = _snap(ctl, work, evidence)
            op = "t10-rooms" if mode in ("different-input", "partial-delete", "manual-layer") else "t10-annotations"
            spec = copy.deepcopy(specs["A"][op])
            tagged = _tagged(doc, op)
            if mode == "different-input":
                spec["items"][0]["place"][0]["at"][0] += 1
            elif mode == "partial-delete":
                assert ctl.delete_handles([next(iter(tagged.values())).dxf.handle])[0] == 1
            elif mode == "manual-layer":
                e = next(e for e in tagged.values() if e.dxf.layer != "CON")
                ctl.set_props(e.dxf.handle, layer="CON")
            elif mode == "manual-text":
                e = next(e for e in tagged.values() if e.dxftype() == "TEXT")
                obj = ctl._call_attr(ctl.doc, "HandleToObject", e.dxf.handle)
                ctl._call(setattr, obj, "TextString", "T10 시험 수동 변경")
            else:
                e = next(e for e in tagged.values() if e.dxftype() == "INSERT")
                obj = ctl._call_attr(ctl.doc, "HandleToObject", e.dxf.handle)
                attrs = ctl._call_attr(obj, "GetAttributes")
                ctl._call(setattr, attrs[0], "TextString", "X99")
            _, before = _snap(ctl, work, evidence)
            result = apply_service.draw_batch(ctl, work, std, spec)
            evidence["conflicts"][mode] = result.model_dump(mode="json")
            assert result.status is Status.CONFLICT and result.meta.decision == "conflict", result.summary
            _, after = _snap(ctl, work, evidence)
            assert _handles(before) == _handles(after) and compare_documents(before, after)["equal"]
            assert ctl.close_drawing(False)

        _open_copy(ctl, path / "baseline.dxf", tmp_path / "failure.dwg")
        meta, before = _snap(ctl, work, evidence)
        failure_spec = _load(path / "t10-rooms.json")
        failure_spec["target"] = {"drawing_id": meta.drawing_id, "snapshot_id": meta.snapshot_id}
        def fail_after_change(_):
            raise RuntimeError("T10 시험용 Import 직후 실패")
        recovered = apply_service.draw_batch(ctl, work, std, failure_spec,
                                              hooks=apply_service.Hooks(after_change=fail_after_change))
        evidence["recovery"] = recovered.model_dump(mode="json")
        assert recovered.status is Status.RECOVERED, recovered.summary
        _, after = _snap(ctl, work, evidence)
        assert _handles(before) == _handles(after) and compare_documents(before, after)["equal"]
        assert not _tagged(after, "t10-rooms") and apply_service.GUARD.reason is None
        assert ctl.close_drawing(False)
        evidence["status"] = "geometry_and_protection_passed"
    finally:
        # conftest.cad가 이 시험 tmp_path 문서만 정리하고 원래 활성 도면을 복원한다.
        from _cadutil import close_work_docs
        close_work_docs(ctl, tmp_path)
        active = next((k for k, v in user_before.items() if v[1]), None)
        if active:
            assert ctl.switch_drawing(active)
        evidence["user_after"] = _user_state(ctl)
        evidence["original_files_unchanged"] = all(file_identity(manifest[k]["path"]) == manifest[k]
                                                    for k in ("source_file", "reference_file"))
        if (evidence["status"] == "geometry_and_protection_passed" and evidence["user_after"] == user_before
                and evidence["original_files_unchanged"]):
            evidence["status"] = "passed"
        write_json(tmp_path / "t10-evidence.json", evidence)
    assert evidence["user_after"] == user_before
    assert evidence["original_files_unchanged"]
