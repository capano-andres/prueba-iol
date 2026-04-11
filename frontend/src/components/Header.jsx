export default function Header({ connected, status }) {
  const now = new Date();
  const timeStr = now.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

  const isMarketHours = now.getHours() >= 11 && now.getHours() < 17;

  return (
    <header className="app-header" id="app-header">
      <div className="app-header__brand">
        <div className="app-header__logo">⚡</div>
        <div>
          <div className="app-header__title">IOL Trading Platform</div>
        </div>
      </div>

      <div className="app-header__status">
        <div className="status-badge">
          <span className={`status-dot ${connected ? 'status-dot--connected' : 'status-dot--disconnected'}`} />
          {connected ? 'Conectado' : 'Desconectado'}
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
