import React from 'react';
import EditFields from './EditFields.jsx';
export default function DetailsPanel({
  record,
  onEdit,
  busy,
  selected,
  item,
  instances,
  issues,
  onClear,
}) {
  return (
    <>
      <section>
        <div className="section-title">
          <h2>객체 정보</h2>
          {selected && (
            <button className="text-button" onClick={onClear}>
              선택 해제
            </button>
          )}
        </div>
        {!selected ? (
          <div className="selection-empty">
            <div className="crosshair">⌖</div>
            <strong>객체를 클릭해 보세요</strong>
            <p>
              원본 항목과 반복 배치 위치,
              <br />
              레이어와 좌표를 확인할 수 있습니다.
            </p>
          </div>
        ) : (
          <>
            <div className="object-kind">
              {selected.kind}
              <span>{selected.handle ? '기준 DXF' : '작업 명세'}</span>
            </div>
            <dl>
              {['layer', 'oid', 'path', 'handle']
                .filter((key) => selected[key] != null)
                .map((key) => (
                  <React.Fragment key={key}>
                    <dt>{key}</dt>
                    <dd>{selected[key] || '—'}</dd>
                  </React.Fragment>
                ))}
            </dl>
            {item && (
              <>
                <div className="instance-count">
                  이 항목은 {instances}곳에 반영됩니다
                </div>
                <h3>원본 위치 · sources</h3>
                {selected.sources.map((source, i) => (
                  <div className="source" key={i}>
                    <code>{source.item}</code>
                    {source.groups.map((group, j) => (
                      <p key={j}>
                        {group.group}
                        <br />
                        <span>
                          place {group.place} · 반복 {group.i}
                        </span>
                      </p>
                    ))}
                  </div>
                ))}
              </>
            )}
            {item && (
              <EditFields
                selected={selected}
                record={record}
                onEdit={onEdit}
                busy={busy}
              />
            )}
            <h3>세계 좌표 · mm</h3>
            <pre>
              {JSON.stringify(
                Object.fromEntries(
                  [
                    'points',
                    'bulges',
                    'center',
                    'radius',
                    'start_angle',
                    'end_angle',
                    'at',
                    'text',
                    'height',
                    'rotation',
                    'align',
                    'name',
                    'loops',
                    'angle',
                  ]
                    .filter(
                      (key) =>
                        selected[key] != null &&
                        (!Array.isArray(selected[key]) || selected[key].length),
                    )
                    .map((key) => [key, selected[key]]),
                ),
                null,
                2,
              )}
            </pre>
          </>
        )}
      </section>
      {issues.length > 0 && (
        <section className="issues">
          <h2>
            확인할 내용 <span>{issues.length}</span>
          </h2>
          {issues.map((issue, i) => (
            <div className={issue.error ? 'issue invalid' : 'issue'} key={i}>
              <small>{issue.path}</small>
              <p>{issue.message}</p>
            </div>
          ))}
        </section>
      )}
    </>
  );
}
