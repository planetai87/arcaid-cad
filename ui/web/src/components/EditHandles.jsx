import React from 'react';
import { Circle, Group, Line, Text } from 'react-konva';
import { snapPoint } from '../utils/edit.js';

export function previewPoint(prim, handle, world) {
  const close = (p) =>
    Math.hypot(p[0] - handle.world[0], p[1] - handle.world[1]) < 0.00001;
  const q = { ...prim };
  if (handle.field === 'center') q.center = world;
  else if (handle.field === 'at') q.at = world;
  else {
    q.points = prim.points?.map((p) => (close(p) ? world : p));
    q.loops = prim.loops?.map((loop) =>
      loop.map((p) => (close(p) ? world : p)),
    );
  }
  return q;
}

export default function EditHandles({
  handles,
  selected,
  scale,
  points,
  axes,
  onEdit,
  onPreview,
  preview,
  onCoordinate,
  saving,
  onDragging,
}) {
  if (!selected?.sources?.length) return null;
  return (
    <>
      {handles.map((h, i) => {
        const shown = preview?.index === i ? preview.world : h.world;
        const move = (e) => {
          e.cancelBubble = true;
          const candidates = points.filter(
            (p) => Math.hypot(p[0] - h.world[0], p[1] - h.world[1]) > 0.00001,
          );
          const next = snapPoint(
            [e.target.x(), -e.target.y()],
            candidates,
            axes,
            scale,
            e.evt.altKey,
          );
          e.target.position({ x: next[0], y: -next[1] });
          onPreview({ index: i, handle: h, world: next });
          onCoordinate({
            x: next[0],
            y: next[1],
            dx: next[0] - h.world[0],
            dy: next[1] - h.world[1],
          });
          return next;
        };
        return (
          <Circle
            key={JSON.stringify([h.source, h.field, h.index])}
            x={shown[0]}
            y={-shown[1]}
            radius={5 / scale}
            fill="#102c3a"
            stroke="#8fe5ff"
            strokeWidth={1.5 / scale}
            hitStrokeWidth={9 / scale}
            draggable={!saving}
            onMouseDown={(e) => {
              e.cancelBubble = true;
            }}
            onClick={(e) => {
              e.cancelBubble = true;
            }}
            onDragStart={(e) => {
              e.cancelBubble = true;
              onDragging(true);
            }}
            onDragMove={move}
            onDragEnd={async (e) => {
              const world = move(e);
              onDragging(false);
              try {
                await onEdit(selected._file, [
                  {
                    op: 'move_point',
                    source: h.source,
                    field: h.field,
                    ...(h.index == null ? {} : { index: h.index }),
                    world,
                  },
                ]);
              } catch {
                /* 위반은 화면의 저장 오류 배너에 표시한다. */
              } finally {
                onPreview(null);
              }
            }}
          />
        );
      })}
    </>
  );
}

export function GridLines({
  grids,
  bounds,
  scale,
  onSelect,
  onEdit,
  onCoordinate,
  onDragging,
  saving,
  noteMode,
  points,
  axes,
}) {
  return (
    <>
      {grids.flatMap(({ grid, path }, index) =>
        ['X', 'Y'].flatMap((axis) =>
          Object.entries(grid?.[axis] || {}).map(([name, value]) => {
            const isX = axis === 'X';
            const update = (e) => {
              e.cancelBubble = true;
              const raw = isX ? e.target.x() : -e.target.y();
              const position = isX ? [raw, bounds.minY] : [bounds.minX, raw];
              const snapped = snapPoint(
                position,
                points,
                axes.filter(
                  (a) =>
                    a.axis === axis && !(a.path === path && a.name === name),
                ),
                scale,
                e.evt.altKey,
              );
              const next = snapped[isX ? 0 : 1];
              e.target.position(isX ? { x: next, y: 0 } : { x: 0, y: -next });
              onCoordinate({
                x: isX ? next : bounds.minX,
                y: isX ? bounds.minY : next,
                dx: isX ? next - value : 0,
                dy: isX ? 0 : next - value,
              });
              return next;
            };
            return (
              <Group
                key={path + axis + name}
                x={isX ? value : 0}
                y={isX ? 0 : -value}
                draggable={!saving && !noteMode}
                listening={!noteMode}
                onMouseDown={(e) => {
                  e.cancelBubble = true;
                }}
                onClick={(e) => {
                  e.cancelBubble = true;
                  onSelect({ path, axis, name });
                }}
                onDragStart={(e) => {
                  e.cancelBubble = true;
                  onDragging(true);
                }}
                onDragMove={update}
                onDragEnd={async (e) => {
                  const next = update(e);
                  onDragging(false);
                  onSelect({ path, axis, name });
                  try {
                    await onEdit(path, [
                      { op: 'set_grid', axis, name, value: next },
                    ]);
                  } catch {
                    e.target.position(
                      isX ? { x: value, y: 0 } : { x: 0, y: -value },
                    );
                  }
                }}
              >
                <Line
                  points={
                    isX
                      ? [0, -bounds.minY, 0, -bounds.maxY]
                      : [bounds.minX, 0, bounds.maxX, 0]
                  }
                  stroke="#567080"
                  opacity={0.65}
                  strokeWidth={0.7 / scale}
                  hitStrokeWidth={7 / scale}
                  dash={[5 / scale, 6 / scale]}
                />
                <Text
                  x={isX ? 0 : bounds.minX - 40 / scale - (index * 26) / scale}
                  y={isX ? -bounds.maxY - 20 / scale - (index * 20) / scale : 0}
                  text={name}
                  fontSize={15 / scale}
                  fill="#a8bfce"
                />
              </Group>
            );
          }),
        ),
      )}
    </>
  );
}
