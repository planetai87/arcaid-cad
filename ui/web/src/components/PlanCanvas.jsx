import React, { memo, useEffect, useMemo, useRef, useState } from 'react';
import { Stage, Layer, Group, Shape, Line, Text } from 'react-konva';
import Primitive, { paint } from './Primitive.jsx';
import EditHandles, { GridLines, previewPoint } from './EditHandles.jsx';
import { snapCandidates } from '../utils/edit.js';
import { boundsOf, hitDistance, visibleIn } from '../utils/geometry.js';
import {
  areaTarget,
  pinPosition,
  statusLabels,
  targetFor,
  targetPoint,
  targetSummary,
} from '../utils/notes.js';
import { fromScreen } from '../utils/coords.js';
import { fitView, zoomAt } from '../utils/viewport.js';
import { snapToGrid } from '../utils/snap.js';

const StaticBase = memo(function StaticBase({ prims, bounds, scale }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!prims.length || !ref.current) return;
    const width = Math.max(1, bounds.maxX - bounds.minX),
      height = Math.max(1, bounds.maxY - bounds.minY);
    ref.current.cache({
      x: bounds.minX - 20 / scale,
      y: -bounds.maxY - 20 / scale,
      width: width + 40 / scale,
      height: height + 40 / scale,
      pixelRatio: Math.min(
        scale,
        4096 / Math.max(width + 40 / scale, height + 40 / scale),
      ),
      hitCanvasPixelRatio: 0.01,
    });
    ref.current.getLayer()?.batchDraw();
    return () => ref.current?.clearCache();
  }, [prims, bounds, scale]);
  return (
    <Group ref={ref} listening={false}>
      <Shape
        listening={false}
        sceneFunc={(ctx) => {
          for (const p of prims) paint(ctx._context, p, '#a6b4bf', 1 / scale);
        }}
      />
    </Group>
  );
});
export function layerColor(rgb = [220, 230, 238]) {
  // 어두운 배경에서 검정·짙은 ACI 색도 구분되게 밝기를 보정한다.
  const max = Math.max(...rgb),
    factor = max < 145 ? 145 / Math.max(max, 1) : 1;
  return (
    'rgb(' +
    rgb.map((n) => Math.max(45, Math.round(n * factor))).join(',') +
    ')'
  );
}

