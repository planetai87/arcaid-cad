"""원본 JSON 편집과 검증·기록·원자적 교체. CAD와 기준 DXF는 다루지 않는다."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

from src.engine.layout import SourceRef, source_transform
from src.engine.spec import SpecError, parse_coord, parse_spec, spec_hash, spec_violation
from .notes import NotesStore
from .specs import read_spec


class EditConflict(Exception):
    def __init__(self, current):
        super().__init__('명세가 변경되었습니다. 현재 값을 확인하고 다시 수정해 주세요')
        self.current = current


def _json(data):
    return (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf8')


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _tokens(field):
    if not isinstance(field, str) or not re.fullmatch(r'[^.\[\]]+(?:\[\d+\]|\.[^.\[\]]+)*', field):
        raise ValueError('원본 필드 경로가 올바르지 않습니다')
    return [int(i) if i else name for name, i in re.findall(r'([^.\[\]]+)|\[(\d+)\]', field)]


def _item(raw, path):
    if not isinstance(path, str) or not re.fullmatch(r'items\[\d+\](?:\.items\[\d+\])*', path):
        raise ValueError('원본 항목 경로가 올바르지 않습니다')
    value = raw
    for key in _tokens(path):
        value = value[key]
    return value


def _slot(obj, field, index=None, defaults=None):
    tokens = _tokens(field)
    if index is not None:
        if type(index) is not int or index < 0:
            raise ValueError('꼭짓점 번호는 0 이상의 정수여야 합니다')
        tokens.append(index)
    for key in tokens[:-1]:
        if isinstance(obj, dict) and key not in obj and defaults is not None:
            obj[key] = deepcopy(defaults[key])
        obj = obj[key]
        defaults = defaults[key] if defaults is not None else None
    key = tokens[-1]
    return obj, key


def _keep_reference(original, coordinate, grid):
    name, _ = parse_coord(original)
    if name is None:
        # 역변환의 부동소수 오차를 걷어 내고, 정수로 떨어지면 원래 명세처럼 정수로 쓴다(2020.0 → 2020).
        value = round(coordinate, 6)
        return int(value) if value == int(value) else value
    offset = coordinate - grid[name]
    # parse_coord의 참조 문법은 지수 표기를 받지 않는다.
    text = format(abs(offset), '.12f').rstrip('0').rstrip('.')
    return name if not text or float(text) == 0 else name + ('+' if offset > 0 else '-') + text


def _apply(raw, op):
    if not isinstance(op, dict):
        raise ValueError('수정 작업은 객체여야 합니다')
    kind = op.get('op')
    if kind == 'set':
        target = _item(raw, op['target'])
        obj, key = _slot(target, op['field'], op.get('index'))
        old = deepcopy(obj.get(key) if isinstance(obj, dict) else obj[key])
        obj[key] = deepcopy(op['value'])
        return op['target'], op['field'] + (f"[{op['index']}]" if 'index' in op else ''), old, op['value']
    if kind == 'set_place':
        group = _item(raw, op['group'])
        if group['kind'] != 'group' or type(op['place']) is not int or op['place'] < 0:
            raise ValueError('group과 배치 번호를 확인해 주세요')
        from src.engine.spec import Placement
        place = group['place'][op['place']]
        defaults = Placement.model_validate(place).model_dump(mode='json')
        obj, key = _slot(place, op['field'], defaults=defaults)
        old = deepcopy(obj.get(key) if isinstance(obj, dict) else obj[key])
        obj[key] = deepcopy(op['value'])
        return op['group'], f"place[{op['place']}].{op['field']}", old, op['value']
    if kind == 'set_grid':
        if op['axis'] not in ('X', 'Y') or op['name'] not in raw['grid'][op['axis']]:
            raise ValueError('수정할 축이 없습니다')
        axis = raw['grid'][op['axis']]
        old, axis[op['name']] = axis[op['name']], op['value']
        return '', f"grid.{op['axis']}.{op['name']}", old, op['value']
    if kind == 'move_point':
        source = SourceRef.model_validate(op['source'])
        spec = parse_spec(raw)
        world = op['world']
        if not isinstance(world, list) or len(world) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in world):
            raise ValueError('세계 좌표는 유한한 숫자 두 개여야 합니다')
        local = source_transform(spec, source).inverse().pt(world, rounded=False)
        obj, key = _slot(_item(raw, source.item), op['field'], op.get('index'))
        original = obj[key]
        if not isinstance(original, list) or len(original) != 2:
            raise ValueError('이 필드는 점 좌표가 아닙니다')
        point = [_keep_reference(v, n, raw.get('grid', {}).get(a, {})) for v, n, a in zip(original, local, ('X', 'Y'))]
        obj[key] = point
        return source.item, op['field'] + (f"[{op['index']}]" if 'index' in op else ''), original, point
    raise ValueError('지원하지 않는 수정 작업입니다')


class EditStore:
    def __init__(self, workspace):
        self.workspace = workspace
        self.root = workspace.root
        # 메모와 같은 작업 폴더 잠금으로 기록과 명세의 동시 쓰기도 직렬화한다.
        self.lock = NotesStore(self.root)

    def inside(self, path):
        path = Path(path)
        if not path.resolve().is_relative_to(self.root):
            raise PermissionError('작업 폴더 밖에는 수정 기록을 쓰거나 읽을 수 없습니다')
        return path

    def history(self, path):
        relative = path.relative_to(self.root)
        return self.inside(self.root / 'review/history' / relative)

    def _replace_many(self, writes):
        """모든 임시 파일을 준비하고 교체한다. 도중 오류는 이미 바꾼 파일까지 복구한다."""
        temporary, originals, replaced = {}, {}, []
        try:
            for path, content in writes.items():
                self.inside(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                originals[path] = path.read_bytes() if path.exists() else None
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.edit-', suffix='.tmp', delete=False) as stream:
                    temporary[path] = Path(stream.name)
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            for path, temp in temporary.items():
                os.replace(temp, path)
                replaced.append(path)
        except OSError:
            for path in reversed(replaced):
                if originals[path] is None:
                    path.unlink(missing_ok=True)
                else:
                    # 준비한 사본을 새로 만들어 원자적으로 이전 상태를 복구한다.
                    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.edit-', suffix='.tmp', delete=False) as stream:
                        restore = Path(stream.name)
                        stream.write(originals[path])
                        stream.flush()
                        os.fsync(stream.fileno())
                    try:
                        os.replace(restore, path)
                    finally:
                        restore.unlink(missing_ok=True)
            raise
        finally:
            for temp in temporary.values():
                temp.unlink(missing_ok=True)

    def save(self, name, expected_sha, ops=None):
        path = self.workspace.resolve(name, '.json')
        if not isinstance(expected_sha, str) or not expected_sha:
            raise ValueError('읽은 파일의 sha256이 필요합니다')
        with self.lock._locked():
            # 잠금을 기다리는 사이 경로가 달라졌는지도 다시 확인한다.
            path = self.workspace.resolve(name, '.json')
            if path.is_relative_to(self.root / 'review') or path.name in ('hashes.json', 'bind.json'):
                raise PermissionError('명세 파일만 수정할 수 있습니다')
            folder = path.parent
            while True:
                if (folder / 'bind.json').exists():
                    raise PermissionError('묶인 실행 사본은 수정하지 않습니다. 원본 ops를 수정해 주세요')
                if folder == self.root:
                    break
                folder = folder.parent
            before = path.read_bytes()
            current = read_spec(path, before)
            if _sha(before) != expected_sha:
                raise EditConflict(current)
            history = self.history(path)
            versions = sorted((p for p in history.glob('*.json') if p.stem.isdigit()), key=lambda p: int(p.stem))
            for version in versions:
                self.inside(version)
            log_path = self.inside(self.root / 'review/edits.jsonl')
            previous_log = log_path.read_bytes() if log_path.exists() else b''
            summaries = []
            try:
                if ops is None:
                    if not versions:
                        raise ValueError('되돌릴 수정 기록이 없습니다')
                    after = self.inside(versions[-1]).read_bytes()
                    result = read_spec(path, after)
                    recorded_ops = [{'op': 'undo', 'history': versions[-1].name}]
                    summaries.append('직전 명세로 되돌림')
                else:
                    if not isinstance(ops, list) or not ops:
                        raise ValueError('수정 작업 목록이 필요합니다')
                    raw = deepcopy(current['spec'])
                    for op in ops:
                        target, field, old, new = _apply(raw, op)
                        count = sum(1 for p in current.get('prims', []) if any(s['item'] == target for s in p['sources']))
                        summaries.append(f"{target + ' ' if target else ''}{field} {json.dumps(old, ensure_ascii=False)}→{json.dumps(new, ensure_ascii=False)}" + (f" (인스턴스 {count}곳)" if target else ''))
                    after = _json(raw)
                    result = read_spec(path, after)
                    recorded_ops = ops
                if result['violations']:
                    from src.engine.results import Violation
                    raise SpecError([Violation.model_validate(v) for v in result['violations']])
            except (ValueError, KeyError, IndexError, TypeError) as error:
                raise SpecError([spec_violation('UI_EDIT', '', str(error))]) from None
            writes = {}
            hashes_path = self.inside(path.parent / 'hashes.json')
            if hashes_path.exists():
                rows = json.loads(hashes_path.read_text(encoding='utf-8-sig'))
                matching = [row for row in rows if row['op'] == result['spec']['op']]
                if len(matching) != 1 or path.stem != result['spec']['op']:
                    raise ValueError('hashes.json의 명세 항목이 일치하지 않습니다')
                matching[0]['spec_hash'] = spec_hash(parse_spec(result['spec']))
                if 'bytes' in matching[0]:
                    matching[0]['bytes'] = len(after)
                writes[hashes_path] = _json(rows)
            row = {'time': datetime.now().astimezone().isoformat(), 'file': name, 'ops': recorded_ops,
                   'before_sha': _sha(before), 'after_sha': _sha(after), 'summary': '; '.join(summaries)}
            writes = {**writes, path: after, log_path: previous_log + json.dumps(row, ensure_ascii=False, allow_nan=False).encode('utf8') + b'\n'}
            # 되돌리기는 기록 스택에서 하나를 꺼내 쓴다(새 기록을 쌓지 않음). 쌓으면 다음 되돌리기가
            # 방금 되돌린 것을 다시 되살려 두 번 누를 때 제자리로 돌아온다.
            history_path = None
            if ops is not None:
                number = int(versions[-1].stem) + 1 if versions else 1
                history_path = self.inside(history / f'{number}.json')
                writes = {history_path: before, **writes}
            # 외부 작성자는 이 잠금을 쓰지 않으므로 교체 직전에도 한 번 더 대조한다.
            latest = path.read_bytes()
            if latest != before:
                raise EditConflict(read_spec(path, latest))
            self._replace_many(writes)
            if history_path is None:
                self.inside(versions[-1]).unlink()
            else:
                for old in (versions + [history_path])[:-50]:
                    self.inside(old).unlink()
            return result
