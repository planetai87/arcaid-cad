"""메모의 저장·충돌·경계·CLI·실시간 알림을 검증한다."""
import asyncio
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.testclient import TestClient
from src.ui.server import create_app, change_events
from src.ui.workspace import Workspace


def client(work):
    return TestClient(create_app(work))


def create(c, rev=0, **kwargs):
    return c.post('/api/notes', json={'rev': rev, 'text': '폭 1,700으로',
                                    'target': {'kind': 'point', 'point': [10, 20]}, **kwargs})


def test_create_reply_status_and_conflict(tmp_path):
    c = client(tmp_path)
    assert c.get('/api/notes').json() == {'version': 1, 'rev': 0, 'notes': []}
    response = create(c)
    assert response.status_code == 200
    data = response.json()
    assert data['rev'] == 1
    note = data['notes'][0]
    assert note['id'] == 'n1' and note['author'] == 'user' and note['status'] == 'open'
    assert note['created'] and note['replies'] == []
    response = c.post('/api/notes/n1', json={'rev': 1, 'reply': '확인했습니다', 'status': 'answered'})
    assert response.status_code == 200
    assert response.json()['notes'][0]['replies'][0]['author'] == 'user'
    stale = create(c, rev=1)
    assert stale.status_code == 409 and stale.json() == response.json()
    for rev, status in [(2, 'resolved'), (3, 'open')]:
        result = c.post('/api/notes/n1', json={'rev': rev, 'status': status, 'text': '새 본문'})
        assert result.status_code == 200
        assert result.json()['notes'][0]['status'] == status
    assert create(c, rev=4).json()['notes'][1]['id'] == 'n2'
    assert sorted(p.name for p in (tmp_path / 'review').iterdir()) == ['notes.json']


def test_spec_base_and_area_targets(tmp_path):
    c = client(tmp_path)
    target = {'kind': 'spec', 'file': 'ops/a.json', 'oid': 'o1', 'path': 'items[0]',
              'sources': [{'item': 'items[0]', 'instance': [1, 2]}], 'layer': 'WAL1', 'point': [1, 2]}
    assert create(c, target=target).json()['notes'][0]['target'] == target
    base = {'kind': 'base', 'file': '@base.dxf', 'handle': 'AB', 'layer': 'WAL', 'point': [3, 4]}
    assert create(c, rev=1, target=base).status_code == 200
    area = {'kind': 'area', 'window': [0, 0, 100, 200],
            'specs': [{'file': 'a.json', 'oid': str(i)} for i in range(200)],
            'bases': [{'file': '@base.dxf', 'handle': str(i)} for i in range(200)]}
    assert create(c, rev=2, target=area).status_code == 200
    area['specs'].append({'file': 'a.json', 'oid': '201'})
    assert create(c, rev=3, target=area).status_code == 400
    assert c.get('/api/notes').json()['rev'] == 3


@pytest.mark.parametrize('patch', [
    {'rev': True}, {'rev': -1}, {'text': ''}, {'text': 1},
    {'target': {'kind': 'point', 'point': [1]}},
    {'target': {'kind': 'area', 'window': [10, 0, 0, 20], 'specs': [], 'bases': []}},
    {'target': {'kind': 'base', 'file': '../outside.dxf', 'handle': 'A', 'layer': 'WAL', 'point': [0, 0]}},
    {'target': {'kind': 'spec', 'file': 'C:/outside.json', 'oid': '1', 'path': 'x', 'sources': [], 'layer': 'WAL', 'point': [0, 0]}},
])
def test_invalid_request_does_not_write(tmp_path, patch):
    assert create(client(tmp_path), **patch).status_code == 400
    assert not (tmp_path / 'review' / 'notes.json').exists()


def test_invalid_update_and_missing_id(tmp_path):
    c = client(tmp_path)
    create(c)
    assert c.post('/api/notes/n1', json={'rev': 1, 'status': 'bad'}).status_code == 400
    assert c.post('/api/notes/missing', json={'rev': 1, 'status': 'resolved'}).status_code == 404
    assert c.post('/api/notes', content='{').status_code == 400
    assert c.get('/api/notes').json()['rev'] == 1


def test_atomic_replace_failure_cleans_temp(tmp_path, monkeypatch):
    from src.ui.notes import NotesStore
    store = NotesStore(tmp_path)
    store.create(0, '원본', {'kind': 'point', 'point': [0, 0]})
    before = (tmp_path / 'review/notes.json').read_bytes()
    def fail(*args):
        raise OSError('저장 실패')
    monkeypatch.setattr('src.ui.notes.os.replace', fail)
    with pytest.raises(OSError):
        store.update(1, 'n1', status='resolved')
    assert (tmp_path / 'review/notes.json').read_bytes() == before
    assert [p.name for p in (tmp_path / 'review').iterdir()] == ['notes.json']


def test_outside_review_symlink_is_rejected(tmp_path):
    work, outside = tmp_path / 'work', tmp_path / 'outside'
    work.mkdir()
    outside.mkdir()
    try:
        (work / 'review').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('심볼릭 링크 권한 없음')
    assert create(client(work)).status_code == 403
    assert list(outside.iterdir()) == []


def test_parallel_revision_writes_have_one_winner(tmp_path):
    from src.ui.notes import NotesStore, RevisionConflict
    def save(i):
        try:
            return NotesStore(tmp_path).create(0, str(i), {'kind': 'point', 'point': [i, 0]})['rev']
        except RevisionConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, [1, 2]), key=str) == [1, 'conflict']


