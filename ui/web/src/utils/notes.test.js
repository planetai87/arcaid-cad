import test from 'node:test';
import assert from 'node:assert/strict';
import {
  pinPosition,
  targetFor,
  areaTarget,
  matchingTargets,
  targetPoint,
} from './notes.js';

const spec = {
  kind: 'line',
  points: [
    [0, 0],
    [100, 100],
  ],
  _file: 'a.json',
  oid: 'o1',
  path: 'g/1',
  sources: [{ item: 'items[3]', groups: [{ i: 1 }] }],
  layer: 'WAL',
};
const base = {
  kind: 'line',
  points: [
    [20, 0],
    [20, 80],
  ],
  _file: '@base.dxf',
  handle: 'AB',
  layer: 'WAL',
};
test('메모 핀의 화면 위치는 Y축 변환과 확대·이동을 따른다', () => {
  assert.deepEqual(pinPosition([10, 20], { x: 100, y: 200, scale: 2 }), {
    x: 120,
    y: 160,
  });
  assert.deepEqual(
    targetPoint({ kind: 'area', window: [0, 10, 100, 30] }),
    [50, 20],
  );
});
test('메모는 파일·oid·path·sources 또는 기준 handle을 보존한다', () => {
  assert.deepEqual(targetFor(spec, [2, 3]), {
    kind: 'spec',
    file: 'a.json',
    oid: 'o1',
    path: 'g/1',
    sources: spec.sources,
    layer: 'WAL',
    point: [2, 3],
  });
  assert.equal(targetFor(base, [1, 2]).handle, 'AB');
  assert.deepEqual(targetFor(null, [1, 2]), { kind: 'point', point: [1, 2] });
});
test('영역은 교차한 객체를 종류별로 모으고 INSERT handle 중복을 제거한다', () => {
  const missed = {
    ...spec,
    oid: 'miss',
    points: [
      [0, 100],
      [100, 0],
    ],
  };
  const area = areaTarget(
    [25, 25, 15, 15],
    [spec, missed],
    [base, { ...base }],
  );
  assert.deepEqual(area.window, [15, 15, 25, 25]);
  assert.deepEqual(
    area.specs.map((s) => s.oid),
    ['o1'],
  );
  assert.deepEqual(
    area.bases.map((s) => s.handle),
    ['AB'],
  );
});
test('영역 대상은 각각 최대 200개이고 잘림을 표시한다', () => {
  const area = areaTarget(
    [0, 0, 100, 100],
    Array.from({ length: 205 }, (_, i) => ({ ...spec, oid: String(i) })),
    [],
  );
  assert.equal(area.specs.length, 200);
  assert.equal(area.truncated, true);
});
test('같은 oid여도 파일이나 인스턴스가 바뀌면 대상이 사라진 것으로 본다', () => {
  const target = targetFor(spec, [1, 2]);
  assert.equal(
    matchingTargets(target, [spec, { ...spec, _file: 'b.json' }], []).length,
    1,
  );
  assert.equal(
    matchingTargets(target, [{ ...spec, sources: [] }], []).length,
    0,
  );
  assert.equal(
    matchingTargets(targetFor(base, [1, 2]), [], [base, { ...base }]).length,
    2,
  );
});

test('영역 선택은 치수선과 보조선의 실제 위치를 검사한다', () => {
  const dim = {
    ...spec,
    kind: 'dim',
    oid: 'dimension',
    points: [
      [0, 0],
      [1000, 0],
      [500, 500],
    ],
    angle: 0,
  };
  assert.deepEqual(
    areaTarget([40, 490, 60, 510], [dim], []).specs.map((p) => p.oid),
    ['dimension'],
  );
  assert.equal(areaTarget([490, 240, 510, 260], [dim], []).specs.length, 0);
});

test('sources의 JSON 키 순서가 달라도 같은 원본 인스턴스로 찾는다', () => {
  const target = targetFor(spec, [1, 2]);
  target.sources = [{ groups: [{ i: 1 }], item: 'items[3]' }];
  assert.equal(matchingTargets(target, [spec], []).length, 1);
});
