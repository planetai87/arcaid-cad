import React, { useEffect, useRef, useState } from 'react';
import { getItem, joinCoord, splitCoord, gridSpacing } from '../utils/edit.js';

export function EditField({
  label,
  value,
  onSave,
  coordinate = false,
  disabled = false,
}) {
  const mirror = label === 'mirror';
  const reference = coordinate ? splitCoord(value) : null;
  const isRef = !!reference?.name;
  const shown = isRef ? reference.offset : value;
  const format = (v) =>
    typeof v === 'string' && !mirror ? v : JSON.stringify(v);
  const [draft, setDraft] = useState(format(shown)),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const saving = useRef(false);
  const encoded = JSON.stringify(shown);
  useEffect(() => {
    setDraft(format(shown));
    setError('');
  }, [encoded]);
  const commit = async () => {
    if (saving.current || disabled || draft === format(shown)) return;
    saving.current = true;
    setBusy(true);
    setError('');
    try {
      let next;
      if (isRef || typeof value === 'number') {
        if (!draft.trim() || !Number.isFinite(Number(draft)))
          throw Error('유한한 숫자를 입력해 주세요');
        next = isRef
          ? joinCoord({ name: reference.name, offset: Number(draft) })
          : Number(draft);
      } else if (typeof value === 'boolean' || mirror) next = JSON.parse(draft);
      else if (value === null) {
        try {
          next = JSON.parse(draft);
        } catch {
          next = draft;
        }
      } else if (typeof value === 'object') next = JSON.parse(draft);
      else next = draft;
      await onSave(next);
    } catch (e) {
      setError(e.message);
    } finally {
      saving.current = false;
      setBusy(false);
    }
  };
  return (
    <label className="edit-field">
      <span>{label}</span>
      <div className="edit-control">
        {isRef && <code>{reference.name} + [</code>}
        {typeof value === 'boolean' || mirror ? (
          <select
            aria-label={label}
            value={draft}
            disabled={busy || disabled}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') e.currentTarget.blur();
            }}
          >
            {value === null || mirror
              ? ['null', '"x"', '"y"'].map((v) => (
                  <option key={v} value={v}>
                    {v === 'null' ? '없음' : JSON.parse(v)}
                  </option>
                ))
              : ['true', 'false'].map((v) => <option key={v}>{v}</option>)}
          </select>
        ) : (
          <input
            aria-label={label}
            value={draft ?? ''}
            disabled={busy || disabled}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                e.preventDefault();
                e.currentTarget.blur();
              }
            }}
            inputMode={
              isRef || typeof value === 'number' ? 'decimal' : undefined
            }
          />
        )}
        {isRef && <code>]</code>}
        {busy && <small>저장 중</small>}
      </div>
      {error && (
        <small className="field-error" role="alert">
          {error}
        </small>
      )}
    </label>
  );
}

function ValueFields({ value, field, onSave, coordinate = false, disabled }) {
  if (Array.isArray(value))
    return (
      <div className="edit-array">
        {value.map((v, i) => (
          <ValueFields
            key={i}
            value={v}
            field={field + '[' + i + ']'}
            onSave={onSave}
            coordinate={coordinate}
            disabled={disabled}
          />
        ))}
      </div>
    );
  if (value !== null && typeof value === 'object')
    return (
      <>
        {Object.entries(value).map(([key, v]) => (
          <ValueFields
            key={key}
            value={v}
            field={field + '.' + key}
            onSave={onSave}
            coordinate={coordinate}
            disabled={disabled}
          />
        ))}
      </>
    );
  return (
    <EditField
      label={field}
      value={value}
      coordinate={coordinate}
      disabled={disabled}
      onSave={(v) => onSave(field, v)}
    />
  );
}
const coordinates = new Set([
  'start',
  'end',
  'center',
  'at',
  'points',
  'p1',
  'p2',
  'line_at',
  'boundary',
  'holes',
  'parts',
  'openings',
]);
export default function EditFields({ selected, record, onEdit, busy }) {
  const [sourceIndex, setSourceIndex] = useState(0);
  useEffect(() => setSourceIndex(0), [selected?._key]);
  const source = selected.sources?.[sourceIndex];
  const original = getItem(record?.spec, source?.item);
  if (!original) return null;
  return (
    <section className="spec-editor">
      <h3>원본 값 수정</h3>
      {selected.sources.length > 1 && (
        <label>
          원본 조각
          <select
            value={sourceIndex}
            onChange={(e) => setSourceIndex(Number(e.target.value))}
          >
            {selected.sources.map((s, i) => (
              <option key={i} value={i}>
                {s.item} · 인스턴스 {i + 1}
              </option>
            ))}
          </select>
        </label>
      )}
      <code>{source.item}</code>
      <p className="hint">
        Enter 또는 칸을 벗어나면 저장합니다. 좌표는 지역 mm입니다.
      </p>
      {Object.entries(original)
        .filter(([key]) => key !== 'kind')
        .map(([field, value]) => (
          <ValueFields
            key={field}
            field={field}
            value={value}
            coordinate={coordinates.has(field)}
            disabled={busy}
            onSave={(key, v) =>
              onEdit(selected._file, [
                { op: 'set', target: source.item, field: key, value: v },
              ])
            }
          />
        ))}
      {(source.groups || []).map((ref, i) => {
        const place = getItem(record.spec, ref.group)?.place?.[ref.place];
        if (!place) return null;
        const values = {
          at: [0, 0],
          rotation: 0,
          mirror: null,
          ...place,
          repeat: { count: 1, step: [0, 0], ...place.repeat },
          vars: Object.fromEntries(
            Object.entries(place.vars || {}).map(([key, v]) => [
              key,
              { start: 1, step: 1, ...v },
            ]),
          ),
        };
        return (
          <div className="place-editor" key={i}>
            <h3>그룹 배치 {i + 1}</h3>
            <code>
              {ref.group} · place {ref.place} · 반복 {ref.i}
            </code>
            {Object.entries(values).map(([field, value]) => (
              <ValueFields
                key={field}
                field={field}
                value={value}
                coordinate={field === 'at'}
                disabled={busy}
                onSave={(key, v) =>
                  onEdit(selected._file, [
                    {
                      op: 'set_place',
                      group: ref.group,
                      place: ref.place,
                      field: key,
                      value: v,
                    },
                  ])
                }
              />
            ))}
          </div>
        );
      })}
    </section>
  );
}

export function GridEditor({ selection, record, onEdit, busy, onClose }) {
  const { path, axis, name } = selection,
    values = record?.grid?.[axis];
  if (values?.[name] === undefined) return null;
  const spacing = gridSpacing(values, name);
  return (
    <section className="grid-editor">
      <div className="section-title">
        <h2>축 {name}</h2>
        <button className="text-button" onClick={onClose}>
          닫기
        </button>
      </div>
      <small>{path}</small>
      <EditField
        label="위치 (mm)"
        value={values[name]}
        disabled={busy}
        onSave={(value) =>
          onEdit(path, [{ op: 'set_grid', axis, name, value }])
        }
      />
      <p>
        이전 축 간격: {spacing.previous ?? '—'} mm
        <br />
        다음 축 간격: {spacing.next ?? '—'} mm
      </p>
    </section>
  );
}