function NoteDraft({ target, position, size, onSave, onCancel }) {
  const [text, setText] = useState(''),
    [busy, setBusy] = useState(false);
  const submit = async (event) => {
    event.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      if (await onSave(text, target)) onCancel();
    } finally {
      setBusy(false);
    }
  };
  return (
    <form
      className="note-draft"
      style={{
        left: Math.max(4, Math.min(position.x + 15, size.width - 280)),
        top: Math.max(4, Math.min(position.y + 15, size.height - 160)),
      }}
      onSubmit={submit}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === 'Escape') {
          event.preventDefault();
          if (!busy) onCancel();
        }
        if (
          event.key === 'Enter' &&
          !event.shiftKey &&
          !event.nativeEvent.isComposing
        ) {
          event.preventDefault();
          void submit(event);
        }
      }}
    >
      <strong>메모 추가</strong>
      <small>{targetSummary(target)}</small>
      <textarea
        autoFocus
        aria-label="메모 본문"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="지적할 내용 입력"
        rows={2}
      />
      <div>
        <button disabled={busy || !text.trim()} type="submit">
          저장
        </button>
        <button type="button" disabled={busy} onClick={onCancel}>
          취소
        </button>
        <span>Enter 저장 · Esc 취소</span>
      </div>
    </form>
  );
}
export default function PlanCanvas({
  prims,
  base,
  grids,
  layers,
  hidden,
  selected,
  onSelect,
  changed,
  fitToken,
  onCoordinate,
  noteMode,
  notes = [],
  recentNotes = new Set(),
  activeNoteId,
  onFocusNote,
  onCreateNote,
  focusRequest,
  highlighted = new Set(),
  noteTarget,
  handles = [],
  onEdit,
  onGridSelect,
  saving = false,
}) {
  const [preview, setPreview] = useState(null);
  const editDragging = useRef(false);
  const onDragging = (value) => {
    editDragging.current = value;
  };
  const container = useRef(null),
    stage = useRef(null);
  const [size, setSize] = useState({ width: 800, height: 600 }),
    [view, setView] = useState({ x: 40, y: 500, scale: 0.03 });
  const [hover, setHover] = useState(null),
    [pointer, setPointer] = useState({ x: 0, y: 0 });
  const [draft, setDraft] = useState(null),
    [dragArea, setDragArea] = useState(null);
  const frame = useRef(null),
    latest = useRef(null),
    previousFit = useRef(''),
    areaStart = useRef(null),
    ignoreClick = useRef(false);
  useEffect(() => {
    const observer = new ResizeObserver((entries) => {
      const r = entries[0].contentRect;
      setSize({ width: r.width, height: r.height });
    });
    observer.observe(container.current);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame.current);
    };
  }, []);
  useEffect(() => {
    setDraft(null);
    setDragArea(null);
    areaStart.current = null;
    ignoreClick.current = false;
  }, [noteMode]);
  const all = useMemo(() => [...base, ...prims], [base, prims]);
  const snapPoints = useMemo(
    () => snapCandidates(all.filter((p) => !hidden.has(p.layer))),
    [all, hidden],
  );
  const snapAxes = useMemo(
    () =>
      grids.flatMap(({ path, grid }) =>
        ['X', 'Y'].flatMap((axis) =>
          Object.entries(grid?.[axis] || {}).map(([name, value]) => ({
            path,
            axis,
            name,
            value,
          })),
        ),
      ),
    [grids],
  );
  const bounds = useMemo(() => boundsOf(all), [all]);
  const baseBounds = useMemo(() => boundsOf(base), [base]);
  useEffect(() => {
    const identity = fitToken + ':' + size.width + ':' + size.height;
    if (previousFit.current !== identity && all.length) {
      setView(fitView(bounds, size));
      previousFit.current = identity;
    }
  }, [fitToken, size, bounds, all]);
  useEffect(() => {
    if (!focusRequest) return;
    const [x, y] = focusRequest.point;
    setView((v) => ({
      ...v,
      x: size.width / 2 - x * v.scale,
      y: size.height / 2 + y * v.scale,
    }));
  }, [focusRequest, size, fitToken]);
  const visible = useMemo(
    () => prims.filter((p) => !hidden.has(p.layer) && visibleIn(p, view, size)),
    [prims, hidden, view, size],
  );
  const baseVisible = useMemo(
    () => base.filter((p) => !hidden.has(p.layer)),
    [base, hidden],
  );
  const source = selected?.sources?.[0]?.item;
  const wheel = (e) => {
    e.evt.preventDefault();
    const point = e.target.getStage().getPointerPosition();
    setView((v) => zoomAt(v, point, Math.pow(1.0015, -e.evt.deltaY)));
  };
  const worldPoint = () => {
    const position = stage.current.getPointerPosition();
    return position ? fromScreen(position, view) : null;
  };
  const move = (e) => {
    if (editDragging.current) return;
    latest.current = e.target.getStage().getPointerPosition();
    if (areaStart.current) {
      const mm = worldPoint();
      if (mm) setDragArea([...areaStart.current, mm.x, mm.y]);
    }
    if (frame.current) return;
    frame.current = requestAnimationFrame(() => {
      const point = latest.current;
      frame.current = null;
      if (!point) return;
      setPointer(point);
      const mm = fromScreen(point, view);
      onCoordinate({ x: snapToGrid(mm.x), y: snapToGrid(mm.y) });
    });
  };
  const pick = (p) => {
    if (ignoreClick.current) {
      ignoreClick.current = false;
      return;
    }
    if (noteMode) {
      const mm = worldPoint();
      if (mm) setDraft(targetFor(p, [mm.x, mm.y]));
    } else onSelect(p);
  };
  const pickBase = (e) => {
    if (e.target !== e.target.getStage()) return;
    const point = worldPoint();
    if (!point) return;
    let best = null,
      distance = 8 / view.scale;
    for (const p of baseVisible) {
      const d = hitDistance(p, [point.x, point.y]);
      if (d < distance) {
        best = p;
        distance = d;
      }
    }
    pick(best);
  };
  const beginArea = (e) => {
    ignoreClick.current = false;
    if (!noteMode || !e.evt.shiftKey || e.evt.button !== 0) return;
    const mm = worldPoint();
    if (!mm) return;
    areaStart.current = [mm.x, mm.y];
    ignoreClick.current = false;
    setDraft(null);
    setDragArea([mm.x, mm.y, mm.x, mm.y]);
  };
  const endArea = () => {
    if (!areaStart.current) return;
    const start = areaStart.current,
      end = worldPoint();
    areaStart.current = null;
    setDragArea(null);
    if (!end || Math.hypot(start[0] - end.x, start[1] - end.y) * view.scale < 3)
      return;
    ignoreClick.current = true;
    setDraft(
      areaTarget(
        [...start, end.x, end.y],
        prims.filter((p) => !hidden.has(p.layer)),
        baseVisible,
      ),
    );
  };
  const rectangle = (window, color) => {
    const [x0, y0, x1, y1] = window;
    return (
      <Line
        points={[x0, -y0, x1, -y0, x1, -y1, x0, -y1]}
        closed
        fill={color + '22'}
        stroke={color}
        strokeWidth={1.5 / view.scale}
        dash={[6 / view.scale, 4 / view.scale]}
      />
    );
  };
  return (
    <div className={'canvas ' + (noteMode ? 'note-mode' : '')} ref={container}>
      <Stage
        ref={stage}
        width={size.width}
        height={size.height}
        x={view.x}
        y={view.y}
        scaleX={view.scale}
        scaleY={view.scale}
        draggable={!noteMode}
        onWheel={wheel}
        onMouseMove={move}
        onMouseLeave={() => {
          setHover(null);
          areaStart.current = null;
          setDragArea(null);
        }}
        onMouseDown={beginArea}
        onMouseUp={endArea}
        onClick={pickBase}
        onTap={pickBase}
        onDragEnd={(e) => {
          if (e.target === e.target.getStage())
            setView((v) => ({ ...v, x: e.target.x(), y: e.target.y() }));
        }}
      >
        <Layer listening={false}>
          <StaticBase
            prims={baseVisible}
            bounds={baseBounds}
            scale={view.scale}
          />
        </Layer>
        <Layer>
          <GridLines
            grids={grids}
            bounds={bounds}
            scale={view.scale}
            onSelect={onGridSelect}
            onEdit={onEdit}
            onCoordinate={onCoordinate}
            onDragging={onDragging}
            saving={saving}
            noteMode={noteMode}
            points={snapPoints}
            axes={snapAxes}
          />
        </Layer>
        <Layer>
          {visible.map((original) => {
            const p =
              preview && original._key === selected?._key
                ? previewPoint(original, preview.handle, preview.world)
                : original;
            const same =
              source &&
              p._file === selected?._file &&
              p.sources?.[0]?.item === source;
            const active =
              p._key === selected?._key ||
              p._key === hover?._key ||
              highlighted.has(p._key);
            const recent = changed.has(p._key);
            return (
              <Primitive
                key={p._key}
                p={p}
                scale={view.scale}
                color={
                  active
                    ? '#ffffff'
                    : recent
                      ? '#ffcb75'
                      : same
                        ? '#ffd84d'
                        : layerColor(layers[p.layer]?.color_rgb)
                }
                emphasis={active || recent || same}
                opacity={1}
                onHover={setHover}
                onSelect={pick}
              />
            );
          })}
        </Layer>
        <Layer listening={false}>
          {baseVisible
            .filter((p) => p._key === selected?._key || highlighted.has(p._key))
            .map((p) => (
              <Primitive
                key={p._key}
                p={p}
                color="#ffffff"
                scale={view.scale}
                emphasis
              />
            ))}
          {noteTarget?.kind === 'area' &&
            rectangle(noteTarget.window, '#87c9ee')}
          {dragArea && rectangle(dragArea, '#ffbc72')}
        </Layer>
        {!noteMode && (
          <Layer>
            <EditHandles
              handles={hidden.has(selected?.layer) ? [] : handles}
              selected={selected}
              scale={view.scale}
              points={snapPoints}
              axes={snapAxes}
              onEdit={onEdit}
              onPreview={setPreview}
              preview={preview}
              onCoordinate={onCoordinate}
              saving={saving}
              onDragging={onDragging}
            />
          </Layer>
        )}
      </Stage>
      {notes.map((note) => {
        const position = pinPosition(targetPoint(note.target), view);
        if (
          position.x < -30 ||
          position.y < -30 ||
          position.x > size.width + 30 ||
          position.y > size.height + 30
        )
          return null;
        return (
          <button
            key={note.id}
            className={
              'note-pin ' +
              note.status +
              (note.id === activeNoteId ? ' active' : '') +
              (recentNotes.has(note.id) ? ' recent' : '')
            }
            style={{ left: position.x, top: position.y }}
            title={note.text}
            aria-label={
              '메모 ' +
              note.id.slice(1) +
              ' · ' +
              statusLabels[note.status] +
              ' · ' +
              note.text
            }
            onClick={() => {
              setDraft(null);
              onFocusNote(note);
            }}
          >
            {note.id.slice(1)}
          </button>
        );
      })}
      {draft && (
        <NoteDraft
          key={JSON.stringify(draft)}
          target={draft}
          position={pinPosition(targetPoint(draft), view)}
          size={size}
          onSave={onCreateNote}
          onCancel={() => setDraft(null)}
        />
      )}
      {!all.length && !draft && (
        <div className="canvas-empty">
          <strong>검토할 도면을 선택하세요</strong>
          <span>왼쪽에서 명세를 켜거나 기준 DXF를 고르세요.</span>
        </div>
      )}
      {hover && !draft && !dragArea && !preview && (
        <div
          className="tooltip"
          style={{
            left: Math.min(pointer.x + 16, size.width - 255),
            top: Math.min(pointer.y + 18, size.height - 90),
          }}
        >
          <strong>{hover.oid || hover.handle}</strong>
          <span>{hover.path || '경로 없음'}</span>
          <span>레이어 {hover.layer}</span>
        </div>
      )}
      <div className="canvas-guide">
        {noteMode
          ? '메모: 클릭 · Shift + 끌기로 영역 · M으로 종료'
          : '휠로 확대 · 빈 곳 끌기로 이동 · 손잡이·축선 끌기로 수정 · Alt: 스냅 해제'}
      </div>
    </div>
  );
}
