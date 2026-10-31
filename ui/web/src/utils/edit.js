// 원본 JSON 좌표 표기와 화면 편집에서 쓰는 순수 함수.
export function splitCoord(value) {
  if (typeof value === 'number') return { name: null, offset: value };
  const match = String(value)
    .trim()
    .match(/^([A-Za-z0-9_.]{1,16})\s*(?:([+-])\s*(\d+(?:\.\d+)?))?$/);
  if (!match || Number.isFinite(Number(value)))
    return { name: null, offset: Number(value) };
  return {
    name: match[1],
    offset: Number(match[3] || 0) * (match[2] === '-' ? -1 : 1),
  };
}
export function joinCoord({ name, offset }) {
  if (!name) return offset;
  const magnitude = Math.abs(offset).toLocaleString('en-US', {
    useGrouping: false,
    maximumFractionDigits: 20,
  });
  return name + (offset === 0 ? '' : (offset > 0 ? '+' : '-') + magnitude);
}
export function getItem(spec, path) {
  if (!spec || !/^items\[\d+\](?:\.items\[\d+\])*$/.test(path || ''))
    return null;
  return [...path.matchAll(/items\[(\d+)\]/g)].reduce(
    (obj, m) => obj?.items?.[Number(m[1])],
    spec,
  );
}
export function gridSpacing(axis, name) {
  const sorted = Object.entries(axis).sort(
    (a, b) => a[1] - b[1] || a[0].localeCompare(b[0]),
  );
  const i = sorted.findIndex(([key]) => key === name);
  return {
    previous: i > 0 ? sorted[i][1] - sorted[i - 1][1] : null,
    next:
      i >= 0 && i < sorted.length - 1 ? sorted[i + 1][1] - sorted[i][1] : null,
  };
}
export function snapCandidates(prims) {
  return prims.flatMap((p) => {
    if (p.kind === 'arc')
      return [p.start_angle, p.end_angle].map((a) => [
        p.center[0] + p.radius * Math.cos((a * Math.PI) / 180),
        p.center[1] + p.radius * Math.sin((a * Math.PI) / 180),
      ]);
    return p.points?.length ? p.points : p.at ? [p.at] : [];
  });
}
export function snapPoint(point, points, axes, scale, alt = false) {
  if (alt) return point;
  const tolerance = 10 / scale;
  const nearX = axes.filter(
    (a) => a.axis === 'X' && Math.abs(a.value - point[0]) <= tolerance,
  );
  const nearY = axes.filter(
    (a) => a.axis === 'Y' && Math.abs(a.value - point[1]) <= tolerance,
  );
  // 2축 교차점이 모두 가까우면 두 좌표를 함께 맞춘다.
  const intersections = nearX
    .flatMap((x) => nearY.map((y) => [x.value, y.value]))
    .filter((p) => Math.hypot(p[0] - point[0], p[1] - point[1]) <= tolerance);
  const candidates = [
    ...points,
    ...(intersections.length
      ? intersections
      : [
          ...nearX.map((a) => [a.value, point[1]]),
          ...nearY.map((a) => [point[0], a.value]),
        ]),
  ];
  let best = point,
    distance = tolerance;
  for (const p of candidates) {
    const d = Math.hypot(p[0] - point[0], p[1] - point[1]);
    if (d <= distance) {
      best = p;
      distance = d;
    }
  }
  // 맞출 대상이 없으면 끌어서 정한 좌표를 10mm 단위로 반올림한다(2187.27 같은 값이 명세에 남지 않게).
  // Alt를 누르면 반올림도 하지 않는다.
  if (best === point) return point.map((v) => Math.round(v / 10) * 10);
  return best;
}
