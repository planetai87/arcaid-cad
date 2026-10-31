import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import PlanCanvas, { layerColor } from './components/PlanCanvas.jsx';
import NotesPanel from './components/NotesPanel.jsx';
import DetailsPanel from './components/DetailsPanel.jsx';
import useNotes from './components/useNotes.js';
import { matchingTargets, targetPoint } from './utils/notes.js';
import { boundsOf, geometrySignature } from './utils/geometry.js';

async function getJSON(url) {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok)
    throw new Error('파일을 읽지 못했습니다 (' + response.status + ')');
  return response.json();
}
const api = (kind, path) =>
  '/api/' + kind + '?path=' + encodeURIComponent(path);
const EMPTY = [];
export default function App() {
  const notes = useNotes();
  const [noteMode, setNoteMode] = useState(false),
    [tab, setTab] = useState('details');
  const [activeNoteId, setActiveNoteId] = useState(null),
    [focusRequest, setFocusRequest] = useState(null);
  useEffect(() => {
    const keyboard = (event) => {
      if (
        event.key.toLowerCase() !== 'm' ||
        event.repeat ||
        event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        event.target.closest('input,textarea,select,[contenteditable]')
      )
        return;
      event.preventDefault();
      setNoteMode((mode) => !mode);
      setTab('notes');
    };
    window.addEventListener('keydown', keyboard);
    return () => window.removeEventListener('keydown', keyboard);
  }, []);
  const [workspace, setWorkspace] = useState({ specs: [], bases: [] }),
    [records, setRecords] = useState({});
  const [enabled, setEnabled] = useState(new Set()),
    [basePath, setBasePath] = useState(''),
    [baseData, setBaseData] = useState(null);
  const [hidden, setHidden] = useState(new Set()),
    [selection, setSelection] = useState(null),
    [fitToken, setFitToken] = useState(0);
  const [changed, setChanged] = useState(new Set()),
    [coordinate, setCoordinate] = useState({ x: 0, y: 0 });
  const [connection, setConnection] = useState('연결 중'),
    [error, setError] = useState('');
  const recordsRef = useRef({}),
    versions = useRef({}),
    changedRef = useRef(new Map()),
    baseRef = useRef(''),
    known = useRef(new Set()),
    alive = useRef(true),
    baseInitialized = useRef(false);
  const loadSpec = useCallback(async (path, highlight = false) => {
    const version = (versions.current[path] || 0) + 1;
    versions.current[path] = version;
    try {
      const data = await getJSON(api('spec', path));
      if (!alive.current || versions.current[path] !== version) return;
      const old = recordsRef.current[path];
      // 치수는 표준 치수 스타일의 실제 문자 높이·간격(mm)을 붙여 그린다.
      const dimstyles = data.dimstyles || {};
      data.prims = (data.prims || []).map((p) => {
        const style = p.kind === 'dim' ? dimstyles[p.name] : null;
        const q = style
          ? { ...p, height: style.text_height, gap: style.gap }
          : p;
        return {
          ...q,
          _file: path,
          _key: path + ':' + p.oid,
          _bounds: boundsOf([q]),
        };
      });
      if (highlight && old) {
        const previous = new Map(
          old.prims.map((p) => [p.oid, geometrySignature(p)]),
        );
        for (const p of data.prims)
          if (previous.get(p.oid) !== geometrySignature(p))
            changedRef.current.set(p._key, Date.now() + 3000);
        setChanged(new Set(changedRef.current.keys()));
      }
      recordsRef.current = { ...recordsRef.current, [path]: data };
      setRecords(recordsRef.current);
      if (!old) setFitToken((t) => t + 1);
    } catch (e) {
      if (!alive.current || versions.current[path] !== version) return;
      recordsRef.current = {
        ...recordsRef.current,
        [path]: {
          prims: [],
          violations: [{ message: e.message }],
          warnings: [],
        },
      };
      setRecords(recordsRef.current);
    }
  }, []);
  const loadBase = useCallback(async (path) => {
    const version = (versions.current.base || 0) + 1;
    versions.current.base = version;
    if (!path) {
      setBaseData(null);
      return;
    }
    try {
      const data = await getJSON(api('base', path));
      if (!alive.current || versions.current.base !== version) return;
      data.prims = data.prims.map((p, i) => ({
        ...p,
        _file: path,
        _key: 'base:' + path + ':' + i,
        _bounds: boundsOf([p]),
      }));
      setBaseData(data);
      setFitToken((t) => t + 1);
    } catch (e) {
      if (alive.current && versions.current.base === version) {
        setBaseData(null);
        setError(e.message);
      }
    }
  }, []);
  const refresh = useCallback(async () => {
    try {
      const data = await getJSON('/api/workspace');
      if (!alive.current) return;
      const next = new Set(data.specs.map((row) => row.path));
      // 새 명세 목록은 지금 계산한다. 상태 갱신 함수는 나중에 실행되므로 그 안에서 known.current를 읽으면
      // 아래에서 바뀐 값을 보게 되어 새 명세가 꺼진 채로 남는다.
      const added = [...next].filter((path) => !known.current.has(path));
      setEnabled((previous) => {
        const updated = new Set([...previous].filter((p) => next.has(p)));
        for (const path of added) updated.add(path);
        return updated;
      });
      for (const path of added) void loadSpec(path);
      known.current = next;
      setWorkspace(data);
      setError('');
      if (!baseInitialized.current) {
        baseInitialized.current = true;
        if (data.bases.some((row) => row.path === '@base.dxf')) {
          baseRef.current = '@base.dxf';
          setBasePath('@base.dxf');
          void loadBase('@base.dxf');
        }
      }
    } catch (e) {
      if (alive.current) setError(e.message);
    }
  }, [loadSpec, loadBase]);
  useEffect(() => {
    alive.current = true;
    void refresh();
    const events = new EventSource('/api/events');
    events.onopen = () => {
      setConnection('변경 감지 중');
      void refresh();
      void notes.reload();
      // 재연결 사이 놓친 변경도 다시 읽는다.
      for (const path of known.current) void loadSpec(path, true);
      if (baseRef.current) void loadBase(baseRef.current);
    };
    events.onerror = () => setConnection('다시 연결 중');
    events.onmessage = (e) => {
      const event = JSON.parse(e.data);
      if (event.type !== 'changed') return;
      if (event.path === 'review/notes.json') {
        void notes.reload();
        return;
      }
      void refresh();
      if (
        event.path.toLowerCase().endsWith('.json') &&
        known.current.has(event.path)
      )
        void loadSpec(event.path, true);
      if (event.path === baseRef.current) void loadBase(event.path);
    };
    const timer = setInterval(() => {
      let expired = false;
      for (const [key, until] of changedRef.current)
        if (until <= Date.now()) {
          changedRef.current.delete(key);
          expired = true;
        }
      if (expired) setChanged(new Set(changedRef.current.keys()));
    }, 250);
    return () => {
      alive.current = false;
      events.close();
      clearInterval(timer);
    };
  }, [refresh, loadSpec, loadBase, notes.reload]);
  const prims = useMemo(
    () =>
      workspace.specs
        .filter((row) => enabled.has(row.path))
        .flatMap((row) => records[row.path]?.prims || []),
    [workspace, enabled, records],
  );
  const base = baseData?.prims || EMPTY;
  const layers = useMemo(
    () =>
      Object.assign({}, ...Object.values(records).map((r) => r.layers || {})),
    [records],
  );
  const layerNames = useMemo(
    () => [...new Set([...prims, ...base].map((p) => p.layer))].sort(),
    [prims, base],
  );
  const grids = useMemo(
    () =>
      workspace.specs
        .filter((row) => enabled.has(row.path))
        .map((row) => ({ path: row.path, grid: records[row.path]?.grid })),
    [workspace, enabled, records],
  );
  const allKnown = useMemo(
    () => workspace.specs.flatMap((row) => records[row.path]?.prims || []),
    [workspace, records],
  );
  const activeNote = notes.notes.find((note) => note.id === activeNoteId);
  const noteTargets = useMemo(
    () => (activeNote ? matchingTargets(activeNote.target, prims, base) : []),
    [activeNote, prims, base],
  );
  const highlighted = useMemo(
    () => new Set(noteTargets.map((p) => p._key)),
    [noteTargets],
  );
  const selected = useMemo(
    () =>
      selection
        ? [...prims, ...base].find((p) => p._key === selection)
        : noteTargets[0] || null,
    [selection, prims, base, noteTargets],
  );
  const selectObject = useCallback((p) => {
    setSelection(p?._key || null);
    setActiveNoteId(null);
  }, []);
  const item = selected?.sources?.[0]?.item;
  const instances = item
    ? prims.filter(
        (p) => p._file === selected._file && p.sources?.[0]?.item === item,
      ).length
    : 0;
  const issues = workspace.specs
    .filter((row) => enabled.has(row.path))
    .flatMap((row) => [
      ...(records[row.path]?.violations || []).map((v) => ({
        path: row.path,
        message: v.message,
        error: true,
      })),
      ...(records[row.path]?.warnings || []).map((message) => ({
        path: row.path,
        message,
      })),
    ]);
  for (const message of baseData?.warnings || [])
    issues.push({ path: basePath, message });
  const toggle = (setter, value) =>
    setter((previous) => {
      const result = new Set(previous);
      result.has(value) ? result.delete(value) : result.add(value);
      return result;
    });
  const focusNote = (note) => {
    setActiveNoteId(note.id);
    setTab('notes');
    setSelection(null);
    setFocusRequest({
      point: targetPoint(note.target),
      id: note.id,
      token: Date.now(),
    });
    const target = note.target;
    const files =
      target.kind === 'spec'
        ? [target.file]
        : target.kind === 'area'
          ? target.specs.map((t) => t.file)
          : [];
    setEnabled(
      (previous) =>
        new Set([
          ...previous,
          ...files.filter((file) => known.current.has(file)),
        ]),
    );
    const matched = matchingTargets(target, allKnown, base);
    const targetLayers = new Set([
      ...matched.map((p) => p.layer),
      ...(target.kind === 'area'
        ? [...target.specs, ...target.bases].map((t) => t.layer)
        : [target.layer]),
    ]);
    setHidden(
      (previous) =>
        new Set([...previous].filter((layer) => !targetLayers.has(layer))),
    );
    const file =
      target.kind === 'base'
        ? target.file
        : target.kind === 'area'
          ? target.bases[0]?.file
          : null;
    if (
      file &&
      file !== baseRef.current &&
      workspace.bases.some((row) => row.path === file)
    ) {
      baseRef.current = file;
      setBasePath(file);
      void loadBase(file);
    }
  };
  const createNote = async (text, target) => {
    const saved = await notes.save(null, { text, target });
    if (saved) setTab('notes');
    return saved;
  };
  const chooseBase = (e) => {
    const path = e.target.value;
    baseRef.current = path;
    setBasePath(path);
    setSelection(null);
    void loadBase(path);
  };
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          arcaid <span>도면 검토</span>
        </div>
        <div className="mode">검토</div>
        <span className="connection" role="status">
          <i className={connection === '변경 감지 중' ? 'online' : ''} />
          {connection}
        </span>
      </header>
      <div className="workspace">
        <aside className="files panel" aria-label="파일과 레이어">
          <section>
            <div className="section-title">
              <h2>작업 명세</h2>
              <span>{workspace.specs.length}개</span>
            </div>
            <p className="hint">함께 볼 명세를 켜세요.</p>
            {!workspace.specs.length && (
              <p className="empty">작업 폴더에 명세 JSON이 없습니다.</p>
            )}
            <div className="file-list">
              {workspace.specs.map((row) => (
                <label
                  key={row.path}
                  className={
                    'file-row ' + (enabled.has(row.path) ? 'enabled' : '')
                  }
                >
                  <input
                    type="checkbox"
                    checked={enabled.has(row.path)}
                    onChange={() => toggle(setEnabled, row.path)}
                  />
                  <span>
                    <strong>{row.op || row.path.split('/').at(-1)}</strong>
                    <small>{row.path}</small>
                    {(row.error ||
                      records[row.path]?.violations?.length > 0) && (
                      <em>명세 오류 · 상세 확인</em>
                    )}
                  </span>
                </label>
              ))}
            </div>
          </section>
          <section>
            <h2>기준 도면</h2>
            <label className="sr-only" htmlFor="base-select">
              기준 DXF 선택
            </label>
            <select id="base-select" value={basePath} onChange={chooseBase}>
              <option value="">기준 도면 없이 보기</option>
              {workspace.bases.map((row) => (
                <option key={row.path} value={row.path}>
                  {row.name || row.path}
                </option>
              ))}
            </select>
            <p className="hint">기준 객체는 회색으로 표시합니다.</p>
            {baseData?.truncated && (
              <p className="error">기준 도면의 일부만 표시 중입니다.</p>
            )}
          </section>
          <section>
            <div className="section-title">
              <h2>레이어</h2>
              <span>{layerNames.length}개</span>
            </div>
            <div className="layer-list">
              {layerNames.map((name) => (
                <label key={name}>
                  <input
                    type="checkbox"
                    checked={!hidden.has(name)}
                    onChange={() => toggle(setHidden, name)}
                  />
                  <i
                    style={{ background: layerColor(layers[name]?.color_rgb) }}
                  />
                  {name}
                </label>
              ))}
            </div>
          </section>
          <div className="files-footer">
            원본 파일이 바뀌면 화면에 반영됩니다.
          </div>
        </aside>
        <main className="drawing">
          <div className="canvas-toolbar">
            <div>
              <strong>모형 공간</strong>
              <span>
                {prims.length.toLocaleString()}개 객체
                {base.length > 0
                  ? ' + 기준 ' + base.length.toLocaleString() + '개'
                  : ''}
              </span>
            </div>
            <div className="canvas-actions">
              <button
                aria-pressed={noteMode}
                onClick={() => {
                  setNoteMode((mode) => !mode);
                  setTab('notes');
                }}
              >
                메모 <kbd>M</kbd>
              </button>
              <button
                onClick={() => {
                  setFocusRequest(null);
                  setFitToken((t) => t + 1);
                }}
              >
                화면 맞춤
              </button>
            </div>
          </div>
          {error && (
            <div className="error-banner" role="alert">
              {error}
              <button onClick={refresh}>다시 읽기</button>
            </div>
          )}
          {notes.error && (
            <div className="error-banner" role="alert">
              {notes.error}
              <button onClick={notes.reload}>다시 읽기</button>
            </div>
          )}
          <PlanCanvas
            noteMode={noteMode}
            notes={notes.notes}
            recentNotes={notes.recent}
            activeNoteId={activeNoteId}
            onFocusNote={focusNote}
            onCreateNote={createNote}
            focusRequest={focusRequest}
            highlighted={highlighted}
            noteTarget={activeNote?.target}
            prims={prims}
            base={base}
            grids={grids}
            layers={layers}
            hidden={hidden}
            selected={selected}
            onSelect={selectObject}
            changed={changed}
            fitToken={fitToken}
            onCoordinate={setCoordinate}
          />
          <footer className="statusbar">
            <span>Y ↑ · mm</span>
            <output>
              X {coordinate.x.toLocaleString()}　Y{' '}
              {coordinate.y.toLocaleString()}
            </output>
            <span>
              {changed.size
                ? '변경 객체를 3초간 강조합니다'
                : '원본 좌표 그대로 표시'}
            </span>
          </footer>
        </main>
        <aside className="details panel" aria-label="검토 정보">
          <div className="detail-tabs" role="tablist" aria-label="검토 패널">
            <button
              role="tab"
              aria-selected={tab === 'details'}
              onClick={() => setTab('details')}
            >
              객체 정보
            </button>
            <button
              role="tab"
              aria-selected={tab === 'notes'}
              onClick={() => setTab('notes')}
            >
              메모 {notes.notes.length}
            </button>
          </div>
          {tab === 'notes' ? (
            <NotesPanel
              notes={notes.notes}
              recent={notes.recent}
              activeId={activeNoteId}
              prims={allKnown}
              base={base}
              onFocus={focusNote}
              onSave={notes.save}
            />
          ) : (
            <DetailsPanel
              selected={selected}
              item={item}
              instances={instances}
              issues={issues}
              onClear={() => {
                setSelection(null);
                setActiveNoteId(null);
              }}
            />
          )}
        </aside>
      </div>
    </div>
  );
}
