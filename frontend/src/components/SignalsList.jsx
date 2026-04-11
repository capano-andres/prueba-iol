export default function SignalsList({ signals }) {
  if (!signals || signals.length === 0) {
    return (
      <div className="empty-state" style={{ padding: '1.5rem 1rem' }}>
        <div style={{ fontSize: '1.2rem', opacity: 0.3, marginBottom: '0.5rem' }}>📡</div>
        <div className="empty-state__text">Sin señales activas</div>
      </div>
    );
  }

  return (
    <div>
      {signals.map((sig, i) => (
        <div className="signal-item" key={i}>
          <span className={`signal-side signal-side--${sig.lado}`}>{sig.lado}</span>
          <span className="signal-symbol">{sig.simbolo}</span>
          <span className="signal-reason">{sig.razon}</span>
          <span className="signal-score">{(sig.score * 100).toFixed(1)}%</span>
        </div>
      ))}
    </div>
  );
}
