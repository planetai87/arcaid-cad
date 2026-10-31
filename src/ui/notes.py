"""검토 메모의 원자적 저장과 Claude Code용 명령줄. CAD·명세는 수정하지 않는다."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import sys
import tempfile
import time

STATUSES = ('open', 'answered', 'resolved')
STALE_LOCK_SECONDS = 30


class RevisionConflict(Exception):
    def __init__(self, current):
        super().__init__('메모가 변경되었습니다. 다시 읽고 저장해 주세요')
        self.current = current


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name}은 비어 있지 않은 문자열이어야 합니다')
    return value


def _revision(value):
    if type(value) is not int or value < 0:
        raise ValueError('rev는 0 이상의 정수여야 합니다')


def _coordinates(value, length):
    if (not isinstance(value, list) or len(value) != length
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
        raise ValueError('좌표는 유한한 숫자 목록이어야 합니다')


def _file(value, suffix):
    _text(value, '대상 파일')
    if value == '@base.dxf' and suffix == '.dxf':
        return
    path = Path(value.replace(chr(92), '/'))
    if path.is_absolute() or PureWindowsPath(value).drive or '..' in path.parts:
        raise ValueError('대상 파일은 작업 폴더 안의 상대 경로여야 합니다')
    if path.suffix.lower() != suffix:
        raise ValueError(f'대상 파일은 {suffix}여야 합니다')


def validate_target(target):
    if not isinstance(target, dict):
        raise ValueError('메모 대상이 필요합니다')
    kind = target.get('kind')
    if kind not in ('spec', 'base', 'point', 'area'):
        raise ValueError('지원하지 않는 메모 대상입니다')
    if kind == 'area':
        window = target.get('window')
        _coordinates(window, 4)
        if window[0] > window[2] or window[1] > window[3]:
            raise ValueError('영역 좌표의 최소·최대 순서를 확인해 주세요')
        for key, identifier, suffix in [('specs', 'oid', '.json'), ('bases', 'handle', '.dxf')]:
            rows = target.get(key)
            if not isinstance(rows, list) or len(rows) > 200:
                raise ValueError('영역 대상은 종류별 최대 200개입니다')
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError('영역 대상 형식이 올바르지 않습니다')
                _file(row.get('file'), suffix)
                _text(row.get(identifier), identifier)
    else:
        _coordinates(target.get('point'), 2)
    if kind in ('spec', 'base'):
        _file(target.get('file'), '.json' if kind == 'spec' else '.dxf')
        _text(target.get('layer'), '레이어')
        _text(target.get('oid' if kind == 'spec' else 'handle'), '대상 식별자')
    if kind == 'spec':
        _text(target.get('path'), '명세 경로')
        if not isinstance(target.get('sources'), list) or any(not isinstance(s, dict) for s in target['sources']):
            raise ValueError('명세 sources는 원본 위치 목록이어야 합니다')
    # 임의 sources 안의 비유한 수치도 JSON 파일에 들어가지 않게 한다.
    json.dumps(target, ensure_ascii=False, allow_nan=False)


class NotesStore:
    def __init__(self, work):
        self.root = Path(work).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError('작업 경로는 폴더여야 합니다')

    def _path(self):
        folder = self.root / 'review'
        path = folder / 'notes.json'
        if (not folder.resolve().is_relative_to(self.root)
                or not path.resolve().is_relative_to(self.root)):
            raise PermissionError('작업 폴더 밖에는 메모를 읽거나 쓸 수 없습니다')
        return path

    def read(self):
        path = self._path()
        if not path.exists():
            return {'version': 1, 'rev': 0, 'notes': []}
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if (not isinstance(data, dict) or data.get('version') != 1
                or not isinstance(data.get('notes'), list)):
            raise ValueError('메모 파일은 version 1 형식이어야 합니다')
        _revision(data.get('rev'))
        ids = set()
        for note in data['notes']:
            if not isinstance(note, dict) or not re.fullmatch(r'n[1-9][0-9]*', note.get('id', '')):
                raise ValueError('메모 id가 올바르지 않습니다')
            if note['id'] in ids or note.get('status') not in STATUSES:
                raise ValueError('메모 id 또는 상태가 올바르지 않습니다')
            ids.add(note['id'])
            _text(note.get('text'), '메모 본문')
            _text(note.get('created'), '작성 시각')
            if note.get('author') not in ('user', 'claude') or not isinstance(note.get('replies'), list):
                raise ValueError('메모 작성자·답글 형식이 올바르지 않습니다')
            validate_target(note.get('target'))
            for reply in note['replies']:
                if not isinstance(reply, dict) or reply.get('author') not in ('user', 'claude'):
                    raise ValueError('답글 형식이 올바르지 않습니다')
                _text(reply.get('text'), '답글 본문')
                _text(reply.get('created'), '답글 작성 시각')
        return data

    @contextmanager
    def _locked(self):
        path = self._path()
        path.parent.mkdir(exist_ok=True)
        lock = path.parent / '.notes.lock'
        deadline = time.monotonic() + 5
        while True:
            try:
                lock.mkdir()
                break
            except FileExistsError:
                # 저장 중 강제 종료로 남은 잠금은 30초가 지나면 치운다(정상 저장은 1초 안에 끝난다).
                try:
                    if time.time() - lock.stat().st_mtime > STALE_LOCK_SECONDS:
                        lock.rmdir()
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise OSError('메모 저장 중입니다. 잠시 후 다시 시도해 주세요') from None
                time.sleep(0.02)
        try:
            yield
        finally:
            lock.rmdir()

    def _save(self, data):
        path = self._path()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                             prefix='.notes-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _mutate(self, rev, change):
        _revision(rev)
        with self._locked():
            data = self.read()
            if rev != data['rev']:
                raise RevisionConflict(data)
            change(data)
            data['rev'] += 1
            self._save(data)
            return data

    def create(self, rev, text, target, author='user'):
        _text(text, '메모 본문')
        validate_target(target)
        if author not in ('user', 'claude'):
            raise ValueError('작성자가 올바르지 않습니다')
        def change(data):
            number = max((int(n['id'][1:]) for n in data['notes']), default=0) + 1
            data['notes'].append({'id': f'n{number}', 'created': datetime.now().astimezone().isoformat(),
                                  'author': author, 'status': 'open', 'text': text,
                                  'target': target, 'replies': []})
        return self._mutate(rev, change)

    def update(self, rev, note_id, *, status=None, text=None, reply=None, author='user'):
        if status is not None and status not in STATUSES:
            raise ValueError('상태는 open·answered·resolved 중 하나여야 합니다')
        if text is not None:
            _text(text, '메모 본문')
        if reply is not None:
            _text(reply, '답글 본문')
        if status is None and text is None and reply is None:
            raise ValueError('변경할 본문·상태·답글이 필요합니다')
        if author not in ('user', 'claude'):
            raise ValueError('작성자가 올바르지 않습니다')
        def change(data):
            note = next((n for n in data['notes'] if n['id'] == note_id), None)
            if note is None:
                raise FileNotFoundError('선택한 메모가 없습니다')
            if status is not None:
                note['status'] = status
            if text is not None:
                note['text'] = text
            if reply is not None:
                note['replies'].append({'author': author, 'text': reply,
                                        'created': datetime.now().astimezone().isoformat()})
        return self._mutate(rev, change)


def target_summary(target):
    kind = target['kind']
    if kind == 'spec':
        sources = ', '.join(str(s.get('item', '')) for s in target['sources'])
        label = f"{target['file']} · {target['oid']} · {target['path']} · {sources} · {target['layer']}"
    elif kind == 'base':
        label = f"{target['file']} · handle {target['handle']} · {target['layer']}"
    elif kind == 'area':
        return f"영역 {target['window']} · 명세 {len(target['specs'])}개 · 기준 {len(target['bases'])}개"
    else:
        label = '지점'
    return f"{label} · {target['point']}"


def main(argv=None):
    parser = argparse.ArgumentParser(description='검토 메모 조회·답글·해결')
    parser.add_argument('--work', required=True, help='검토 작업 폴더')
    commands = parser.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('list', help='메모 목록')
    listing.add_argument('--open', action='store_true', help='열린 메모만 표시')
    listing.add_argument('--json', action='store_true', help='JSON 출력')
    reply = commands.add_parser('reply', help='Claude 답글 추가')
    reply.add_argument('id')
    reply.add_argument('text')
    reply.add_argument('--status', choices=('answered', 'resolved'))
    resolve = commands.add_parser('resolve', help='메모 해결')
    resolve.add_argument('id')
    args = parser.parse_args(argv)
    try:
        store = NotesStore(args.work)
        data = store.read()
        if args.command == 'list':
            if args.open:
                data['notes'] = [n for n in data['notes'] if n['status'] == 'open']
            if args.json:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print('번호\t상태\t본문 / 대상')
                for note in data['notes']:
                    print(f"{note['id']}\t{note['status']}\t{note['text'].replace(chr(10), ' ')}")
                    print('  ' + target_summary(note['target']))
        else:
            store.update(data['rev'], args.id, author='claude',
                         status='resolved' if args.command == 'resolve' else args.status,
                         reply=args.text if args.command == 'reply' else None)
            print(f'{args.id}: 저장했습니다')
        return 0
    except (OSError, ValueError, RevisionConflict) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
