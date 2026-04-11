import { useState } from 'react';
import StrategyCard from '../components/StrategyCard';
import StrategyForm from '../components/StrategyForm';

export default function Dashboard({ strategies, strategyTypes, onRefresh, onSelectStrategy }) {
  const [showForm, setShowForm] = useState(false);

  return (
    <div id="dashboard-page">
      {/* Action Bar */}
      <div className="action-bar">
        <div>
          <h1 className="action-bar__title">Estrategias</h1>
          <div className="action-bar__subtitle">
            Gestioná tus estrategias de trading algorítmico
          </div>
        </div>
        <button
          className="btn btn--primary"
          id="btn-new-strategy"
          onClick={() => setShowForm(true)}
        >
          ＋ Nueva Estrategia
        </button>
      </div>

      {/* Summary Stats */}
      {strategies.length > 0 && (
        <div className="summary-stats">
          <div className="summary-stat">
            <div className="summary-stat__label">Total Estrategias</div>
            <div className="summary-stat__value" style={{ color: 'var(--accent-purple-light)' }}>
              {strategies.length}
            </div>
          </div>
          <div className="summary-stat">
            <div className="summary-stat__label">Ejecutando</div>
            <div className="summary-stat__value" style={{ color: 'var(--color-profit)' }}>
              {strategies.filter(s => s.estado === 'running').length}
            </div>
          </div>
          <div className="summary-stat">
            <div className="summary-stat__label">Posiciones Totales</div>
            <div className="summary-stat__value" style={{ color: 'var(--text-primary)' }}>
              {strategies.reduce((acc, s) => acc + (s.n_posiciones || 0), 0)}
            </div>
          </div>
          <div className="summary-stat">
            <div className="summary-stat__label">P&L Total</div>
            <div className="summary-stat__value" style={{
              color: strategies.reduce((a, s) => a + (s.pnl_realizado || 0), 0) >= 0
                ? 'var(--color-profit)' : 'var(--color-loss)'
            }}>
              {(() => {
                const total = strategies.reduce((a, s) => a + (s.pnl_realizado || 0), 0);
                return `${total >= 0 ? '+' : ''}${total.toFixed(2)}`;
              })()}
            </div>
          </div>
        </div>
      )}

      {/* Strategy Grid */}
      {strategies.length === 0 ? (
        <div className="card" style={{ marginTop: '2rem' }}>
          <div className="empty-state">
            <div className="empty-state__icon">🚀</div>
            <div className="empty-state__title">Sin estrategias configuradas</div>
            <div className="empty-state__text">
              Creá tu primera estrategia para empezar a operar.
              Podés arrancar con GGAL Opciones en modo DRY-RUN.
            </div>
            <button
              className="btn btn--primary"
              style={{ marginTop: '1.5rem' }}
              onClick={() => setShowForm(true)}
            >
              ＋ Crear Primera Estrategia
            </button>
          </div>
        </div>
      ) : (
        <div className="strategy-grid">
          {strategies.map(s => (
            <StrategyCard
              key={s.id}
              strategy={s}
              onSelect={onSelectStrategy}
              onRefresh={onRefresh}
            />
          ))}
        </div>
      )}

      {/* Create Modal */}
      {showForm && (
        <StrategyForm
          strategyTypes={strategyTypes}
          onClose={() => setShowForm(false)}
          onCreated={onRefresh}
        />
      )}
    </div>
  );
}
