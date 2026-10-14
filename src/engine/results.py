"""엔진 공통 결과 형식(구조 결정 D2)

읽기·검사·적용 결과는 모두 이 모델을 쓰고, MCP와 CLI가 같은 모델을 돌려준다.
status가 success이고 unchecked가 비어 있을 때만 '완료'다. out_of_scope는 완료 판정에 넣지 않는다.
"""
from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

RESULT_SCHEMA_VERSION = "1.0"


class Status(str, Enum):
    """결과 상태"""
    SUCCESS = "success"        # 성공
    CONFLICT = "conflict"      # 충돌(적용 직전 확인 불일치, 재실행 충돌 등)
    FAILED = "failed"          # 실패
    RECOVERED = "recovered"    # 실패했지만 적용 전 상태로 돌아온 것을 확인함
    UNVERIFIED = "unverified"  # 확인 불가(복귀 여부 등을 확인하지 못함)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Scope(_Model):
    target: str = Field(description="이번 요청의 대상(도면 이름, 스냅샷 ID, 파일 경로 등)")
    supported: List[str] = Field(default_factory=list, description="이번 요청이 다루는 항목(객체 종류, 검사 항목)")


class Checked(_Model):
    item: str = Field(description="처리하거나 검사한 항목")
    count: int = Field(ge=0, description="개수")


class Violation(_Model):
    code: str = Field(description="위반 종류 코드(예: LAYER_NOT_STANDARD)")
    message: str = Field(description="사람이 읽는 설명")
    handle: Optional[str] = Field(default=None, description="객체 핸들")
    location: Optional[List[float]] = Field(default=None, min_length=2, max_length=3, description="위치 [x, y(, z)]")
    current: Any = Field(default=None, description="현재 값")
    expected: Any = Field(default=None, description="표준 값(고칠 값)")
    fixable: bool = Field(default=False, description="정답이 정해져 있어 자동으로 고칠 수 있는지")


class Applied(_Model):
    action: str = Field(description="적용한 작업")
    count: int = Field(ge=0, description="개수")
    handles: List[str] = Field(default_factory=list, description="영향을 받은 객체 핸들")


class Unchecked(_Model):
    item: str = Field(description="요청 범위 안에서 처리·검사하지 못한 항목")
    reason: str = Field(description="이유")
    count: Optional[int] = Field(default=None, ge=0, description="개수(알 때만)")


class OutOfScope(_Model):
    item: str = Field(description="처음부터 범위에서 뺀 항목")
    reason: str = Field(description="이유")


class Assumption(_Model):
    name: str = Field(description="가정한 항목")
    value: Any = Field(description="가정한 값")
    reason: str = Field(description="가정한 이유")


class ResultIds(_Model):
    request_id: Optional[str] = Field(default=None, description="요청 ID(op)")
    drawing_id: Optional[str] = Field(default=None, description="도면 ID")
    snapshot_id: Optional[str] = Field(default=None, description="스냅샷 ID")
    standard: Optional[str] = Field(default=None, description="표준 프로파일 ID@버전")
    schema_version: str = Field(default=RESULT_SCHEMA_VERSION, description="결과 스키마 버전")


class EngineResult(_Model):
    """읽기·검사·적용 결과(D2)"""
    status: Status
    summary: str = Field(description="한 줄 요약")
    scope: Scope
    checked: List[Checked] = Field(default_factory=list)
    violations: List[Violation] = Field(default_factory=list)
    applied: List[Applied] = Field(default_factory=list)
    unchecked: List[Unchecked] = Field(default_factory=list)
    out_of_scope: List[OutOfScope] = Field(default_factory=list)
    assumptions: List[Assumption] = Field(default_factory=list)
    ids: ResultIds = Field(default_factory=ResultIds)

    def is_complete(self) -> bool:
        """'완료'로 보고해도 되는지: 성공이고 검사하지 못한 항목이 없을 때만"""
        return self.status is Status.SUCCESS and not self.unchecked
