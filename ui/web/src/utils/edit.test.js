import test from 'node:test';
import assert from 'node:assert/strict';
import {
  splitCoord,
  joinCoord,
  snapPoint,
  snapCandidates,
  gridSpacing,
  getItem,
} from './edit.js';

test('그리드 참조 분해·조립: 0·음수·소수 오프셋과 숫자', () => {
  assert.deepEqual(splitCoord('X2 + 300'), { name: 'X2', offset: 300 });
  assert.equal(joinCoord({ name: 'X2', offset: 0 }), 'X2');
  assert.equal(joinCoord({ name: 'X2', offset: -150.25 }), 'X2-150.25');
  assert.equal(joinCoord({ name: 'Y1', offset: 0.0000001 }), 'Y1+0.0000001');
  assert.equal(joinCoord({ name: null, offset: 42 }), 42);
  assert.deepEqual(splitCoord(-10), { name: null, offset: -10 });
});

test('가장 가까운 끝점·축선, 화면 10px 거리 및 Alt 우회', () => {
  const points = [
    [104, 104],
    [150, 150],
  ];
  assert.deepEqual(snapPoint([100, 100], points, [], 1), [104, 104]);
  assert.deepEqual(snapPoint([100, 100], points, [], 3), [100, 100]);
  assert.deepEqual(snapPoint([100, 100], points, [], 1, true), [100, 100]);
  // 맞출 대상이 없으면 10mm 단위로 반올림, Alt면 그대로
  assert.deepEqual(snapPoint([2187.27, 3800.4], [], [], 1), [2190, 3800]);
  assert.deepEqual(
    snapPoint([2187.27, 3800.4], [], [], 1, true),
    [2187.27, 3800.4],
  );
  assert.deepEqual(
    snapPoint([99, 70], [], [{ axis: 'X', value: 100 }], 1),
    [100, 70],
  );
  assert.deepEqual(
    snapPoint(
      [99, 70],
      [],
      [
        { axis: 'X', value: 100 },
        { axis: 'Y', value: 72 },
      ],
      1,
    ),
    [100, 72],
  );
  assert.deepEqual(snapPoint([100, 100], [[111, 100]], [], 1), [100, 100]);
});

test('명세·기준 끝점과 원호 끝점 후보', () => {
  const points = snapCandidates([
    {
      kind: 'line',
      points: [
        [0, 0],
        [10, 20],
      ],
    },
    {
      kind: 'arc',
      center: [100, 100],
      radius: 10,
      start_angle: 0,
      end_angle: 90,
    },
  ]);
  assert.deepEqual(points, [
    [0, 0],
    [10, 20],
    [110, 100],
    [100, 110],
  ]);
});

test('이웃 축 거리와 원본 항목 조회', () => {
  assert.deepEqual(gridSpacing({ X3: 6600, X1: 0, X2: 3300 }, 'X2'), {
    previous: 3300,
    next: 3300,
  });
  assert.deepEqual(gridSpacing({ X1: 0 }, 'X1'), {
    previous: null,
    next: null,
  });
  assert.equal(
    getItem({ items: [{ items: [{ radius: 12 }] }] }, 'items[0].items[0]')
      .radius,
    12,
  );
});
