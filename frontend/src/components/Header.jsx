import { useState } from 'react';
import { api } from '../api/client';

export default function Header({ connected, status, page, onNavigate }) {
  const [reconnecting, setReconnecting] = useState(false);
  const [reconnectStatus, setReconnectStatus] = useState(null); // 'ok' | 'error' | null

  const now = new Date();
  const timeStr = now.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

  const day = now.getDay();
  const isMarketHours = day !== 0 && day !== 6 && (
    (now.getHours() === 10 && now.getMinutes() >= 30) ||
    (now.getHours() > 10 && now.getHours() < 17)
  );

  async function handleReconnect() {
    setReconnecting(true);
    setReconnectStatus(null);
    try {
      await api.reconnect();
      setReconnectStatus('ok');
      setTimeout(() => setReconnectStatus(null), 3000);
    } catch (err) {
      setReconnectStatus('error');
      setTimeout(() => setReconnectStatus(null), 5000);
    } finally {
      setReconnecting(false);
    }
  }

  return (
    <header className="app-header" id="app-header">
      <div className="app-header__brand">
        <div className="app-header__logo">⚡</div>
        <div>
          <div className="app-header__title">IOL Trading Platform</div>
        </div>
      </div>

      {onNavigate && (
        <div className="app-header__nav" style={{ display: 'flex', gap: '0.5rem', flex: 1, marginLeft: '2rem', flexWrap: 'wrap' }}>
          <button 
            className={`btn btn--sm ${page === 'dashboard' || page === 'detail' ? 'btn--primary' : 'btn--ghost'}`} 
            onClick={() => onNavigate('dashboard')}
          >
            📊 Estrategias
          </button>
          <button 
            className={`btn btn--sm ${page === 'trading' ? 'btn--primary' : 'btn--ghost'}`} 
            onClick={() => onNavigate('trading')}
          >
            📈 Trading
          </button>
          <button 
            className={`btn btn--sm ${page === 'portfolio' ? 'btn--primary' : 'btn--ghost'}`} 
            onClick={() => onNavigate('portfolio')}
          >
            💼 Mi Cuenta
          </button>
        </div>
      )}

      <div className="app-header__status">
        {/* Connection status + Reconnect button */}
        <div className="status-badge" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span className={`status-dot ${connected ? 'status-dot--connected' : 'status-dot--disconnected'}`} />
          {connected ? 'Conectado' : 'Desconectado'}
          <button
            className="btn btn--sm btn--ghost"
            onClick={handleReconnect}
            disabled={reconnecting}
            title="Forzar re-autenticación con IOL"
            style={{
              padding: '0.15rem 0.4rem',
              fontSize: '0.65rem',
              marginLeft: '0.25rem',
              borderColor: reconnectStatus === 'ok' ? 'var(--color-profit)' 
                         : reconnectStatus === 'error' ? 'var(--color-loss)' 
                         : undefined,
              color: reconnectStatus === 'ok' ? 'var(--color-profit)' 
                   : reconnectStatus === 'error' ? 'var(--color-loss)' 
                   : undefined,
            }}
          >
            {reconnecting ? '⏳' 
              : reconnectStatus === 'ok' ? '✅' 
              : reconnectStatus === 'error' ? '❌' 
              : '🔄'}
          </button>
        </div>

        <div className="status-badge">
          🕐 {timeStr}
        </div>

        <div className="status-badge">
          {isMarketHours ? '🟢 Mercado abierto' : '🔴 Mercado cerrado'}
        </div>

        {status && (
          <div className="status-badge">
            📊 {status.n_running} activas / {status.n_strategies} total
          </div>
        )}

        {status && (
          <div className="status-badge" style={{ fontFamily: 'var(--font-mono)' }}>
            <span style={{ color: status.pnl_total >= 0 ? 'var(--color-profit)' : 'var(--color-loss)' }}>
              P&L {status.pnl_total >= 0 ? '+' : ''}{status.pnl_total?.toFixed(2) || '0.00'}
            </span>
          </div>
        )}
      </div>
    </header>
  );
}
