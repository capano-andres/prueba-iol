import { useState, useEffect } from 'react';
import { api } from '../api/client';
import AccountPanel from '../components/AccountPanel';

export default function Portfolio() {
  const [portfolio, setPortfolio] = useState(null);
  const [operations, setOperations] = useState([]);
  const [loadingVars, setLoadingVars] = useState({ pf: true, op: true });

  useEffect(() => {
    let mounted = true;
    
    const fetchPortfolio = async () => {
      try {
        const data = await api.getPortfolio();
        if (mounted && data && !data.error) {
          setPortfolio(data);
        }
      } catch (err) {
        console.error("Error al obtener portafolio:", err);
      } finally {
        if (mounted) setLoadingVars(prev => ({ ...prev, pf: false }));
      }
    };

    const fetchOperations = async () => {
      try {
        const data = await api.getOperations();
        if (mounted && Array.isArray(data)) {
          // Las operaciones suelen venir ordenadas, pero por si acaso aplicamos reverse o sort por fecha
          setOperations(data);
        }
      } catch (err) {
        console.error("Error al obtener operaciones:", err);
      } finally {
        if (mounted) setLoadingVars(prev => ({ ...prev, op: false }));
      }
    };

    fetchPortfolio();
    fetchOperations();

    const interval = setInterval(() => {
      fetchPortfolio();
      fetchOperations();
    }, 30000); // 30s de refresco para no sobrecargar

    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  const formatARS = (val) => new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'ARS' }).format(val || 0);

  return (
    <div className="portfolio-page" style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      
      {/* Action Bar */}
      <div className="action-bar">
        <div>
          <h1 className="action-bar__title">Mi Cuenta</h1>
          <div className="action-bar__subtitle">
            Resumen de capital, portafolio actual y últimas operaciones en IOL
          </div>
        </div>
      </div>

      <AccountPanel />

      <div className="portfolio-grid" style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '2rem' }}>
        
        {/* Activos en Cartera */}
        <div className="card">
          <div className="card__header">
            <div className="card__title">Portafolio Actual</div>
          </div>
          <div className="card__body" style={{ padding: 0 }}>
            {loadingVars.pf ? (
              <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)' }}>Cargando portafolio...</div>
            ) : portfolio && portfolio.activos && portfolio.activos.length > 0 ? (
              <div className="table-container">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Símbolo</th>
                      <th className="hide-mobile">Descripción</th>
                      <th style={{ textAlign: 'right' }}>Cantidad</th>
                      <th style={{ textAlign: 'right' }}>Precio Prom.</th>
                      <th style={{ textAlign: 'right' }}>Último Precio</th>
                      <th style={{ textAlign: 'right' }} className="hide-mobile">Valorización</th>
                      <th style={{ textAlign: 'right' }}>Var. Diaria</th>
                      <th style={{ textAlign: 'right' }}>Rendimiento %</th>
                      <th style={{ textAlign: 'right' }}>Total (ARS)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {portfolio.activos.map(activo => {
                      const gananciaPct = activo.gananciaPorcentaje || 0;
                      const gananciaDinero = activo.gananciaDinero || 0;
                      
                      return (
                      <tr key={`${activo.titulo?.simbolo}`}>
                        <td>
                          <span className="strategy-card__tag tag--asset">{activo.titulo?.simbolo}</span>
                        </td>
                        <td className="hide-mobile">{activo.titulo?.descripcion}</td>
                        <td style={{ textAlign: 'right' }}>{activo.cantidad}</td>
                        <td style={{ textAlign: 'right' }}>{formatARS(activo.ppc)}</td>
                        <td style={{ textAlign: 'right' }}>{formatARS(activo.ultimoPrecio)}</td>
                        <td style={{ textAlign: 'right', fontWeight: 'bold' }} className="hide-mobile">{formatARS(activo.valorizado)}</td>
                        <td style={{ 
                          textAlign: 'right', 
                          color: activo.variacionDiaria >= 0 ? 'var(--color-profit)' : 'var(--color-loss)' 
                        }}>
                          {activo.variacionDiaria >= 0 ? '+' : ''}{activo.variacionDiaria?.toFixed(2)}%
                        </td>
                        <td style={{ 
                          textAlign: 'right', 
                          fontWeight: '600',
                          color: gananciaPct >= 0 ? 'var(--color-profit)' : 'var(--color-loss)' 
                        }}>
                          {gananciaPct >= 0 ? '+' : ''}{gananciaPct.toFixed(2)}%
                        </td>
                        <td style={{ 
                          textAlign: 'right', 
                          color: gananciaDinero >= 0 ? 'var(--color-profit)' : 'var(--color-loss)' 
                        }}>
                          {gananciaDinero >= 0 ? '+' : ''}{formatARS(gananciaDinero)}
                        </td>
                      </tr>
                    )})}
                  </tbody>
                </table>
              </div>
            ) : (
              <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)' }}>
                No tienes activos en este portafolio.
              </div>
            )}
          </div>
        </div>

        {/* Últimas Operaciones */}
        <div className="card">
          <div className="card__header">
            <div className="card__title">Últimas Operaciones</div>
          </div>
          <div className="card__body" style={{ padding: 0 }}>
            {loadingVars.op ? (
              <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)' }}>Cargando operaciones...</div>
            ) : operations && operations.length > 0 ? (
              <div className="table-container" style={{ maxHeight: '400px', overflowY: 'auto' }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Fecha</th>
                      <th>Operación</th>
                      <th>Símbolo</th>
                      <th style={{ textAlign: 'right' }}>Cantidad</th>
                      <th style={{ textAlign: 'right' }}>Precio</th>
                      <th style={{ textAlign: 'right' }}>Monto Total</th>
                      <th style={{ textAlign: 'center' }}>Estado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {operations.slice(0, 50).map(op => {
                      const fechaDate = new Date(op.fechaOrden || op.fecha);
                      const isVenta = String(op.tipo).toLowerCase().includes('venta');
                      const montoTotal = op.montoOperado || op.monto || ((op.cantidadOperada || op.cantidad || 0) * (op.precioOperado || op.precio || 0));
                      
                      return (
                        <tr key={op.numero}>
                          <td style={{ whiteSpace: 'nowrap' }}>
                            <span className="hide-mobile">{fechaDate.toLocaleDateString('es-AR')} </span>
                            {fechaDate.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' })}
                          </td>
                          <td>
                            <span className={`strategy-card__tag ${isVenta ? 'tag--live' : 'tag--type'}`} style={{ minWidth: '60px', textAlign: 'center', display: 'inline-block', whiteSpace: 'nowrap' }}>
                              {op.tipo}
                            </span>
                          </td>
                          <td>
                            <span className="strategy-card__tag tag--asset">{op.activo?.simbolo || op.simbolo || 'N/A'}</span>
                          </td>
                          <td style={{ textAlign: 'right' }}>{op.cantidadOperada || op.cantidad || 0}</td>
                          <td style={{ textAlign: 'right' }}>{formatARS(op.precioOperado || op.precio || 0)}</td>
                          <td style={{ textAlign: 'right', fontWeight: '500' }}>{formatARS(montoTotal)}</td>
                          <td style={{ textAlign: 'center' }}>
                            <span style={{ 
                              color: op.estado === 'terminada' ? 'var(--color-profit)' 
                                   : op.estado === 'cancelada' || op.estado === 'rechazada' ? 'var(--color-loss)' 
                                   : 'var(--color-warning)'
                            }}>
                              {op.estado}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)' }}>
                No hay operaciones históricas recientes.
              </div>
            )}
          </div>
        </div>

      </div>
    </div>
  );
}
