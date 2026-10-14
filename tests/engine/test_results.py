"""엔진 공통 결과 형식(D2) 테스트"""
import pytest
from pydantic import ValidationError

from src.engine.results import (Applied, Assumption, Checked, EngineResult, OutOfScope, ResultIds,
                                RESULT_SCHEMA_VERSION, Scope, Status, Unchecked, Violation)

D2_FIELDS = {"status", "summary", "scope", "checked", "violations", "applied",
             "unchecked", "out_of_scope", "assumptions", "ids"}


def full_result(**kw):
    base = dict(
        status=Status.SUCCESS, summary="위반 1건",
        scope=Scope(target="snap-001", supported=["layer", "color"]),
        checked=[Checked(item="entities", count=120)],
        violations=[Violation(code="LAYER_NOT_STANDARD", message="비표준 레이어 A-CEN", handle="2F1",
                              location=[100.0, 200.0], current="A-CEN", expected="CEN", fixable=True)],
        applied=[Applied(action="set_layer", count=1, handles=["2F1"])],
        unchecked=[], out_of_scope=[OutOfScope(item="walls", reason="0단계 범위 밖")],
        assumptions=[Assumption(name="scale", value=100, reason="요청에 축척 없음")],
        ids=ResultIds(request_id="op-1", drawing_id="dwg-1", snapshot_id="snap-001", standard="heo-2026@1"),
    )
    base.update(kw)
    return EngineResult(**base)


def test_d2_fields_present_in_schema():
    assert D2_FIELDS <= set(EngineResult.model_json_schema()["properties"])


def test_status_values_match_d2():
    assert {s.value for s in Status} == {"success", "conflict", "failed", "recovered", "unverified"}


def test_json_round_trip():
    r = full_result()
    assert EngineResult.model_validate_json(r.model_dump_json()) == r
    assert r.ids.schema_version == RESULT_SCHEMA_VERSION


def test_unknown_status_and_extra_fields_rejected():
    with pytest.raises(ValidationError):
        full_result(status="done")
    with pytest.raises(ValidationError):
        EngineResult(status="success", summary="", scope=Scope(target="x"), extra_field=1)


def test_complete_only_when_success_and_nothing_unchecked():
    assert full_result().is_complete()
    assert not full_result(unchecked=[Unchecked(item="dimstyle", reason="스냅샷에 없음")]).is_complete()
    assert not full_result(status=Status.CONFLICT).is_complete()


def test_out_of_scope_does_not_block_completion():
    assert full_result(out_of_scope=[OutOfScope(item="layouts", reason="범위 밖")]).is_complete()