def test_cli_list_reply_resolve(tmp_path, capsys):
    from src.ui.notes import main
    c = client(tmp_path)
    target = {'kind': 'spec', 'file': 'a.json', 'oid': 'o1', 'path': 'g/1',
              'sources': [{'item': 'items[3]'}], 'layer': 'WAL', 'point': [10, 20]}
    create(c, target=target)
    prefix = ['--work', str(tmp_path)]
    assert main(prefix + ['list', '--open']) == 0
    output = capsys.readouterr().out
    assert all(s in output for s in ['n1', 'a.json', 'o1', 'g/1', 'items[3]', '10', '20'])
    assert main(prefix + ['reply', 'n1', '반영했습니다', '--status', 'answered']) == 0
    capsys.readouterr()
    note = c.get('/api/notes').json()['notes'][0]
    assert note['replies'][0]['author'] == 'claude' and note['status'] == 'answered'
    assert main(prefix + ['resolve', 'n1']) == 0
    capsys.readouterr()
    assert main(prefix + ['list', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['notes'][0]['status'] == 'resolved'
    assert main(prefix + ['list', '--open', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['notes'] == []


def test_notes_excluded_from_specs_and_sse_notifies(tmp_path):
    workspace = Workspace(tmp_path)
    async def check():
        events = change_events(workspace, interval=0.01)
        assert 'connected' in await anext(events)
        assert create(client(tmp_path)).status_code == 200
        event = await asyncio.wait_for(anext(events), 2)
        assert json.loads(event.split('data: ')[1])['path'] == 'review/notes.json'
        await events.aclose()
    asyncio.run(check())
    # 잘못된 내용이어도 메모 저장소를 명세로 표시하지 않는다.
    (tmp_path / 'review/notes.json').write_text('{', encoding='utf8')
    assert workspace.discover()['specs'] == []


def test_snapshot_reuses_unchanged_large_file_hash(tmp_path, monkeypatch):
    path = tmp_path / 'large.dxf'
    path.write_bytes(b'x' * 2_000_000)
    workspace = Workspace(tmp_path)
    reads = []
    original = Path.read_bytes
    def counted(p):
        reads.append(p)
        return original(p)
    monkeypatch.setattr(Path, 'read_bytes', counted)
    first = workspace.snapshot()
    assert workspace.snapshot() == first
    assert reads.count(path) == 1
    path.write_bytes(b'y' * 2_000_001)
    assert workspace.snapshot()['large.dxf']['sha256'] != first['large.dxf']['sha256']
    assert reads.count(path) == 2


def test_base_area_cap_and_external_notes_link(tmp_path):
    c = client(tmp_path)
    target = {'kind': 'area', 'window': [0, 0, 10, 10], 'specs': [],
              'bases': [{'file': '@base.dxf', 'handle': str(i)} for i in range(201)]}
    assert create(c, target=target).status_code == 400
    work, outside = tmp_path / 'work', tmp_path / 'outside.json'
    work.mkdir()
    (work / 'review').mkdir()
    outside.write_text('{"보호": true}', encoding='utf8')
    before = outside.read_bytes()
    try:
        (work / 'review/notes.json').symlink_to(outside)
    except OSError:
        pytest.skip('심볼릭 링크 권한 없음')
    assert create(client(work)).status_code == 403
    assert outside.read_bytes() == before


def test_external_reply_and_status_emit_sse(tmp_path):
    from src.ui.notes import main
    c = client(tmp_path)
    create(c)
    async def check():
        events = change_events(Workspace(tmp_path), interval=0.01)
        await anext(events)
        assert main(['--work', str(tmp_path), 'reply', 'n1', '답글', '--status', 'answered']) == 0
        message = await asyncio.wait_for(anext(events), 2)
        assert json.loads(message.split('data: ')[1])['path'] == 'review/notes.json'
        assert c.get('/api/notes').json()['notes'][0]['replies'][0]['text'] == '답글'
        await events.aclose()
    asyncio.run(check())


def test_snapshot_cache_external_base_and_deletion(tmp_path, monkeypatch):
    work = tmp_path / 'work'
    work.mkdir()
    path = tmp_path / 'base.dxf'
    path.write_bytes(b'original')
    workspace = Workspace(work, path)
    reads = []
    original = Path.read_bytes
    def counted(p):
        reads.append(p)
        return original(p)
    monkeypatch.setattr(Path, 'read_bytes', counted)
    before = workspace.snapshot()
    assert workspace.snapshot() == before and reads.count(path) == 1
    path.unlink()
    assert workspace.snapshot() == {}
    path.write_bytes(b'new data!')
    assert workspace.snapshot()['@base.dxf']['sha256'] != before['@base.dxf']['sha256']


def test_stale_lock_is_cleared(tmp_path):
    # 저장 중 강제 종료로 남은 잠금 폴더는 30초가 지나면 치우고 저장한다.
    import os
    import time
    from src.ui.notes import NotesStore, STALE_LOCK_SECONDS
    lock = tmp_path / 'review' / '.notes.lock'
    lock.mkdir(parents=True)
    old = time.time() - STALE_LOCK_SECONDS - 5
    os.utime(lock, (old, old))
    data = NotesStore(tmp_path).create(0, '잠금 뒤 저장', {'kind': 'point', 'point': [0, 0]})
    assert data['rev'] == 1 and not lock.exists()
