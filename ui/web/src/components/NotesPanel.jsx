import React, { useState } from 'react';
import { statusLabels, targetMissing, targetSummary } from '../utils/notes.js';

function NoteCard({ note, onFocus, onSave, missing, recent, active }) {
  const [reply, setReply] = useState('');
  const [busy, setBusy] = useState(false);
  const update = async (change) => {
    setBusy(true);
    try {
      return await onSave(note.id, change);
    } finally {
      setBusy(false);
    }
  };
  const submit = async (event) => {
    event.preventDefault();
    if (reply.trim() && (await update({ reply }))) setReply('');
  };
  return (
    <article
      className={
        'note-card ' + (recent ? 'recent ' : '') + (active ? 'active' : '')
      }
      onClick={(event) => {
        if (!event.target.closest('form,.note-actions')) onFocus(note);
      }}
    >
      <button
        className="note-heading"
        onClick={(event) => {
          event.stopPropagation();
          onFocus(note);
        }}
      >
        <strong>#{note.id.slice(1)}</strong>
        <span className={'note-status ' + note.status}>
          {statusLabels[note.status]}
        </span>
        <span>{note.author === 'claude' ? 'Claude' : '사용자'}</span>
      </button>
      <p className="note-text">{note.text}</p>
      <p className="note-target">{targetSummary(note.target)}</p>
      {note.target.kind === 'spec' && (
        <details className="note-source">
          <summary>원본 위치 · 그룹 인스턴스</summary>
          <pre>{JSON.stringify(note.target.sources, null, 2)}</pre>
        </details>
      )}
      {missing && <p className="error">대상이 바뀜</p>}
      <time className="note-time" dateTime={note.created}>
        {new Date(note.created).toLocaleString('ko-KR')}
      </time>
      <div className="note-replies">
        {note.replies.map((reply, index) => (
          <div className="note-reply" key={index}>
            <small>
              {reply.author === 'claude' ? 'Claude' : '사용자'} ·{' '}
              {new Date(reply.created).toLocaleString('ko-KR')}
            </small>
            <p>{reply.text}</p>
          </div>
        ))}
      </div>
      <div className="note-actions">
        {note.status !== 'resolved' && (
          <button
            disabled={busy}
            onClick={() => void update({ status: 'resolved' })}
          >
            해결
          </button>
        )}
        {note.status !== 'open' && (
          <button
            disabled={busy}
            onClick={() => void update({ status: 'open' })}
          >
            다시 열기
          </button>
        )}
      </div>
      <form className="note-reply-form" onSubmit={submit}>
        <label className="sr-only" htmlFor={'reply-' + note.id}>
          메모 {note.id} 답글
        </label>
        <textarea
          id={'reply-' + note.id}
          value={reply}
          onChange={(e) => setReply(e.target.value)}
          placeholder="답글 입력"
          rows={2}
        />
        <button disabled={busy || !reply.trim()} type="submit">
          답글 저장
        </button>
      </form>
    </article>
  );
}
export default function NotesPanel({
  notes,
  recent,
  activeId,
  prims,
  base,
  onFocus,
  onSave,
}) {
  // 기본은 해결 전 메모(열림·답함)를 보인다. Claude가 답한 메모도 사용자가 확인·해결하기 전까지 남긴다.
  const [filter, setFilter] = useState('open');
  const visible = notes.filter(
    (note) => filter === 'all' || note.status !== 'resolved',
  );
  return (
    <section className="notes-panel">
      <div className="section-title">
        <h2>메모 {notes.length}개</h2>
        <div className="notes-filter" aria-label="메모 필터">
          <button
            aria-pressed={filter === 'open'}
            onClick={() => setFilter('open')}
          >
            진행 중
          </button>
          <button
            aria-pressed={filter === 'all'}
            onClick={() => setFilter('all')}
          >
            전체
          </button>
        </div>
      </div>
      <p className="hint">
        M으로 메모 모드 · 클릭으로 지점 선택 · Shift + 끌기로 영역 선택
      </p>
      {!visible.length && <p className="empty">표시할 메모가 없습니다.</p>}
      {visible.map((note) => (
        <NoteCard
          key={note.id}
          note={note}
          missing={targetMissing(note.target, prims, base)}
          active={note.id === activeId}
          recent={recent.has(note.id)}
          onFocus={onFocus}
          onSave={onSave}
        />
      ))}
    </section>
  );
}
