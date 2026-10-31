import { useCallback, useEffect, useRef, useState } from 'react';

export default function useNotes() {
  const [data, setData] = useState({ version: 1, rev: 0, notes: [] });
  const [error, setError] = useState('');
  const [recent, setRecent] = useState(new Set());
  const current = useRef(data),
    alive = useRef(true),
    sequence = useRef(0),
    flashes = useRef(new Map()),
    conflict = useRef(false);
  const accept = useCallback((next) => {
    if (!alive.current) return;
    for (const note of next.notes) {
      const before = current.current.notes.find((n) => n.id === note.id);
      if (before && note.replies.length > before.replies.length)
        flashes.current.set(note.id, Date.now() + 3000);
    }
    current.current = next;
    setData(next);
    setRecent(new Set(flashes.current.keys()));
  }, []);
  const reload = useCallback(async () => {
    const version = ++sequence.current;
    try {
      const response = await fetch('/api/notes', { cache: 'no-store' });
      if (!response.ok)
        throw Error('메모를 읽지 못했습니다 (' + response.status + ')');
      const next = await response.json();
      if (sequence.current === version) {
        accept(next);
        if (!conflict.current) setError('');
      }
    } catch (e) {
      if (alive.current) setError(e.message);
    }
  }, [accept]);
  useEffect(() => {
    alive.current = true;
    void reload();
    const timer = setInterval(() => {
      let expired = false;
      for (const [id, until] of flashes.current)
        if (until <= Date.now()) {
          flashes.current.delete(id);
          expired = true;
        }
      if (expired) setRecent(new Set(flashes.current.keys()));
    }, 250);
    return () => {
      alive.current = false;
      clearInterval(timer);
    };
  }, [reload]);
  const save = useCallback(
    async (id, change) => {
      try {
        const response = await fetch(
          '/api/notes' + (id ? '/' + encodeURIComponent(id) : ''),
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ rev: current.current.rev, ...change }),
          },
        );
        if (response.status === 409) {
          ++sequence.current;
          const next = await response.json();
          conflict.current = true;
          if (next.rev >= current.current.rev) accept(next);
          setError(
            '다른 곳에서 메모가 변경되어 다시 불러왔습니다. 내용을 확인하고 다시 저장해 주세요.',
          );
          return false;
        }
        if (!response.ok)
          throw Error(
            '메모를 저장하지 못했습니다 (' +
              response.status +
              '): ' +
              (await response.text()),
          );
        ++sequence.current;
        const next = await response.json();
        if (next.rev >= current.current.rev) accept(next);
        conflict.current = false;
        setError('');
        return true;
      } catch (e) {
        if (alive.current) setError(e.message);
        return false;
      }
    },
    [accept],
  );
  return { notes: data.notes, rev: data.rev, error, recent, reload, save };
}
