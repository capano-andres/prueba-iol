import { api } from '../api/client';

export default function StrategyCard({ strategy, onSelect, onRefresh }) {
  const s = strategy;
  const pnl = s.pnl_realizado || 0;
  const pnlClass = pnl > 0 ? 'metric__value--profit' : pnl < 0 ? 'metric__value--loss' : 'metric__value--neutral';

  const fondosUsados = s.fondos_asignados > 0
    ? ((s.nominal_en_uso / s.fondos_asignados) * 100).toFixed(0)
    : 0;

  async function handleStart() {
    try {
      await api.startStrategy(s.id);
      onRefresh?.();
    } catch (err) {
      alert(`Error: ${err.message}`);
    }
  }

  async function handlePause() {
    try {
      await api.pauseStrategy(s.id);
      onRefresh?.();
    } catch (err) {
      alert(`Error: ${err.message}`);
    }
  }

  async function handleStop() {
    if (!confirm('¿Detener esta estrategia? Se cerrarán las posiciones abiertas.')) return;
    try {
      await api.stopStrategy(s.id);
      onRefresh?.();
    } catch (err) {
      alert(`Error: ${err.message}`);
    }
  }

  async function handleDelete() {
    if (!confirm(`¿Eliminar "${s.nombre}"? Esta acción no se puede deshacer.`)) return;
    try {
      await api.deleteStrategy(s.id);
      onRefresh?.();
    } catch (err) {
      alert(`Error: ${err.message}`);
    }
  }

  return (
    <div
      className={`card card--interactive strategy-card strategy-card--${s.estado}`}
      id={`strategy-${s.id}`}
    >
      {/* Top section */}
      <div className="strategy-card__top">
        <div>
          <div className="strategy-card__name" onClick={() => onSelect?.(s.id)} style={{ cursor: 'pointer' }}>
            {s.nombre}
          </div>
          <div className="strategy-card__meta">
            <span className="strategy-card__tag tag--asset">{s.activo}</span>
            <span className="strategy-card__tag tag--type">
              {s.tipo_estrategia === 'options_mispricing' ? 'BSM Mispricing' : s.tipo_estrategia}
            </span>
            <span className={`strategy-card__tag ${s.dry_run ? 'tag--dry-run' : 'tag--live'}`}>
              {s.dry_run ? 'DRY-RUN' : '🔴 LIVE'}
            </span>
          </div>
        </div>
        <div className={`strategy-card__status-badge strategy-card__status-badge--${s.estado}`}>
          <span className="status-dot" style={{
            background: s.estado === 'running' ? 'var(--status-running)' :
                         s.estado === 'paused' ? 'var(--status-paused)' : 'var(--status-stopped)',
            width: 6, height: 6
          }} />
          {s.estado}
        </div>
      </div>

      {/* Metrics */}
      <div className="strategy-card__metrics">
        <div className="metric">
          <span className="metric__label">P&L Realizado</span>
          <span className={`metric__value ${pnlClass}`}>
            {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
          </span>
        </div>
        <div className="metric">
          <span className="metric__label">Posiciones</span>
          <span className="metric__value metric__value--neutral">{s.n_posiciones}</span>
        </div>
        <div className="metric">
          <span className="metric__label">Fondos</span>
          <span className="metric__value metric__value--neutral">
            {s.fondos_asignados > 0
              ? `${fondosUsados}%`
              : '∞'}
          </span>
        </div>
      </div>

      {/* Spot price */}
      {s.spot && (
        <div style={{ padding: '0 1.5rem 0.5rem', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
          Spot: <span className="text-mono" style={{ color: 'var(--text-primary)' }}>
            ${s.spot?.toLocaleString('es-AR', { minimumFractionDigits: 2 })}
          </span>
        </div>
      )}

      {/* Actions */}
      <div className="strategy-card__actions">
        {s.estado === 'stopped' && (
          <button className="btn btn--success btn--sm" onClick={handleStart} id={`btn-start-${s.id}`}>
            ▶ Iniciar
          </button>
        )}
        {s.estado === 'running' && (
          <button className="btn btn--warning btn--sm" onClick={handlePause} id={`btn-pause-${s.id}`}>
            ⏸ Pausar
          </button>
        )}
        {s.estado === 'paused' && (
          <button className="btn btn--success btn--sm" onClick={handleStart} id={`btn-resume-${s.id}`}>
            ▶ Reanudar
          </button>
        )}
        {s.estado !== 'stopped' && (
          <button className="btn btn--danger btn--sm" onClick={handleStop} id={`btn-stop-${s.id}`}>
            ⏹ Detener
          </button>
        )}
        <button className="btn btn--ghost btn--sm" onClick={() => onSelect?.(s.id)} id={`btn-detail-${s.id}`}>
          📋 Detalle
        </button>
        {s.estado === 'stopped' && (
          <button className="btn btn--danger btn--sm" onClick={handleDelete} id={`btn-delete-${s.id}`}
            style={{ marginLeft: 'auto' }}>
            🗑
          </button>
        )}
      </div>
    </div>
  );
}
