"""도면 스냅샷(DXF 사본)의 메타·작업 폴더·닫힌 파일 캐시(구조 결정 D4·D6)

스냅샷은 CAD가 내보낸 읽기용 DXF 사본이다. 이 모듈은 CAD를 모른다(내보내기는 MCP 쪽이 한다).
- 사본과 메타는 <작업 폴더>/snapshots/<스냅샷 ID>.dxf / .json에 둔다
- 열린 도면은 매번 새로 내보낸다. 닫힌 파일만 경로·수정 시각·크기·사본 해시가 같으면 이전 사본을 다시 쓴다(읽기 전용)
"""
import hashlib
import json
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional

import ezdxf
from pydantic import BaseModel, ConfigDict, Field

from .results import EngineResult

INDEX_NAME = "index.json"


class SnapshotError(Exception):
    """작업 폴더나 스냅샷을 쓸 수 없거나 찾지 못했다"""


class SnapshotMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    method: Literal["open_document", "closed_file"]
    cached: bool = False
    source_name: str
    source_path: str
    source_saved: Optional[bool] = None
    source_mtime: Optional[float] = None     # 닫힌 파일만
    source_size: Optional[int] = None        # 닫힌 파일만
    exported_at: str
    dxf_path: str
    dxf_sha256: str
    dxfversion: str
    insunits: int
    drawing_id: Optional[str] = None         # $FINGERPRINTGUID(파일을 복사하면 같은 값이 된다)


class SnapshotResult(EngineResult):
    """export_snapshot 결과: D2 공통 항목 + 스냅샷 메타"""
    snapshot: Optional[SnapshotMeta] = None
    warnings: List[str] = Field(default_factory=list)


def snapshots_dir(work: Path) -> Path:
    return Path(work) / "snapshots"


def check_work_dir(path: Path) -> list:
    """작업 폴더를 만들고 쓸 수 있는지 확인한다. 쓸 수 없으면 SnapshotError, 한글·공백 경로는 경고 문자열"""
    path = Path(path)
    try:
        snapshots_dir(path).mkdir(parents=True, exist_ok=True)
        probe = snapshots_dir(path) / f".probe-{secrets.token_hex(4)}"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        raise SnapshotError(f"작업 폴더를 쓸 수 없음: {path} ({e})") from e
    warnings = []
    text = str(path.resolve())
    if not text.isascii():
        warnings.append(f"작업 폴더 경로에 한글 등 ASCII가 아닌 문자가 있음(동작은 확인됐지만 영문 경로 권장): {text}")
    if " " in text:
        warnings.append(f"작업 폴더 경로에 공백이 있음(명령행 경로는 따옴표가 필요): {text}")
    return warnings


def new_snapshot_id(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    return f"snap-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _meta_path(work: Path, snapshot_id: str) -> Path:
    return snapshots_dir(work) / f"{snapshot_id}.json"


def _index_path(work: Path) -> Path:
    return snapshots_dir(work) / INDEX_NAME


def _key(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _read_index(work: Path) -> dict:
    p = _index_path(work)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def finalize(work: Path, snapshot_id: str, dxf_path: Path, **source) -> SnapshotMeta:
    """내보낸 DXF의 헤더(버전·단위·도면 ID)와 해시를 채워 메타를 저장한다. 닫힌 파일이면 캐시 색인에 넣는다"""
    dxf_path = Path(dxf_path)
    if not dxf_path.exists():
        raise SnapshotError(f"스냅샷 파일이 없음: {dxf_path}")
    doc = ezdxf.readfile(str(dxf_path))
    meta = SnapshotMeta(snapshot_id=snapshot_id, exported_at=datetime.now().isoformat(timespec="seconds"),
                        dxf_path=str(dxf_path), dxf_sha256=_sha256(dxf_path), dxfversion=doc.dxfversion,
                        insunits=int(doc.header.get("$INSUNITS", 0)),
                        drawing_id=doc.header.get("$FINGERPRINTGUID"), **source)
    _meta_path(work, snapshot_id).write_text(meta.model_dump_json(indent=1), encoding="utf-8")
    if meta.method == "closed_file":
        index = _read_index(work)
        index[_key(meta.source_path)] = snapshot_id
        _index_path(work).write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def load_meta(work: Path, snapshot_id: str) -> SnapshotMeta:
    p = _meta_path(work, snapshot_id)
    if not p.exists():
        raise SnapshotError(f"스냅샷이 없음: {snapshot_id}")
    return SnapshotMeta.model_validate_json(p.read_text(encoding="utf-8"))


def find_cached(work: Path, source_path: Path) -> Optional[SnapshotMeta]:
    """닫힌 파일의 이전 사본: 경로·수정 시각·크기가 같고 사본이 그대로 있을 때만 돌려준다(cached=True)"""
    source_path = Path(source_path)
    sid = _read_index(work).get(_key(source_path))
    if not sid or not source_path.exists():
        return None
    try:
        meta = load_meta(work, sid)
    except (SnapshotError, ValueError):
        return None
    st = source_path.stat()
    if meta.source_mtime != st.st_mtime or meta.source_size != st.st_size:
        return None
    dxf = Path(meta.dxf_path)
    if not dxf.exists() or _sha256(dxf) != meta.dxf_sha256:
        return None
    return meta.model_copy(update={"cached": True})
