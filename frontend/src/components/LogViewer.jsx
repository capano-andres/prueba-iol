import { useRef, useEffect } from 'react';

export default function LogViewer({ logs }) {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs?.length]);

  return (
    <div className="log-viewer" id="log-viewer">
      {(!logs || logs.length === 0) ? (
        <div style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '1rem' }}>
          Sin logs aún. Iniciá la estrategia para ver actividad.
        </div>
      ) : (
        logs.map((log, i) => (
          <div className="log-entry" key={i}>
            <span className="log-entry__time">{log.ts}</span>
            <span className={`log-entry__level log-entry__level--${log.level}`}>
              {log.level}
            </span>
            <span className="log-entry__msg">{log.message}</span>
          </div>
        ))
      )}
      <div ref={bottomRef} />
    </div>
  );
}
