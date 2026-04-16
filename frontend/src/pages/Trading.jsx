import { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../api/client';
import { useToast, useConfirm } from '../components/UIProvider';

const ACTIVOS_QUICK = ['GGAL', 'YPFD', 'PAMP', 'ALUA', 'COME', 'BYMA', 'TRAN', 'SUPV', 'BHIP'];

export default function Trading() {
  const toast = useToast();
  const confirm = useConfirm();
  const [ticker, setTicker] = useState('GGAL');
  const [mercado, setMercado] = useState('bCBA');
  const [quote, setQuote] = useState(null);
  const [loadingQuote, setLoadingQuote] = useState(false);
  const [quoteError, setQuoteError] = useState(null);

  // Order form
  const [orderSide, setOrderSide] = useState('compra');
  const [orderQty, setOrderQty] = useState(1);
  const [orderPrice, setOrderPrice] = useState('');
  const [orderPlazo, setOrderPlazo] = useState('t0');
  const [placing, setPlacing] = useState(false);
  const [orderResult, setOrderResult] = useState(null);
  const [orderError, setOrderError] = useState(null);

  // PuedeOperar
  const [puedeOperar, setPuedeOperar] = useState(null);  // null=cargando, true/false
  const [checkingOperar, setCheckingOperar] = useState(false);

  // Operations
  const [operations, setOperations] = useState([]);
  const [loadingOps, setLoadingOps] = useState(false);

  // Auto-refresh ref
  const refreshRef = useRef(null);

  const fetchQuote = useCallback(async (sym) => {
    const s = sym || ticker;
    if (!s) return;
    setLoadingQuote(true);
    setQuoteError(null);
    try {
      const res = await api.getQuote(mercado, s.toUpperCase());
      setQuote(res.data);
      // Auto-fill price with last known
      if (res.data?.ultimoPrecio) {
        setOrderPrice(res.data.ultimoPrecio);
      }
    } catch (err) {
      setQuoteError(err.message);
      setQuote(null);
    } finally {
      setLoadingQuote(false);
    }
  }, [mercado, ticker]);

  const fetchOperations = useCallback(async () => {
    setLoadingOps(true);
    try {
      const res = await api.getTradingOperations();
      const ops = Array.isArray(res.data) ? res.data.slice(0, 20) : [];
      if (ops.length > 0) console.log('[Operaciones] Ejemplo:', ops[0]);
      setOperations(ops);
    } catch {
      // silent
    } finally {
      setLoadingOps(false);
    }
  }, []);

  const checkPuedeOperar = useCallback(async () => {
    setCheckingOperar(true);
    try {
      const res = await api.puedeOperar();
      console.log('[PuedeOperar] Respuesta:', res);
      setPuedeOperar(res.operatoriaHabilitada ?? false);
    } catch (err) {
      console.error('[PuedeOperar] Error:', err);
      setPuedeOperar(null);
    } finally {
      setCheckingOperar(false);
    }
  }, []);

  // Initial load
  useEffect(() => {
    fetchQuote();
    fetchOperations();
    checkPuedeOperar();
  }, []);

  // Auto-refresh quote every 10s
  useEffect(() => {
    if (refreshRef.current) clearInterval(refreshRef.current);
    refreshRef.current = setInterval(() => {
      if (ticker) fetchQuote();
    }, 10000);
    return () => clearInterval(refreshRef.current);
  }, [ticker, fetchQuote]);

  async function handlePlaceOrder() {
    if (!ticker || !orderPrice || !orderQty) return;
    const ok = await confirm({
      title: `Confirmar ${orderSide.toUpperCase()}`,
      message: `¿Confirmar ${orderSide.toUpperCase()} de ${orderQty}x ${ticker} a $${orderPrice}?`,
      type: orderSide === 'venta' ? 'danger' : 'default',
      confirmText: orderSide === 'compra' ? 'Comprar' : 'Vender',
    });
    if (!ok) return;

    setPlacing(true);
    setOrderError(null);
    setOrderResult(null);
    try {
      const res = await api.placeOrder({
        mercado,
        simbolo: ticker.toUpperCase(),
        operacion: orderSide,
        cantidad: orderQty,
        precio: parseFloat(orderPrice),
        plazo: orderPlazo,
      });
      setOrderResult(res.data);
      toast.success(`Orden enviada: ${orderSide.toUpperCase()} ${orderQty}x ${ticker} a $${orderPrice}`);
      fetchOperations();
    } catch (err) {
      setOrderError(err.message);
      toast.error(`Error al enviar orden: ${err.message}`, 8000);
    } finally {
      setPlacing(false);
    }
  }

  async function handleCancelOrder(orderId) {
    const ok = await confirm({
      title: 'Cancelar orden',
      message: `¿Cancelar orden #${orderId}?`,
      type: 'danger',
      confirmText: 'Cancelar orden',
    });
    if (!ok) return;
    try {
      await api.cancelOrder(orderId);
      fetchOperations();
    } catch (err) {
      toast.error(err.message);
    }
  }

  const q = quote;
  const variacion = q?.variacion || 0;
  const varColor = variacion > 0 ? 'var(--color-profit)' : variacion < 0 ? 'var(--color-loss)' : 'var(--text-secondary)';

  // Parse puntas
  let puntas = [];
  if (q?.puntas) {
    if (Array.isArray(q.puntas)) puntas = q.puntas;
    else puntas = [q.puntas];
  }

  return (
    <div id="trading-page">
      {/* ── Buscador ─────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1.5rem', flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 700 }}>📈 Trading Manual</h1>
        {/* Badge PuedeOperar */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          {checkingOperar ? (
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>⏳ Verificando operatoria...</span>
          ) : puedeOperar === true ? (
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: '0.35rem',
              padding: '0.25rem 0.65rem', borderRadius: '99px', fontSize: '0.75rem', fontWeight: 600,
              background: 'rgba(0,217,126,0.12)', color: 'var(--color-profit)',
              border: '1px solid rgba(0,217,126,0.3)'
            }}>✅ Operatoria Habilitada</span>
          ) : puedeOperar === false ? (
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: '0.35rem',
              padding: '0.25rem 0.65rem', borderRadius: '99px', fontSize: '0.75rem', fontWeight: 600,
              background: 'rgba(255,59,92,0.12)', color: 'var(--color-loss)',
              border: '1px solid rgba(255,59,92,0.3)'
            }}>🚫 Operatoria Bloqueada</span>
          ) : (
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: '0.35rem',
              padding: '0.25rem 0.65rem', borderRadius: '99px', fontSize: '0.75rem', fontWeight: 600,
              background: 'rgba(255,193,7,0.12)', color: 'var(--color-warning)',
              border: '1px solid rgba(255,193,7,0.3)'
            }}>⚠️ Estado desconocido</span>
          )}
          <button
            className="btn btn--ghost btn--sm"
            onClick={checkPuedeOperar}
            disabled={checkingOperar}
            title="Verificar estado de operatoria IOL"
          >
            🔄
          </button>
        </div>
      </div>

      {/* Quick Tickers */}
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
        {ACTIVOS_QUICK.map(t => (
          <button
            key={t}
            className={`btn btn--sm ${ticker === t ? 'btn--primary' : 'btn--ghost'}`}
            onClick={() => { setTicker(t); fetchQuote(t); }}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Search bar */}
      <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1.5rem', flexWrap: 'wrap' }}>
        <select
          className="form-select"
          value={mercado}
          onChange={(e) => setMercado(e.target.value)}
          style={{ width: 'auto', minWidth: '120px' }}
        >
          <option value="bCBA">BCBA (Arg)</option>
          <option value="nYSE">NYSE</option>
          <option value="nASDAQ">NASDAQ</option>
        </select>
        <input
          className="form-input"
          style={{ maxWidth: '200px' }}
          placeholder="Ticker (ej: GGAL)"
          value={ticker}
          onChange={(e) => setTicker(e.target.value.toUpperCase())}
          onKeyDown={(e) => e.key === 'Enter' && fetchQuote()}
        />
        <button className="btn btn--primary" onClick={() => fetchQuote()} disabled={loadingQuote}>
          {loadingQuote ? <span className="spinner spinner--sm" style={{ marginRight: '0.25rem' }}></span> : '🔍'} Buscar
        </button>
      </div>

      {quoteError && (
        <div style={{
          padding: '0.75rem 1rem', marginBottom: '1rem',
          background: 'var(--color-loss-dim)', border: '1px solid rgba(255,59,92,0.3)',
          borderRadius: 'var(--radius-md)', color: 'var(--color-loss)', fontSize: '0.85rem'
        }}>
          {quoteError}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.25rem' }}>
        {/* ── COTIZACIÓN ─────────────────────────────────────────── */}
        <div className="card" style={{ gridColumn: q ? 'auto' : '1 / -1' }}>
          <div className="card__header">
            <span className="card__title">Cotización {ticker}</span>
            {q && (
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                Auto-refresh 10s
              </span>
            )}
          </div>
          <div className="card__body">
            {!q ? (
              <div className="empty-state" style={{ padding: '2rem' }}>
                <div className="empty-state__title">Buscá un ticker para ver la cotización</div>
              </div>
            ) : (
              <>
                {/* Precio principal */}
                <div style={{ display: 'flex', alignItems: 'baseline', gap: '1rem', marginBottom: '1rem' }}>
                  <span style={{
                    fontSize: '2.2rem', fontWeight: 800, fontFamily: 'var(--font-mono)',
                    color: 'var(--text-primary)'
                  }}>
                    ${q.ultimoPrecio?.toLocaleString('es-AR', { minimumFractionDigits: 2 })}
                  </span>
                  <span style={{
                    fontSize: '1rem', fontWeight: 600, fontFamily: 'var(--font-mono)',
                    color: varColor
                  }}>
                    {variacion >= 0 ? '+' : ''}{variacion.toFixed(2)}%
                  </span>
                </div>

                {/* Datos grid */}
                <div style={{
                  display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))',
                  gap: '0.75rem', marginBottom: '1rem'
                }}>
                  {[
                    ['Apertura', q.apertura],
                    ['Máximo', q.maximo],
                    ['Mínimo', q.minimo],
                    ['Cierre Ant.', q.cierreAnterior],
                    ['Volumen', q.volumen?.toLocaleString()],
                    ['Monto Op.', q.montoOperado ? `$${Math.round(q.montoOperado / 1000000)}M` : '-'],
                  ].map(([label, val]) => (
                    <div key={label} style={{
                      background: 'var(--bg-primary)', borderRadius: 'var(--radius-sm)',
                      padding: '0.5rem 0.75rem'
                    }}>
                      <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                        {label}
                      </div>
                      <div style={{ fontSize: '0.9rem', fontWeight: 600, fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>
                        {val ?? '-'}
                      </div>
                    </div>
                  ))}
                </div>

                {/* Puntas */}
                {puntas.length > 0 && (
                  <div style={{ marginTop: '0.75rem' }}>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.5rem', fontWeight: 600 }}>
                      Libro de Órdenes (Top Puntas)
                    </div>
                    <div style={{
                      display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem'
                    }}>
                      {/* BID */}
                      <div style={{ background: 'rgba(0, 217, 126, 0.06)', borderRadius: 'var(--radius-sm)', padding: '0.5rem 0.75rem', border: '1px solid rgba(0, 217, 126, 0.15)' }}>
                        <div style={{ fontSize: '0.65rem', color: 'var(--color-profit)', marginBottom: '0.25rem', fontWeight: 600 }}>
                          COMPRA (BID)
                        </div>
                        {puntas.slice(0, 5).map((p, i) => (
                          <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', fontFamily: 'var(--font-mono)', padding: '0.1rem 0' }}>
                            <span style={{ color: 'var(--text-muted)' }}>{p.cantidadCompra || '-'}</span>
                            <span style={{ color: 'var(--color-profit)', fontWeight: 600, cursor: 'pointer' }}
                              onClick={() => setOrderPrice(p.precioCompra)}
                            >
                              ${p.precioCompra?.toLocaleString('es-AR', { minimumFractionDigits: 2 }) || '-'}
                            </span>
                          </div>
                        ))}
                      </div>
                      {/* ASK */}
                      <div style={{ background: 'rgba(255, 59, 92, 0.06)', borderRadius: 'var(--radius-sm)', padding: '0.5rem 0.75rem', border: '1px solid rgba(255, 59, 92, 0.15)' }}>
                        <div style={{ fontSize: '0.65rem', color: 'var(--color-loss)', marginBottom: '0.25rem', fontWeight: 600 }}>
                          VENTA (ASK)
                        </div>
                        {puntas.slice(0, 5).map((p, i) => (
                          <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', fontFamily: 'var(--font-mono)', padding: '0.1rem 0' }}>
                            <span style={{ color: 'var(--color-loss)', fontWeight: 600, cursor: 'pointer' }}
                              onClick={() => setOrderPrice(p.precioVenta)}
                            >
                              ${p.precioVenta?.toLocaleString('es-AR', { minimumFractionDigits: 2 }) || '-'}
                            </span>
                            <span style={{ color: 'var(--text-muted)' }}>{p.cantidadVenta || '-'}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        {/* ── BOLETA DE ORDEN ────────────────────────────────────── */}
        {q && (
          <div className="card">
            <div className="card__header">
              <span className="card__title">📝 Boleta de Orden</span>
            </div>
            <div className="card__body">
              {/* Compra / Venta toggle */}
              <div style={{
                display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem',
                marginBottom: '1.25rem'
              }}>
                <button
                  className={`btn ${orderSide === 'compra' ? 'btn--success' : 'btn--ghost'}`}
                  onClick={() => setOrderSide('compra')}
                  style={{ fontSize: '0.95rem', padding: '0.75rem' }}
                >
                  📈 COMPRAR
                </button>
                <button
                  className={`btn ${orderSide === 'venta' ? 'btn--danger' : 'btn--ghost'}`}
                  onClick={() => setOrderSide('venta')}
                  style={{
                    fontSize: '0.95rem', padding: '0.75rem',
                    ...(orderSide === 'venta' ? { background: 'var(--color-loss)', color: 'white', borderColor: 'var(--color-loss)' } : {})
                  }}
                >
                  📉 VENDER
                </button>
              </div>

              {/* Símbolo */}
              <div style={{
                background: 'var(--bg-primary)', borderRadius: 'var(--radius-md)',
                padding: '0.75rem 1rem', marginBottom: '1rem',
                display: 'flex', justifyContent: 'space-between', alignItems: 'center'
              }}>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Instrumento</span>
                <span style={{ fontSize: '1.1rem', fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--accent-purple-light)' }}>
                  {ticker}
                </span>
              </div>

              {/* Cantidad */}
              <div className="form-group">
                <label className="form-label">Cantidad</label>
                <input
                  className="form-input"
                  type="number"
                  min="1"
                  value={orderQty}
                  onChange={(e) => setOrderQty(parseInt(e.target.value) || 1)}
                />
              </div>

              {/* Precio */}
              <div className="form-group">
                <label className="form-label">Precio Límite ($)</label>
                <input
                  className="form-input"
                  type="number"
                  step="0.01"
                  value={orderPrice}
                  onChange={(e) => setOrderPrice(e.target.value)}
                />
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
                  Tip: Click en un precio del libro para completar automáticamente.
                </div>
              </div>

              {/* Plazo */}
              <div className="form-group">
                <label className="form-label">Plazo</label>
                <select
                  className="form-select"
                  value={orderPlazo}
                  onChange={(e) => setOrderPlazo(e.target.value)}
                >
                  <option value="t0">T+0 (Contado Inmediato)</option>
                  <option value="t1">T+1 (24hs)</option>
                  <option value="t2">T+2 (48hs)</option>
                </select>
              </div>

              {/* Total estimado */}
              {orderPrice && orderQty && (
                <div style={{
                  background: orderSide === 'compra' ? 'rgba(0,217,126,0.08)' : 'rgba(255,59,92,0.08)',
                  border: `1px solid ${orderSide === 'compra' ? 'rgba(0,217,126,0.2)' : 'rgba(255,59,92,0.2)'}`,
                  borderRadius: 'var(--radius-md)', padding: '0.75rem 1rem', marginBottom: '1rem',
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center'
                }}>
                  <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Total Estimado</span>
                  <span style={{
                    fontSize: '1.2rem', fontWeight: 700, fontFamily: 'var(--font-mono)',
                    color: orderSide === 'compra' ? 'var(--color-profit)' : 'var(--color-loss)'
                  }}>
                    ${(parseFloat(orderPrice) * orderQty).toLocaleString('es-AR', { minimumFractionDigits: 2 })}
                  </span>
                </div>
              )}

              {orderError && (
                <div style={{
                  padding: '0.5rem 0.75rem', marginBottom: '0.75rem',
                  background: 'var(--color-loss-dim)', borderRadius: 'var(--radius-sm)',
                  color: 'var(--color-loss)', fontSize: '0.8rem'
                }}>
                  {orderError}
                </div>
              )}

              {orderResult && (
                <div style={{
                  padding: '0.5rem 0.75rem', marginBottom: '0.75rem',
                  background: 'var(--color-profit-dim)', borderRadius: 'var(--radius-sm)',
                  color: 'var(--color-profit)', fontSize: '0.8rem'
                }}>
                  ✅ Orden enviada exitosamente (#{orderResult.numeroOperacion || 'OK'})
                </div>
              )}

              <button
                className={`btn btn--full ${orderSide === 'compra' ? 'btn--success' : 'btn--danger'}`}
                onClick={handlePlaceOrder}
                disabled={placing || !orderPrice || !orderQty}
                style={{
                  padding: '0.85rem', fontSize: '1rem', fontWeight: 700,
                  ...(orderSide === 'venta' ? { background: 'var(--color-loss)', color: 'white' } : {})
                }}
              >
                {placing ? '⏳ Enviando...'
                  : `${orderSide === 'compra' ? '📈 Comprar' : '📉 Vender'} ${orderQty}x ${ticker} a $${orderPrice || '...'}`}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ── OPERACIONES RECIENTES ────────────────────────────────── */}
      <div className="card" style={{ marginTop: '1.25rem' }}>
        <div className="card__header">
          <span className="card__title">📋 Operaciones Recientes</span>
          <button className="btn btn--ghost btn--sm" onClick={fetchOperations} disabled={loadingOps}>
            {loadingOps ? <span className="spinner spinner--sm" style={{ marginRight: '0.25rem' }}></span> : '🔄 '}Refrescar
          </button>
        </div>
        <div className="card__body" style={{ padding: 0 }}>
          {operations.length === 0 ? (
            <div className="empty-state" style={{ padding: '2rem' }}>
              <div className="empty-state__title">Sin operaciones recientes</div>
            </div>
          ) : (
            <div className="table-container">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Ticker</th>
                    <th>Tipo</th>
                    <th>Cantidad</th>
                    <th>Precio</th>
                    <th>Estado</th>
                    <th>Fecha</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {operations.map((op, i) => {
                    const opType = op.tipo?.toLowerCase() || '';
                    const isCompra = opType.includes('compra');
                    return (
                      <tr key={op.numero || i}>
                        <td>{op.numero || '-'}</td>
                        <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{op.simbolo || '-'}</td>
                        <td>
                          <span style={{
                            padding: '0.15rem 0.4rem', borderRadius: '4px', fontSize: '0.7rem', fontWeight: 600,
                            background: isCompra ? 'var(--color-profit-dim)' : 'var(--color-loss-dim)',
                            color: isCompra ? 'var(--color-profit)' : 'var(--color-loss)',
                          }}>
                            {op.tipo || '-'}
                          </span>
                        </td>
                        <td>{op.cantidadOperada ?? op.cantidad ?? '-'}</td>
                        <td>${op.precioOperado ?? op.precio ?? '-'}</td>
                        <td style={{
                          color: op.estado === 'terminada' ? 'var(--color-profit)'
                               : op.estado === 'cancelada' ? 'var(--text-muted)'
                               : 'var(--color-warning)'
                        }}>
                          {op.estado || '-'}
                        </td>
                        <td style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                          {op.fechaOperada ? new Date(op.fechaOperada).toLocaleString('es-AR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-'}
                        </td>
                        <td>
                          {(() => {
                            const estado = (op.estado || '').toLowerCase();
                            const esFinal = ['terminada', 'cancelada', 'rechazada', 'anulada'].includes(estado);
                            return !esFinal && (
                              <button
                                className="btn btn--danger btn--sm"
                                onClick={() => handleCancelOrder(op.numero)}
                                style={{ padding: '0.2rem 0.4rem', fontSize: '0.7rem' }}
                              >
                                ✕
                              </button>
                            );
                          })()}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
