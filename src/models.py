"""도구 구조화 출력 모델(MCP Structured Output, 2025-06-18 규격)

모든 도구는 Pydantic 모델을 반환한다. FastMCP가 이것으로 outputSchema를 자동 생성하므로,
클라이언트(Claude, Cursor 등)는 예전처럼 해석하기 어려운 COM 객체 문자열이 아니라
구조화된 structuredContent를 바로 받는다.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """공통 도구 실행 결과"""
    success: bool = Field(description="실행 성공 여부")
    message: str = Field(description="사람이 읽을 수 있는 결과 설명")
    entity_handles: List[str] = Field(
        default_factory=list,
        description="이번 작업으로 생기거나 영향을 받은 객체 핸들 목록. 이후 편집·조회에 쓴다"
    )
    error: Optional[str] = Field(default=None, description="실패 시 오류 정보")


class EntityInfo(BaseModel):
    """객체 요약 정보"""
    handle: str = Field(description="객체 핸들(CAD 안에서 고유한 식별자)")
    entity_type: str = Field(description="객체 종류(line/circle/arc 등)")
    layer: Optional[str] = Field(default=None, description="레이어 이름")
    color: Optional[int] = Field(default=None, description="색상 인덱스(ACI)")


class ListEntitiesResult(BaseModel):
    """객체 목록 조회 결과"""
    success: bool = Field(description="조회 성공 여부")
    message: str = Field(description="결과 설명")
    total: int = Field(default=0, description="모형 공간 객체 총수")
    returned: int = Field(default=0, description="이번에 실제로 돌려준 객체 수(limit 제한)")
    entities: List[EntityInfo] = Field(default_factory=list, description="객체 목록")


class LayerInfo(BaseModel):
    """레이어 정보"""
    name: str = Field(description="레이어 이름")
    color: Optional[int] = Field(default=None, description="레이어 색상 인덱스(ACI)")
    is_current: bool = Field(default=False, description="현재 레이어 여부")
    is_frozen: bool = Field(default=False, description="동결 여부")
    is_locked: bool = Field(default=False, description="잠금 여부")
    is_on: bool = Field(default=True, description="켜짐(보임) 여부")


class ListLayersResult(BaseModel):
    """레이어 목록 조회 결과"""
    success: bool = Field(description="조회 성공 여부")
    message: str = Field(description="결과 설명")
    layers: List[LayerInfo] = Field(default_factory=list, description="레이어 목록")


class EntityPropertiesResult(BaseModel):
    """객체 상세 속성 조회 결과"""
    success: bool = Field(description="조회 성공 여부")
    message: str = Field(description="결과 설명")
    handle: str = Field(default="", description="객체 핸들")
    entity_type: str = Field(default="", description="객체 종류")
    layer: Optional[str] = Field(default=None, description="레이어")
    color: Optional[int] = Field(default=None, description="색상 인덱스(ACI)")
    linetype: Optional[str] = Field(default=None, description="선종류 이름")
    lineweight: Optional[int] = Field(default=None, description="선굵기")
    properties: Dict[str, Any] = Field(
        default_factory=dict,
        description="객체 종류에 따라 다른 기하 속성(좌표, 반지름, 문자 내용 등)"
    )
    error: Optional[str] = Field(default=None, description="실패 시 오류 정보")


class CommandResult(BaseModel):
    """명령행 작업(send_command, cancel_command) 결과"""
    success: bool = Field(description="명령을 보냈는지(cancel_command는 명령이 없는 상태가 됐는지)")
    message: str = Field(description="결과 설명")
    still_active: Optional[bool] = Field(
        default=None,
        description="명령이 아직 진행 중이거나 입력을 기다리는지. None이면 상태를 읽지 못함"
    )
    cmdactive: int = Field(
        default=-1,
        description="CMDACTIVE 값(0 없음, 1 명령, 2 투명 명령, 4 스크립트, 8 대화상자, 32 LISP, -1 읽지 못함)"
    )
    error: Optional[str] = Field(default=None, description="실패 시 오류 정보")


class LispResult(BaseModel):
    """run_lisp 결과"""
    success: bool = Field(description="식을 실행하고 결과를 받았는지")
    message: str = Field(description="결과 설명")
    value: Optional[str] = Field(default=None, description="식의 결과를 prin1으로 쓴 문자열(문자열은 따옴표 포함)")
    cmdactive: int = Field(default=-1, description="실행 뒤 CMDACTIVE 값(-1 읽지 못함)")
    error: Optional[str] = Field(default=None, description="실패 시 오류 정보")


class DrawingInfo(BaseModel):
    """열린 도면 정보"""
    index: int = Field(description="Documents 컬렉션 안의 순서")
    name: str = Field(description="도면 이름(파일 이름)")
    full_name: str = Field(default="", description="전체 경로(한 번도 저장하지 않은 도면은 빈 문자열)")
    is_active: bool = Field(default=False, description="CAD의 활성 도면인지(도구는 활성 도면에 작업한다)")
    saved: Optional[bool] = Field(default=None, description="마지막 저장 이후 수정이 없는지. None이면 읽지 못함")


class ListDrawingsResult(BaseModel):
    """열린 도면 목록 조회 결과"""
    success: bool = Field(description="조회 성공 여부")
    message: str = Field(description="결과 설명")
    drawings: List[DrawingInfo] = Field(default_factory=list, description="열린 도면 목록")


class BlockInfo(BaseModel):
    """블록 정보"""
    name: str = Field(description="블록 이름")
    entity_count: int = Field(default=0, description="블록 안의 객체 수")


class ListBlocksResult(BaseModel):
    """블록 목록 조회 결과"""
    success: bool = Field(description="조회 성공 여부")
    message: str = Field(description="결과 설명")
    blocks: List[BlockInfo] = Field(default_factory=list, description="블록 목록")
