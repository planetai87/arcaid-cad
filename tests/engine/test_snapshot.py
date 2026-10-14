"""스냅샷 메타·작업 폴더·닫힌 파일 캐시(src/engine/snapshot.py) 테스트"""
import os
import re
from datetime import datetime
from pathlib import Path

import ezdxf
import pytest

from src.engine.snapshot import (SnapshotError, check_work_dir, finalize, find_cached, load_meta, new_snapshot_id,
                                 snapshots_dir)


def make_dxf(path: Path, insunits: int = 4) -> Path:
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = insunits
    doc.modelspace().add_line((0, 0), (1000, 0))
    doc.saveas(str(path))
    return path


def make_source(tmp_path: Path, content: bytes = b"dwg-bytes") -> Path:
    src = tmp_path / "src" / "plan.dwg"
    src.parent.mkdir(exist_ok=True)
    src.write_bytes(content)
    return src


def test_snapshot_id_format_and_uniqueness():
    now = datetime(2026, 10, 14, 15, 30, 12)
    a, b = new_snapshot_id(now), new_snapshot_id(now)
    assert re.fullmatch(r"snap-20261014-153012-[0-9a-f]{6}", a)
    assert a != b


def test_work_dir_created_and_warnings(tmp_path):
    work = tmp_path / "work"
    assert check_work_dir(work) == []
    assert work.is_dir() and snapshots_dir(work).is_dir()
    warnings = check_work_dir(tmp_path / "한글 폴더")
    assert any("한글" in w for w in warnings) and any("공백" in w for w in warnings)


def test_unwritable_work_dir_is_an_error(tmp_path):
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    with pytest.raises(SnapshotError):
        check_work_dir(blocker / "work")   # 파일 아래에는 폴더를 만들 수 없다


def test_finalize_reads_header_and_round_trips(tmp_path):
    work = tmp_path / "work"
    check_work_dir(work)
    sid = new_snapshot_id()
    dxf = make_dxf(snapshots_dir(work) / f"{sid}.dxf", insunits=6)
    meta = finalize(work, sid, dxf, method="open_document", source_name="plan.dwg",
                    source_path="D:/p/plan.dwg", source_saved=False)
    assert meta.dxfversion == "AC1027" and meta.insunits == 6
    assert meta.drawing_id and meta.drawing_id.startswith("{")
    assert len(meta.dxf_sha256) == 64 and meta.cached is False
    assert load_meta(work, sid) == meta


def test_load_unknown_snapshot_is_an_error(tmp_path):
    check_work_dir(tmp_path)
    with pytest.raises(SnapshotError):
        load_meta(tmp_path, "snap-없음")


def _closed_snapshot(work: Path, src: Path):
    sid = new_snapshot_id()
    dxf = make_dxf(snapshots_dir(work) / f"{sid}.dxf")
    st = src.stat()
    return finalize(work, sid, dxf, method="closed_file", source_name=src.name, source_path=str(src),
                    source_mtime=st.st_mtime, source_size=st.st_size)


def test_cache_hit_for_unchanged_closed_file(tmp_path):
    work = tmp_path / "work"
    check_work_dir(work)
    src = make_source(tmp_path)
    meta = _closed_snapshot(work, src)
    hit = find_cached(work, src)
    assert hit is not None and hit.snapshot_id == meta.snapshot_id and hit.cached is True


def test_cache_miss_when_source_changed(tmp_path):
    work = tmp_path / "work"
    check_work_dir(work)
    src = make_source(tmp_path)
    _closed_snapshot(work, src)
    src.write_bytes(b"dwg-bytes-changed")          # 크기가 바뀜
    assert find_cached(work, src) is None
    src.write_bytes(b"dwg-bytes-CHANGED")          # 크기는 같고 수정 시각만 바뀜
    st = src.stat()
    os.utime(src, (st.st_atime, st.st_mtime + 10))
    assert find_cached(work, src) is None


def test_cache_miss_when_snapshot_copy_missing_or_altered(tmp_path):
    work = tmp_path / "work"
    check_work_dir(work)
    src = make_source(tmp_path)
    meta = _closed_snapshot(work, src)
    Path(meta.dxf_path).write_text("altered", encoding="utf-8")
    assert find_cached(work, src) is None
    Path(meta.dxf_path).unlink()
    assert find_cached(work, src) is None


def test_open_document_snapshots_are_not_cached(tmp_path):
    work = tmp_path / "work"
    check_work_dir(work)
    src = make_source(tmp_path)
    sid = new_snapshot_id()
    dxf = make_dxf(snapshots_dir(work) / f"{sid}.dxf")
    finalize(work, sid, dxf, method="open_document", source_name=src.name, source_path=str(src))
    assert find_cached(work, src) is None
