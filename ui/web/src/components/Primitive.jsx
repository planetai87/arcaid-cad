import React, { memo } from 'react';
import { Shape } from 'react-konva';
import {
  polylinePoints,
  arcPoints,
  dimensionGeometry,
} from '../utils/geometry.js';

const geometry = new WeakMap();
function prepared(p) {
  if (geometry.has(p)) return geometry.get(p);
  const value = { ...p };
  if (p.kind === 'polyline')
    value.vertices = polylinePoints(p.points, p.bulges, p.closed);
  if (p.kind === 'arc')
    value.vertices = arcPoints(p.center, p.radius, p.start_angle, p.end_angle);
  if (p.kind === 'dim' && p.points?.length === 3)
    value.dimension = dimensionGeometry(p);
  geometry.set(p, value);
  return value;
}
function path(ctx, points, closed = false) {
  if (!points?.length) return;
  ctx.moveTo(points[0][0], -points[0][1]);
  for (let i = 1; i < points.length; i++)
    ctx.lineTo(points[i][0], -points[i][1]);
  if (closed) ctx.closePath();
}
function label(
  ctx,
  p,
  text,
  height = p.height || 120,
  at = p.at,
  rotation = p.rotation || 0,
  align = p.align || 'L',
) {
  ctx.save();
  ctx.translate(at[0], -at[1]);
  ctx.rotate((-rotation * Math.PI) / 180);
  ctx.font = height + 'px "Malgun Gothic", sans-serif';
  const horizontal = align.at(-1);
  ctx.textAlign =
    horizontal === 'C' ? 'center' : horizontal === 'R' ? 'right' : 'left';
  ctx.textBaseline =
    align[0] === 'T'
      ? 'top'
      : align[0] === 'M'
        ? 'middle'
        : align[0] === 'B'
          ? 'bottom'
          : 'alphabetic';
  ctx.fillText(text, 0, 0);
  ctx.restore();
}
export function paint(
  ctx,
  original,
  color,
  width,
  hit = false,
  minText = width * 11,
) {
  const p = prepared(original);
  ctx.save();
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = width;
  ctx.lineJoin = 'round';
  ctx.beginPath();
  if (p.kind === 'line') path(ctx, p.points);
  if (p.kind === 'polyline' || p.kind === 'arc')
    path(ctx, p.vertices, p.closed);
  if (p.kind === 'circle')
    ctx.arc(p.center[0], -p.center[1], p.radius, 0, Math.PI * 2);
  if (p.kind === 'hatch') {
    for (const loop of p.loops || []) path(ctx, loop, true);
    if (!hit) {
      ctx.globalAlpha = 0.13;
      ctx.fill('evenodd');
      ctx.globalAlpha = 1;
    }
  }
  if (p.kind === 'text') {
    if (hit) {
      const h = Math.max(p.height || 120, minText);
      ctx.font = h + 'px "Malgun Gothic", sans-serif';
      const w = ctx.measureText(p.text || '').width,
        align = p.align || 'L',
        horizontal = align.at(-1);
      const left = horizontal === 'C' ? -w / 2 : horizontal === 'R' ? -w : 0;
      const top = align[0] === 'T' ? 0 : align[0] === 'M' ? -h / 2 : -h;
      ctx.save();
      ctx.translate(p.at[0], -p.at[1]);
      ctx.rotate((-(p.rotation || 0) * Math.PI) / 180);
      ctx.rect(left, top, w, h);
      ctx.restore();
    }
    // 문자도 치수처럼 축소해도 읽히는 최소 크기를 둔다.
    else label(ctx, p, p.text || '', Math.max(p.height || 120, minText));
  }
  if (p.kind === 'block') {
    const [x, y] = p.at,
      r = width * 5;
    ctx.moveTo(x - r, -y);
    ctx.lineTo(x + r, -y);
    ctx.moveTo(x, -y - r);
    ctx.lineTo(x, -y + r);
    ctx.arc(x, -y, r, 0, Math.PI * 2);
    // 블록 형상은 Prim에 없으므로 속성 값(예: 축 버블 X1)을, 없으면 블록 이름을 표시한다.
    const values = Object.values(p.attribs || {}).filter(Boolean);
    if (!hit)
      label(
        ctx,
        p,
        values.length ? values.join(' ') : p.name || '블록',
        minText,
        [x + r, y + r],
      );
  }
  if (p.dimension) {
    const { a, b, value } = p.dimension;
    path(ctx, [p.points[0], a]);
    path(ctx, [p.points[1], b]);
    path(ctx, [a, b]);
    const r = width * 4;
    for (const end of [a, b])
      path(ctx, [
        [end[0] - r, end[1] - r],
        [end[0] + r, end[1] + r],
      ]);
    if (!hit) {
      // 실제 높이로 그리되, 축소해도 화면에서 약 13px 아래로 작아지지 않게 한다(검토용 가독성).
      const height = Math.max(p.height || 120, minText);
      // 문자는 치수선 위쪽(dimtad=1)에 dimgap만큼 띄운다. 치수선 방향에 수직으로 민다.
      let angle = (p.angle || 0) % 180;
      if (angle > 90) angle -= 180;
      const normal = [
        -Math.sin((angle * Math.PI) / 180),
        Math.cos((angle * Math.PI) / 180),
      ];
      const offset = (p.gap || 50) * Math.max(1, height / (p.height || height));
      label(
        ctx,
        p,
        value.toLocaleString('ko-KR', { maximumFractionDigits: 2 }),
        height,
        [
          (a[0] + b[0]) / 2 + normal[0] * offset,
          (a[1] + b[1]) / 2 + normal[1] * offset,
        ],
        angle,
        'BC',
      );
    }
  }
  if (!hit) ctx.stroke();
  ctx.restore();
}
export default memo(function Primitive({
  p,
  color,
  scale,
  onHover,
  onSelect,
  emphasis = false,
  opacity = 1,
}) {
  return (
    <Shape
      stroke={color}
      fill={p.kind === 'hatch' || p.kind === 'text' ? color : undefined}
      opacity={opacity}
      strokeWidth={(emphasis ? 2.7 : 1.2) / scale}
      hitStrokeWidth={10 / scale}
      perfectDrawEnabled={false}
      sceneFunc={(context) =>
        paint(
          context._context,
          p,
          color,
          (emphasis ? 2.7 : 1.2) / scale,
          false,
          13 / scale,
        )
      }
      hitFunc={(context, shape) => {
        paint(context._context, p, color, 10 / scale, true, 13 / scale);
        context.fillStrokeShape(shape);
      }}
      onMouseEnter={() => onHover?.(p)}
      onMouseLeave={() => onHover?.(null)}
      onClick={(event) => {
        event.cancelBubble = true;
        onSelect?.(p);
      }}
      onTap={(event) => {
        event.cancelBubble = true;
        onSelect?.(p);
      }}
    />
  );
});
