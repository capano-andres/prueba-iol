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
  const pnlRealizado = s.pnl_realizado || 0;
  const pnlNoRealizado = s.pnl_no_realizado_total || 0;
  const pnlTotal = pnlRealizado + pnlNoRealizado;
  
  const winStats = s.win_stats || { total: 0, ganadas: 0, perdidas: 0, win_rate: 0 };
  const maxDrawdown = s.config?.max_drawdown_ars || 0;
  
  // Calcular % de drawdown consumido (solo si estamos perdiendo)
  let drawdownPct = 0;
  if (maxDrawdown > 0 && pnlTotal < 0) {
    drawdownPct = Math.min((Math.abs(pnlTotal) / maxDrawdown) * 100, 100);
  }

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
        {/* P&L Total */}
        <div className="summary-stat" style={{ borderLeft: '3px solid', borderLeftColor: pnlTotal >= 0 ? 'var(--color-profit)' : 'var(--color-loss)', paddingLeft: '1rem' }}>
          <div className="summary-stat__label">P&L Total (Realizado + Vivo)</div>
          <div className="summary-stat__value" style={{
            color: pnlTotal >= 0 ? 'var(--color-profit)' : 'var(--color-loss)',
            fontSize: '1.5rem'
          }}>
            {pnlTotal >= 0 ? '+' : ''}{pnlTotal.toFixed(2)} ARS
          </div>
          <div style={{ fontSize: '0.75rem', marginTop: '0.25rem', color: 'var(--text-muted)' }}>
            Realizado: <span style={{ color: pnlRealizado >= 0 ? 'var(--color-profit)' : 'var(--color-loss)' }}>{pnlRealizado >= 0 ? '+' : ''}{pnlRealizado.toFixed(2)}</span> | 
            Vivo: <span style={{ color: pnlNoRealizado >= 0 ? 'var(--color-profit)' : 'var(--color-loss)' }}>{pnlNoRealizado >= 0 ? '+' : ''}{pnlNoRealizado.toFixed(2)}</span>
          </div>
        </div>

        {/* Riesgo (Griegas) */}
        <div className="summary-stat">
          <div className="summary-stat__label">Exposición Direccional (Griegas Netas)</div>
          <div style={{ display: 'flex', gap: '1rem', marginTop: '0.5rem' }}>
            <div style={{ background: 'var(--bg-primary)', padding: '0.25rem 0.5rem', borderRadius: '4px' }}>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginRight: '0.5rem' }}>Δ Delta</span>
              <span style={{ 
                fontWeight: 'bold', 
                color: s.net_delta > 0 ? 'var(--color-profit)' : s.net_delta < 0 ? 'var(--color-loss)' : 'var(--text-primary)' 
              }}>
                {s.net_delta ? s.net_delta.toFixed(2) : '0.00'}
              </span>
            </div>
            <div style={{ background: 'var(--bg-primary)', padding: '0.25rem 0.5rem', borderRadius: '4px' }}>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginRight: '0.5rem' }}>ν Vega</span>
              <span style={{ 
                fontWeight: 'bold', 
                color: 'var(--color-warning)'
              }}>
                {s.net_vega ? s.net_vega.toFixed(2) : '0.00'}
              </span>
            </div>
          </div>
        </div>

        {/* Win Rate */}
        <div className="summary-stat">
          <div className="summary-stat__label">Win Rate (Histórico)</div>
          <div className="summary-stat__value" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <span>{winStats.win_rate.toFixed(1)}%</span>
            <span style={{ fontSize: '0.75rem', fontWeight: 'normal', color: 'var(--text-muted)' }}>
              ({winStats.ganadas}W / {winStats.perdidas}L)
            </span>
          </div>
        </div>

        {/* Max Drawdown */}
        {maxDrawdown > 0 && (
          <div className="summary-stat" style={{ gridColumn: '1 / -1' }}>
            <div className="summary-stat__label" style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>Distancia al Stop-Loss (Max Drawdown: -{maxDrawdown} ARS)</span>
              <span>{drawdownPct.toFixed(1)}%</span>
            </div>
            <div style={{ 
              height: '8px', 
              width: '100%', 
              background: 'var(--bg-primary)', 
              borderRadius: '4px', 
              marginTop: '0.5rem',
              overflow: 'hidden'
            }}>
              <div style={{ 
                height: '100%', 
                width: `${drawdownPct}%`, 
                background: drawdownPct > 80 ? 'var(--color-loss)' : drawdownPct > 50 ? 'var(--color-warning)' : 'var(--color-profit)',
                transition: 'width 0.3s ease, background 0.3s ease'
              }} />
            </div>
          </div>
        )}

      </div>
      
      {/* Secondary Stats */}
      <div className="summary-stats" style={{ marginTop: '1rem', paddingTop: '1rem', borderTop: '1px solid var(--border-color)' }}>
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
