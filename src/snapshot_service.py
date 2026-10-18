"""스냅샷 조율(MCP 쪽): CAD 내보내기(cad_controller) + 스냅샷 메타(engine.snapshot)

COM 스레드에서 부른다(server.py의 com()). 판독은 엔진이 CAD 없이 한다.
"""
from pathlib import Path
from typing import List, Tuple

try:
    from .engine.snapshot import SnapshotError, SnapshotMeta, check_work_dir, finalize, find_cached, new_snapshot_id, snapshots_dir
except ImportError:
    from engine.snapshot import SnapshotError, SnapshotMeta, check_work_dir, finalize, find_cached, new_snapshot_id, snapshots_dir


def snapshot_active(ctl, work: Path) -> Tuple[SnapshotMeta, List[str]]:
    """활성 도면을 DXF 사본으로 내보낸다(매번 새로). 내보내기 전후 문서 상태가 다르면 경고"""
    warnings = check_work_dir(work)
    if not ctl.is_running():
        raise SnapshotError("CAD 미연결 또는 열린 도면 없음")
    doc = ctl.doc
    before = ctl.doc_state(doc)
    sid = new_snapshot_id()
    result = ctl.export_dxf(str(snapshots_dir(work) / sid))
    if not result["ok"]:
        raise SnapshotError(f"내보내기 실패: {result['error']}")
    after = ctl.doc_state(doc)
    if after != before:
        if before.get("saved") is True and after == dict(before, saved=False):
            warnings.append("saved만 True→False로 바뀜: B0에서 관찰한 COM Export 부작용과 일치")
        else:
            warnings.append(f"내보내기 전후 문서 상태가 다름: {before} → {after}")
    meta = finalize(work, sid, Path(result["path"]), method="open_document", source_name=before["name"],
                    source_path=before["full_name"] or before["name"], source_saved=before["saved"])
    return meta, warnings


def snapshot_file(ctl, work: Path, file_path: str) -> Tuple[SnapshotMeta, List[str]]:
    """파일을 DXF 사본으로 내보낸다. CAD에 열려 있으면 그 문서를(매번 새로), 닫혀 있으면 이전 사본이 같을 때 다시 쓴다"""
    warnings = check_work_dir(work)
    path = Path(file_path).resolve()
    if not path.exists():
        raise SnapshotError(f"파일이 없음: {path}")
    open_paths = {Path(d["full_name"]).resolve() for d in ctl.list_drawings() if d["full_name"]}
    if path not in open_paths:
        cached = find_cached(work, path)
        if cached is not None:
            return cached, warnings
    stat = path.stat()
    sid = new_snapshot_id()
    result = ctl.export_closed_file(str(path), str(snapshots_dir(work) / sid))
    if not result["ok"]:
        raise SnapshotError(f"내보내기 실패: {result['error']}")
    if result["was_open"]:
        meta = finalize(work, sid, Path(result["path"]), method="open_document", source_name=path.name,
                        source_path=str(path))
    else:
        meta = finalize(work, sid, Path(result["path"]), method="closed_file", source_name=path.name,
                        source_path=str(path), source_saved=True, source_mtime=stat.st_mtime, source_size=stat.st_size)
    return meta, warnings
