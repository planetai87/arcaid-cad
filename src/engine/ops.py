"""MCP·CLI 공용 출력: 검사 결과 감싸기와 반환 상한"""
from .apply import CheckFixResult
from .check import check_standard
from .write import BuildResult

CHECK_LIMIT = 20
MANIFEST_LIMIT = 20


def check_file(dxf, std, k=None, snapshot=None, full_out=None, limit=CHECK_LIMIT) -> CheckFixResult:
    """파일 검사 결과를 MCP와 같은 모델·상한으로 돌려준다. 전체 파일은 엔진이 쓴다"""
    result = check_standard(dxf, std, k=k, snapshot=snapshot, full_out=full_out, limit=limit)
    return CheckFixResult(**result.model_dump())


def limit_build(result: BuildResult, limit=MANIFEST_LIMIT) -> BuildResult:
    """작성 결과의 출력용 복사본만 자른다. 넣기에 쓰는 원본 manifest는 보존한다"""
    out = result.model_copy(deep=True)
    out.manifest = out.manifest[:limit]
    out.truncated = result.truncated or len(result.manifest) > limit
    return out
