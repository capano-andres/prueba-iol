import { useState, useEffect } from 'react';
import { api } from '../api/client';
import PositionsTable from '../components/PositionsTable';
import SignalsList from '../components/SignalsList';
import LogViewer from '../components/LogViewer';

export default function StrategyDetail({ strategyId, strategy, onBack, onRefresh }) {
  const [logs, setLogs] = useState([]);

  useEffect(() => {
    if (!strategyId) return;
    api.getStrategyLogs(strategyId, 100)
      .then(setLogs)
      .catch(console.error);
  }, [strategyId, strategy?.estado]);

  if (!strategy) {
    return (
      <div>
        <button className="detail-header__back" onClick={onBack}>← Volver al Dashboard</button>
        <div className="card" style={{ marginTop: '1rem' }}>
          <div className="empty-state">
            <div className="empty-state__title">Estrategia no encontrada</div>
          </div>
        </div>
      </div>
    );
  }

  const s = strategy;
  const pnl = s.pnl_realizado || 0;

  async function handleStart() {
    try { await api.startStrategy(s.id); onRefresh?.(); }
    catch (err) { alert(`Error: ${err.message}`); }
  }
  async function handlePause() {
    try { await api.pauseStrategy(s.id); onRefresh?.(); }
    catch (err) { alert(`Error: ${err.message}`); }
  }
  async function handleStop() {
    if (!confirm('¿Detener esta estrategia?')) return;
    try { await api.stopStrategy(s.id); onRefresh?.(); }
    catch (err) { alert(`Error: ${err.message}`); }
  }

  return (
    <div id="strategy-detail-page">
      {/* Header */}
      <div className="detail-header">
        <div>
          <button className="detail-header__back" onClick={onBack}>
            ← Volver al Dashboard
          </button>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 700, marginTop: '0.5rem' }}>
            {s.nombre}
          </h1>
          <div className="flex gap-2 mt-2">
            <span className="strategy-card__tag tag--asset">{s.activo}</span>
            <span className={`strategy-card__tag ${s.dry_run ? 'tag--dry-run' : 'tag--live'}`}>
              {s.dry_run ? 'DRY-RUN' : '🔴 LIVE'}
            </span>
            <span className={`strategy-card__status-badge strategy-card__status-badge--${s.estado}`}>
              {s.estado.toUpperCase()}
            </span>
          </div>
        </div>
        <div className="flex gap-2">
          {s.estado === 'stopped' && (
            <button className="btn btn--success" onClick={handleStart}>▶ Iniciar</button>
          )}
          {s.estado === 'running' && (
            <button className="btn btn--warning" onClick={handlePause}>⏸ Pausar</button>
          )}
          {s.estado === 'paused' && (
            <button className="btn btn--success" onClick={handleStart}>▶ Reanudar</button>
          )}
          {s.estado !== 'stopped' && (
            <button className="btn btn--danger" onClick={handleStop}>⏹ Detener</button>
          )}
        </div>
      </div>

      {/* Summary Stats */}
      <div className="summary-stats">
        <div className="summary-stat">
          <div className="summary-stat__label">P&L Realizado</div>
          <div className="summary-stat__value" style={{
            color: pnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)'
          }}>
            {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)} ARS
          </div>
        </div>
        <div className="summary-stat">
          <div className="summary-stat__label">Posiciones</div>
          <div className="summary-stat__value">{s.n_posiciones}</div>
        </div>
        <div className="summary-stat">
          <div className="summary-stat__label">Fondos Asignados</div>
          <div className="summary-stat__value">
            {s.fondos_asignados > 0
              ? `$${s.fondos_asignados.toLocaleString('es-AR')}`
              : 'Sin límite'}
          </div>
        </div>
        <div className="summary-stat">
          <div className="summary-stat__label">Nominal en Uso</div>
          <div className="summary-stat__value">
            ${(s.nominal_en_uso || 0).toLocaleString('es-AR', { minimumFractionDigits: 2 })}
          </div>
        </div>
        <div className="summary-stat">
          <div className="summary-stat__label">Spot {s.activo}</div>
          <div className="summary-stat__value">
            {s.spot ? `$${s.spot.toLocaleString('es-AR', { minimumFractionDigits: 2 })}` : 'N/D'}
          </div>
        </div>
      </div>

      {/* Two-column layout */}
      <div className="detail-grid">
        {/* Left: Positions */}
        <div className="card">
          <div className="card__header">
            <span className="card__title">Posiciones Abiertas</span>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
              {s.posiciones_abiertas?.length || 0} activas
            </span>
          </div>
          <div className="card__body" style={{ padding: 0, paddingTop: 0 }}>
            <PositionsTable positions={s.posiciones_abiertas} />
          </div>
        </div>

        {/* Right: Signals */}
        <div className="card">
          <div className="card__header">
            <span className="card__title">Últimas Señales</span>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
              Top 5 por score
            </span>
          </div>
          <div className="card__body">
            <SignalsList signals={s.last_signals} />
          </div>
        </div>
      </div>

      {/* Logs */}
      <div className="card" style={{ marginTop: '1.25rem' }}>
        <div className="card__header">
          <span className="card__title">Logs</span>
          <button className="btn btn--ghost btn--sm" onClick={() => {
            api.getStrategyLogs(strategyId, 100).then(setLogs).catch(console.error);
          }}>
            🔄 Refrescar
          </button>
        </div>
        <div className="card__body">
          <LogViewer logs={logs.length > 0 ? logs : strategy?.logs || []} />
        </div>
      </div>

      {/* Config */}
      <div className="card" style={{ marginTop: '1.25rem' }}>
        <div className="card__header">
          <span className="card__title">Configuración</span>
        </div>
        <div className="card__body">
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
            gap: '0.75rem'
          }}>
            {s.config && Object.entries(s.config).map(([key, val]) => (
              <div key={key} style={{
                background: 'var(--bg-primary)', borderRadius: 'var(--radius-sm)',
                padding: '0.6rem 0.85rem'
              }}>
                <div style={{
                  fontSize: '0.65rem', color: 'var(--text-muted)',
                  textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '0.2rem'
                }}>
                  {key.replace(/_/g, ' ')}
                </div>
                <div style={{
                  fontSize: '0.9rem', fontWeight: 600,
                  fontFamily: 'var(--font-mono)', color: 'var(--text-primary)'
                }}>
                  {typeof val === 'number' ? val.toLocaleString() : String(val)}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
