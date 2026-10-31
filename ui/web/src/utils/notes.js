import { pointsOf, boundsOf, dimensionGeometry } from './geometry.js';

export const statusLabels = {
  open: '열림',
  answered: '답함',
  resolved: '해결',
};
export const targetPoint = (target) =>
  target.kind === 'area'
    ? [
        (target.window[0] + target.window[2]) / 2,
        (target.window[1] + target.window[3]) / 2,
      ]
    : target.point;
export const pinPosition = ([x, y], view) => ({
  x: view.x + x * view.scale,
  y: view.y - y * view.scale,
});
export function targetFor(p, point) {
  if (!p) return { kind: 'point', point };
  if (p.handle)
    return {
      kind: 'base',
      file: p._file,
      handle: p.handle,
      layer: p.layer,
      point,
    };
  return {
    kind: 'spec',
    file: p._file,
    oid: p.oid,
    path: p.path,
    sources: p.sources,
    layer: p.layer,
    point,
  };
}
function inside([x, y], [x0, y0, x1, y1]) {
  return x >= x0 && x <= x1 && y >= y0 && y <= y1;
}
function segmentCrosses(a, b, w) {
  let low = 0,
    high = 1;
  for (let axis = 0; axis < 2; axis++) {
    const delta = b[axis] - a[axis];
    if (delta === 0) {
      if (a[axis] < w[axis] || a[axis] > w[axis + 2]) return false;
    } else {
      const t0 = (w[axis] - a[axis]) / delta,
        t1 = (w[axis + 2] - a[axis]) / delta;
      low = Math.max(low, Math.min(t0, t1));
      high = Math.min(high, Math.max(t0, t1));
      if (low > high) return false;
    }
  }
  return true;
}
function polygonContains(points, [x, y]) {
  let result = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const a = points[i],
      b = points[j];
    if (
      a[1] > y !== b[1] > y &&
      x < ((b[0] - a[0]) * (y - a[1])) / (b[1] - a[1]) + a[0]
    )
      result = !result;
  }
  return result;
}
function intersects(p, w) {
  if (p.kind === 'dim' && p.points?.length === 3) {
    const { a, b } = dimensionGeometry(p);
    return [
      [p.points[0], a],
      [p.points[1], b],
      [a, b],
    ].some(([start, end]) => segmentCrosses(start, end, w));
  }
  const b = p._bounds || boundsOf([p]);
  if (b.minX > w[2] || b.maxX < w[0] || b.minY > w[3] || b.maxY < w[1])
    return false;
  if (p.kind === 'circle') {
    const [x, y] = p.center;
    const near = Math.hypot(
      Math.max(w[0] - x, 0, x - w[2]),
      Math.max(w[1] - y, 0, y - w[3]),
    );
    const far = Math.hypot(
      Math.max(Math.abs(x - w[0]), Math.abs(x - w[2])),
      Math.max(Math.abs(y - w[1]), Math.abs(y - w[3])),
    );
    return near <= p.radius && far >= p.radius;
  }
  const loops = p.kind === 'hatch' ? p.loops || [] : [pointsOf(p)];
  let contains = false;
  for (const points of loops) {
    if (points.some((point) => inside(point, w))) return true;
    for (let i = 1; i < points.length; i++)
      if (segmentCrosses(points[i - 1], points[i], w)) return true;
    if (p.kind === 'hatch' || p.kind === 'text' || p.closed) {
      if (points.length && segmentCrosses(points.at(-1), points[0], w))
        return true;
      if (polygonContains(points, [w[0], w[1]])) contains = !contains;
    }
  }
  return contains;
}
export function areaTarget(window, prims, base) {
  const [a, b, c, d] = window,
    w = [Math.min(a, c), Math.min(b, d), Math.max(a, c), Math.max(b, d)];
  const collect = (rows, kind) => {
    const found = new Map();
    for (const p of rows) {
      if (!intersects(p, w)) continue;
      const target = targetFor(p, [0, 0]);
      const { point, kind: ignored, ...identity } = target;
      found.set(
        JSON.stringify([
          identity.file,
          kind === 'spec' ? identity.oid : identity.handle,
        ]),
        identity,
      );
    }
    return [...found.values()];
  };
  const specs = collect(prims, 'spec'),
    bases = collect(base, 'base');
  return {
    kind: 'area',
    window: w,
    specs: specs.slice(0, 200),
    bases: bases.slice(0, 200),
    truncated: specs.length > 200 || bases.length > 200,
  };
}
function sourceSignature(sources) {
  return JSON.stringify(sources, (_key, value) => {
    if (value && typeof value === 'object' && !Array.isArray(value))
      return Object.fromEntries(
        Object.keys(value)
          .sort()
          .map((key) => [key, value[key]]),
      );
    return value;
  });
}
function matches(p, t, kind) {
  return (
    p._file === t.file &&
    (kind === 'base'
      ? p.handle === t.handle
      : p.oid === t.oid &&
        (t.path == null || p.path === t.path) &&
        (t.sources == null ||
          sourceSignature(p.sources) === sourceSignature(t.sources)))
  );
}
export function matchingTargets(target, prims, base) {
  if (target.kind === 'spec')
    return prims.filter((p) => matches(p, target, 'spec'));
  if (target.kind === 'base')
    return base.filter((p) => matches(p, target, 'base'));
  if (target.kind === 'area')
    return [
      ...prims.filter((p) => target.specs.some((t) => matches(p, t, 'spec'))),
      ...base.filter((p) => target.bases.some((t) => matches(p, t, 'base'))),
    ];
  return [];
}
export function targetMissing(target, prims, base) {
  if (target.kind === 'point') return false;
  if (target.kind === 'area')
    return (
      target.specs.some((t) => !prims.some((p) => matches(p, t, 'spec'))) ||
      target.bases.some((t) => !base.some((p) => matches(p, t, 'base')))
    );
  return !matchingTargets(target, prims, base).length;
}
export function targetSummary(target) {
  const point = targetPoint(target)
    .map((n) => Math.round(n * 100) / 100)
    .join(', ');
  if (target.kind === 'area')
    return (
      '영역 [' +
      target.window.join(', ') +
      '] · 명세 ' +
      target.specs.length +
      '개 · 기준 ' +
      target.bases.length +
      '개' +
      (target.truncated ? ' · 200개 이후 생략' : '')
    );
  if (target.kind === 'point') return '지점 (' + point + ')';
  if (target.kind === 'base')
    return (
      target.file +
      ' · handle ' +
      target.handle +
      ' · ' +
      target.layer +
      ' · (' +
      point +
      ')'
    );
  return (
    target.file +
    ' · ' +
    target.oid +
    ' · ' +
    target.path +
    ' · ' +
    target.layer +
    ' · ' +
    target.sources.map((s) => s.item).join(', ') +
    ' · (' +
    point +
    ')'
  );
}
