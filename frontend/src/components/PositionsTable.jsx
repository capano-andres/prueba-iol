export default function PositionsTable({ positions }) {
  if (!positions || positions.length === 0) {
    return (
      <div className="empty-state" style={{ padding: '2rem 1rem' }}>
        <div style={{ fontSize: '1.5rem', opacity: 0.3, marginBottom: '0.5rem' }}>📭</div>
        <div className="empty-state__text">Sin posiciones abiertas</div>
      </div>
    );
  }

  return (
    <div className="table-container">
      <table className="data-table" id="positions-table">
        <thead>
          <tr>
            <th>Símbolo</th>
            <th>Tipo</th>
            <th>Lado</th>
            <th>Cant.</th>
            <th>Precio Ap.</th>
            <th>Mid Actual</th>
            <th>P&L NR</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((pos) => {
            const pnl = pos.pnl_no_realizado;
            const pnlClass = pnl > 0 ? 'text-profit' : pnl < 0 ? 'text-loss' : 'text-muted';
            return (
              <tr key={pos.id}>
                <td style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{pos.simbolo}</td>
                <td>
                  <span className={`strategy-card__tag ${pos.tipo === 'CALL' ? 'tag--asset' : 'tag--type'}`}>
                    {pos.tipo}
                  </span>
                </td>
                <td>
                  <span className={`signal-side signal-side--${pos.lado}`}>{pos.lado}</span>
                </td>
                <td>{pos.cantidad}</td>
                <td>${pos.precio_apertura?.toFixed(2)}</td>
                <td>{pos.mid_actual ? `$${pos.mid_actual.toFixed(2)}` : 'N/D'}</td>
                <td className={pnlClass}>
                  {pnl != null ? `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}` : 'N/D'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
