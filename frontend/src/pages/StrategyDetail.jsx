import { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../api/client';
import PositionsTable from '../components/PositionsTable';
import SignalsList from '../components/SignalsList';
import LogViewer from '../components/LogViewer';

export default function StrategyDetail({ strategyId, strategy, strategyTypes, onBack, onRefresh }) {
  const [logs, setLogs] = useState([]);
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState({ config: {} });
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState(null);

  // Auto-refresh logs every 5s
  const fetchLogs = useCallback(() => {
    if (!strategyId) return;
    api.getStrategyLogs(strategyId, 200)
      .then(setLogs)
      .catch(console.error);
  }, [strategyId]);

  useEffect(() => {
    fetchLogs();
    const interval = setInterval(fetchLogs, 5000);
    return () => clearInterval(interval);
  }, [fetchLogs, strategy?.estado]);

  // Initialize edit form when entering edit mode
  useEffect(() => {
    if (editing && strategy) {
      setEditForm({
        nombre: strategy.nombre || '',
        fondos_asignados: strategy.fondos_asignados || 0,
        dry_run: strategy.dry_run ?? true,
        activo: strategy.activo || 'GGAL',
        config: { ...(strategy.config || {}) },
      });
    }
  }, [editing, strategy]);

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
  
  let drawdownPct = 0;
  if (maxDrawdown > 0 && pnlTotal < 0) {
    drawdownPct = Math.min((Math.abs(pnlTotal) / maxDrawdown) * 100, 100);
  }

  // Get strategy type info for param labels/types
  const tipoInfo = strategyTypes?.[s.tipo_estrategia];

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

  // ── Edit handlers ──────────────────────────────────────────────────────

  function handleEditChange(key, value) {
    setEditForm(prev => ({ ...prev, [key]: value }));
  }

  function handleConfigChange(key, value, type) {
    const parsed = type === 'bool'  ? Boolean(value)
                 : type === 'float' ? parseFloat(value) || 0
                 : type === 'int'   ? parseInt(value) || 0
                 : value;
    setEditForm(prev => ({
      ...prev,
      config: { ...prev.config, [key]: parsed },
    }));
  }

  async function handleSave() {
    setSaving(true);
    setEditError(null);
    try {
      await api.updateStrategy(s.id, editForm);
      setEditing(false);
      onRefresh?.();
    } catch (err) {
      setEditError(err.message);
    } finally {
      setSaving(false);
    }
  }

  function handleCancelEdit() {
    setEditing(false);
    setEditError(null);
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
              <span style={{ fontWeight: 'bold', color: 'var(--color-warning)' }}>
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
              height: '8px', width: '100%', background: 'var(--bg-primary)', 
              borderRadius: '4px', marginTop: '0.5rem', overflow: 'hidden'
            }}>
              <div style={{ 
                height: '100%', width: `${drawdownPct}%`, 
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
            {s.fondos_asignados > 0 ? `$${s.fondos_asignados.toLocaleString('es-AR')}` : 'Sin límite'}
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

      {/* Two-column layout: Positions + Signals */}
      <div className="detail-grid">
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

      {/* ── LOGS — Live feed ────────────────────────────────────────────── */}
      <div className="card" style={{ marginTop: '1.25rem' }}>
        <div className="card__header">
          <span className="card__title">📋 Logs en Vivo</span>
          <div className="flex gap-2">
            <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', alignSelf: 'center' }}>
              Auto-refresh 5s
            </span>
            <button className="btn btn--ghost btn--sm" onClick={fetchLogs}>
              🔄 Refrescar
            </button>
          </div>
        </div>
        <div className="card__body">
          <LogViewer logs={logs.length > 0 ? logs : strategy?.logs || []} />
        </div>
      </div>

      {/* ── CONFIGURACIÓN — Editable cuando está detenida ─────────────── */}
      <div className="card" style={{ marginTop: '1.25rem' }}>
        <div className="card__header">
          <span className="card__title">⚙️ Configuración</span>
          {s.estado === 'stopped' && !editing && (
            <button className="btn btn--primary btn--sm" onClick={() => setEditing(true)}>
              ✏️ Editar
            </button>
          )}
          {editing && (
            <div className="flex gap-2">
              <button className="btn btn--ghost btn--sm" onClick={handleCancelEdit} disabled={saving}>
                Cancelar
              </button>
              <button className="btn btn--success btn--sm" onClick={handleSave} disabled={saving}>
                {saving ? <><span className="spinner spinner--sm" style={{ marginRight: '0.25rem' }}></span> Guardando...</> : '💾 Guardar'}
              </button>
            </div>
          )}
          {s.estado !== 'stopped' && !editing && (
            <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>
              Detené la estrategia para editar
            </span>
          )}
        </div>
        <div className="card__body">
          {editError && (
            <div style={{
              padding: '0.75rem 1rem', marginBottom: '1rem',
              background: 'var(--color-loss-dim)', border: '1px solid rgba(255,59,92,0.3)',
              borderRadius: 'var(--radius-md)', color: 'var(--color-loss)', fontSize: '0.85rem'
            }}>
              {editError}
            </div>
          )}

          {editing ? (
            /* ── Modo edición ──────────────────────────────────────── */
            <div>
              {/* Campos principales */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))', gap: '1rem', marginBottom: '1.5rem' }}>
                <div className="form-group">
                  <label className="form-label">Nombre</label>
                  <input
                    className="form-input"
                    value={editForm.nombre}
                    onChange={(e) => handleEditChange('nombre', e.target.value)}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Activo</label>
                  <input
                    className="form-input"
                    value={editForm.activo}
                    onChange={(e) => handleEditChange('activo', e.target.value.toUpperCase())}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Fondos Asignados (ARS)</label>
                  <input
                    className="form-input"
                    type="number"
                    min="0"
                    step="1000"
                    value={editForm.fondos_asignados || ''}
                    onChange={(e) => handleEditChange('fondos_asignados', parseFloat(e.target.value) || 0)}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Modo</label>
                  <div className="toggle-container" style={{ marginTop: '0.35rem' }}>
                    <input
                      type="checkbox"
                      className="toggle"
                      checked={!editForm.dry_run}
                      onChange={(e) => {
                        if (e.target.checked && !confirm('⚠️ LIVE enviará órdenes reales. ¿Seguro?')) return;
                        handleEditChange('dry_run', !e.target.checked);
                      }}
                    />
                    <span style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', marginLeft: '0.5rem' }}>
                      {editForm.dry_run ? '🧪 DRY-RUN' : '🔴 LIVE'}
                    </span>
                  </div>
                </div>
              </div>

              {/* Parámetros de estrategia */}
              {tipoInfo && (
                <>
                  <div style={{
                    fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-secondary)',
                    textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '0.75rem',
                    paddingTop: '1rem', borderTop: '1px solid var(--border-color)'
                  }}>
                    Parámetros — {tipoInfo.nombre}
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '0.75rem' }}>
                    {tipoInfo.params.map(p => (
                      <div className="form-group" key={p.key}>
                        <label className="form-label" htmlFor={`edit-${p.key}`}>{p.label}</label>
                        {p.descripcion && (
                          <div style={{
                            fontSize: '0.65rem', color: 'rgba(130, 80, 250, 0.8)',
                            marginBottom: '0.4rem', lineHeight: '1.2'
                          }}>
                            {p.descripcion}
                          </div>
                        )}
                        {p.type === 'bool' ? (
                          <div className="toggle-container" style={{ marginTop: '0.35rem' }}>
                            <input
                              type="checkbox"
                              className="toggle"
                              id={`edit-${p.key}`}
                              checked={!!(editForm.config[p.key] ?? p.default)}
                              onChange={(e) => handleConfigChange(p.key, e.target.checked, 'bool')}
                            />
                            <span style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', marginLeft: '0.5rem' }}>
                              {editForm.config[p.key] ?? p.default ? 'Activado' : 'Desactivado'}
                            </span>
                          </div>
                        ) : p.type === 'string' ? (
                          <input
                            className="form-input"
                            id={`edit-${p.key}`}
                            type="text"
                            value={editForm.config[p.key] ?? p.default}
                            onChange={(e) => handleConfigChange(p.key, e.target.value.toUpperCase(), p.type)}
                          />
                        ) : (
                          <input
                            className="form-input"
                            id={`edit-${p.key}`}
                            type="number"
                            step={p.type === 'float' ? '0.01' : '1'}
                            value={editForm.config[p.key] ?? p.default}
                            onChange={(e) => handleConfigChange(p.key, e.target.value, p.type)}
                          />
                        )}
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          ) : (
            /* ── Modo vista (read-only) ────────────────────────────── */
            <div style={{
              display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
              gap: '0.75rem'
            }}>
              {s.config && Object.entries(s.config).map(([key, val]) => {
                // Find the param info for better labels
                const paramInfo = tipoInfo?.params?.find(p => p.key === key);
                return (
                  <div key={key} style={{
                    background: 'var(--bg-primary)', borderRadius: 'var(--radius-sm)',
                    padding: '0.6rem 0.85rem'
                  }}>
                    <div style={{
                      fontSize: '0.65rem', color: 'var(--text-muted)',
                      textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '0.2rem'
                    }}>
                      {paramInfo?.label || key.replace(/_/g, ' ')}
                    </div>
                    <div style={{
                      fontSize: '0.9rem', fontWeight: 600,
                      fontFamily: 'var(--font-mono)', color: 'var(--text-primary)'
                    }}>
                      {typeof val === 'boolean' ? (val ? '✅ Sí' : '❌ No')
                       : typeof val === 'number' ? val.toLocaleString()
                       : String(val)}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
