import { useState, useEffect } from 'react';
import { api } from '../api/client';

const ACTIVOS_POPULARES = ['COME', 'GGAL', 'BHIP', 'PAMP', 'YPFD', 'ALUA', 'BYMA', 'TRAN', 'SUPV'];

export default function StrategyForm({ onClose, onCreated, strategyTypes }) {
  const [form, setForm] = useState({
    nombre: '',
    tipo_estrategia: 'options_mispricing',
    activo: 'COME',
    mercado: 'bCBA',
    fondos_asignados: 0,
    dry_run: true,
    config: {},
  });
  const [loading, setLoading] = useState(false);
  const [loadingAi, setLoadingAi] = useState(false);
  const [aiAnalysis, setAiAnalysis] = useState(null);
  const [error, setError] = useState(null);

  // Cargar defaults del tipo seleccionado
  useEffect(() => {
    const tipo = strategyTypes?.[form.tipo_estrategia];
    if (tipo) {
      const defaults = {};
      tipo.params.forEach(p => { defaults[p.key] = p.default; });
      setForm(prev => ({ ...prev, config: { ...defaults, ...prev.config } }));
    }
  }, [form.tipo_estrategia, strategyTypes]);

  function handleChange(key, value) {
    setForm(prev => ({ ...prev, [key]: value }));
  }

  function handleConfigChange(key, value, type) {
    const parsed = type === 'bool'  ? Boolean(value)
                 : type === 'float' ? parseFloat(value) || 0
                 : type === 'int'   ? parseInt(value) || 0
                 : value;
    setForm(prev => ({
      ...prev,
      config: { ...prev.config, [key]: parsed },
    }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!form.nombre.trim()) {
      setError('El nombre es obligatorio.');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await api.createStrategy(form);
      onCreated?.();
      onClose?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleAskClaude() {
    if (!form.activo) return;
    setLoadingAi(true);
    setError(null);
    setAiAnalysis(null);
    try {
      const response = await api.configureAI({
        activo: form.activo,
        mercado: form.mercado,
        tipo_estrategia: form.tipo_estrategia,
        fondos_asignados: form.fondos_asignados || 0
      });
      // response comes as { analysis: "...", config: {...} }
      if (response && response.config) {
        setForm(prev => ({
          ...prev,
          config: { ...prev.config, ...response.config }
        }));
        setAiAnalysis(response.analysis);
      }
    } catch (err) {
      setError("Error Claude IA: " + err.message);
    } finally {
      setLoadingAi(false);
    }
  }

  const generateGeminiPrompt = () => {
    if (!tipoInfo) return '';
    const paramsList = tipoInfo.params.map((p, i) => `${i + 1}. "${p.key}" (${p.type}): ${p.descripcion || p.label}`).join('\n');
    return `Actúa como un analista cuantitativo Quantamental especialista en la Bolsa Argentina (Merval / ByMA).

TAREA PRINCIPAL:
Necesito que utilices tus capacidades de Búsqueda Profunda (Deep Research) en la web para analizar en tiempo real la situación macroeconómica, técnica y fundamental del activo bursátil: ${form.activo || '[TICKER]'}.
Busca noticias de hoy en portales como El Cronista, Ámbito e Infobae. Analiza las tasas de caución, la volatilidad implícita y la tendencia direccional.

OBJETIVO:
En base a tu investigación, configura los parámetros matemáticos de riesgo para mi bot algorítmico, operando la estrategia "${tipoInfo.nombre}". 
Los fondos asignados para esta operación son: ${form.fondos_asignados || 0} ARS.

LISTA DE PARÁMETROS A CONFIGURAR:
${paramsList}

FORMATO ESTRICTO DE SALIDA:
Devuélveme tu respuesta ÚNICA Y EXCLUSIVAMENTE en formato JSON validado. Debe contener estas dos llaves:
- "analysis": Un párrafo extenso citando OBLIGATORIAMENTE LAS FUENTES de periódicos financieros que usaste de base, tu diagnóstico del precio y tu elección de parámetros.
- "config": Un objeto con la lista exacta de las variables provistas arriba con su valor recomendado.

Asegúrate de que el JSON sea perfectamente válido sin texto markdown adicional.`;
  };

  const tipoInfo = strategyTypes?.[form.tipo_estrategia];

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose?.()}>
      <div className="modal" id="strategy-form-modal">
        <div className="modal__header">
          <h2 className="modal__title">Nueva Estrategia</h2>
          <button className="modal__close" onClick={onClose}>&times;</button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal__body">
            {error && (
              <div style={{
                padding: '0.75rem 1rem', marginBottom: '1rem',
                background: 'var(--color-loss-dim)', border: '1px solid rgba(255,59,92,0.3)',
                borderRadius: 'var(--radius-md)', color: 'var(--color-loss)', fontSize: '0.85rem'
              }}>
                {error}
              </div>
            )}

            <div className="form-group">
              <label className="form-label" htmlFor="strat-name">Nombre</label>
              <input
                className="form-input"
                id="strat-name"
                placeholder="Ej: GGAL Agresiva"
                value={form.nombre}
                onChange={(e) => handleChange('nombre', e.target.value)}
                autoFocus
              />
            </div>

            <div className="form-row">
              <div className="form-group">
                <label className="form-label" htmlFor="strat-type">Tipo de Estrategia</label>
                <select
                  className="form-select"
                  id="strat-type"
                  value={form.tipo_estrategia}
                  onChange={(e) => handleChange('tipo_estrategia', e.target.value)}
                >
                  {strategyTypes && Object.entries(strategyTypes).map(([key, val]) => (
                    <option key={key} value={key}>{val.nombre}</option>
                  ))}
                </select>
              </div>

              <div className="form-group">
                <label className="form-label" htmlFor="strat-asset">Activo</label>
                <select
                  className="form-select"
                  id="strat-asset"
                  value={ACTIVOS_POPULARES.includes(form.activo) ? form.activo : '__custom__'}
                  onChange={(e) => {
                    if (e.target.value === '__custom__') {
                      handleChange('activo', '');   // vaciar → aparece el input
                    } else {
                      handleChange('activo', e.target.value);
                    }
                  }}
                >
                  {ACTIVOS_POPULARES.map(a => (
                    <option key={a} value={a}>{a}</option>
                  ))}
                  <option value="__custom__">Otro (escribir abajo)...</option>
                </select>
                {!ACTIVOS_POPULARES.includes(form.activo) && (
                  <input
                    className="form-input"
                    style={{ marginTop: '0.5rem' }}
                    id="strat-asset-custom"
                    placeholder="Ticker personalizado (ej: LOMA, EDN...)"
                    value={form.activo}
                    onChange={(e) => handleChange('activo', e.target.value.toUpperCase())}
                    autoFocus
                  />
                )}
              </div>
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="strat-funds">Fondos Asignados (ARS)</label>
              <input
                className="form-input"
                id="strat-funds"
                type="number"
                min="0"
                step="1000"
                placeholder="0 = sin límite"
                value={form.fondos_asignados || ''}
                onChange={(e) => handleChange('fondos_asignados', parseFloat(e.target.value) || 0)}
              />
              <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.25rem', display: 'block' }}>
                Dejá en 0 para operar sin límite de fondos.
              </span>
            </div>

            {/* Modo toggle */}
            <div className="form-group">
              <label className="form-label">Modo de Operación</label>
              <div className="toggle-container">
                <span className={`toggle-label ${!form.dry_run ? 'toggle-label--active' : ''}`}>
                  {form.dry_run ? '🧪 DRY-RUN (simulación)' : '🔴 LIVE (órdenes reales)'}
                </span>
                <input
                  type="checkbox"
                  className="toggle"
                  id="strat-live-toggle"
                  checked={!form.dry_run}
                  onChange={(e) => {
                    if (e.target.checked && !confirm('⚠️ ATENCIÓN: El modo LIVE enviará órdenes reales al mercado. ¿Estás seguro?')) {
                      return;
                    }
                    handleChange('dry_run', !e.target.checked);
                  }}
                />
              </div>
            </div>

            {/* Claude AI Analysis Panel */}
            <div className="form-group" style={{ 
                marginTop: '1rem', 
                padding: '1rem', 
                background: 'linear-gradient(145deg, rgba(30,30,40,0.4) 0%, rgba(20,20,30,1) 100%)', 
                border: '1px solid rgba(130, 80, 250, 0.4)', 
                borderRadius: '8px' 
              }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                <span style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  🧠 Claude AI Autopilot
                </span>
                <button 
                  type="button" 
                  onClick={handleAskClaude} 
                  disabled={loadingAi || !form.activo}
                  style={{
                    background: 'rgba(130, 80, 250, 0.2)',
                    border: '1px solid rgba(130, 80, 250, 0.5)',
                    color: '#d4b3ff',
                    padding: '4px 12px',
                    borderRadius: '4px',
                    fontSize: '0.75rem',
                    cursor: loadingAi ? 'not-allowed' : 'pointer',
                    transition: 'all 0.2s',
                  }}
                  onMouseOver={(e) => {
                    if (!loadingAi) e.currentTarget.style.background = 'rgba(130, 80, 250, 0.4)';
                  }}
                  onMouseOut={(e) => {
                    if (!loadingAi) e.currentTarget.style.background = 'rgba(130, 80, 250, 0.2)';
                  }}
                >
                  {loadingAi ? '⏳ Analizando Merval...' : '⚡ Pre-configurar usando IA'}
                </button>
              </div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                {aiAnalysis ? (
                  <div style={{ color: '#e2d3f7', fontStyle: 'italic', lineHeight: '1.4' }}>
                    "{aiAnalysis}"
                  </div>
                ) : (
                  "Consulta a la IA para analizar el activo, proyectar macroeconomía local y autocompletar la configuración inferior bajo un riesgo calibrado."
                )}
              </div>
            </div>

            {/* Gemini Prompt Generator */}
            {tipoInfo && (
                <div className="form-group" style={{ 
                  marginTop: '0.5rem', 
                  padding: '1rem', 
                  background: 'rgba(25, 25, 35, 0.6)', 
                  border: '1px dashed rgba(100, 150, 255, 0.4)', 
                  borderRadius: '8px' 
                }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                  <span style={{ fontSize: '0.80rem', fontWeight: 600, color: '#88bcff' }}>
                    ✨ Gemini Deep Research (Manual)
                  </span>
                  <button 
                    type="button" 
                    onClick={() => {
                      navigator.clipboard.writeText(generateGeminiPrompt());
                      alert('Prompt copiado al portapapeles. ¡Pégalo en Gemini y usá el modo "Pensar Profundamente"!');
                    }}
                    style={{
                      background: 'rgba(100, 150, 255, 0.2)', border: '1px solid rgba(100, 150, 255, 0.5)',
                      color: '#aaccff', padding: '4px 10px', borderRadius: '4px', fontSize: '0.7rem', cursor: 'pointer'
                    }}
                  >
                    📋 Copiar Prompt
                  </button>
                </div>
                <div style={{ fontSize: '0.70rem', color: 'var(--text-muted)' }}>
                  ¿Optarías por usar Deep Research en Google Gemini en vez del Autopilot interno? Seleccioná tu estrategia y tus fondos arriba, pulsá copiar este prompt auto-generado, y volcá el JSON de Gemini abajo.
                </div>
              </div>
            )}

            {/* Config params */}
            {tipoInfo && (
              <>
                <div style={{
                  marginTop: '1rem', marginBottom: '0.75rem',
                  fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-secondary)',
                  textTransform: 'uppercase', letterSpacing: '0.06em'
                }}>
                  Parámetros — {tipoInfo.nombre}
                </div>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '1rem' }}>
                  {tipoInfo.descripcion}
                </div>

                <div className="form-row" style={{ gridTemplateColumns: 'repeat(2, 1fr)' }}>
                  {tipoInfo.params.map(p => (
                    <div className="form-group" key={p.key}>
                      <label className="form-label" htmlFor={`cfg-${p.key}`} style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                        {p.label}
                      </label>
                      {p.descripcion && (
                        <div style={{
                          fontSize: '0.65rem',
                          color: 'rgba(130, 80, 250, 0.8)',
                          marginBottom: '0.4rem',
                          lineHeight: '1.2'
                        }}>
                          {p.descripcion}
                        </div>
                      )}
                      {p.type === 'bool' ? (
                        <div className="toggle-container" style={{ marginTop: '0.35rem' }}>
                          <input
                            type="checkbox"
                            className="toggle"
                            id={`cfg-${p.key}`}
                            checked={!!(form.config[p.key] ?? p.default)}
                            onChange={(e) => handleConfigChange(p.key, e.target.checked, 'bool')}
                          />
                          <span style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', marginLeft: '0.5rem' }}>
                            {form.config[p.key] ?? p.default ? 'Activado' : 'Desactivado'}
                          </span>
                        </div>
                      ) : p.type === 'string' ? (
                        <input
                          className="form-input"
                          id={`cfg-${p.key}`}
                          type="text"
                          value={form.config[p.key] ?? p.default}
                          onChange={(e) => handleConfigChange(p.key, e.target.value.toUpperCase(), p.type)}
                        />
                      ) : (
                        <input
                          className="form-input"
                          id={`cfg-${p.key}`}
                          type="number"
                          step={p.type === 'float' ? '0.01' : '1'}
                          value={form.config[p.key] ?? p.default}
                          onChange={(e) => handleConfigChange(p.key, e.target.value, p.type)}
                        />
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>

          <div className="modal__footer">
            <button type="button" className="btn btn--ghost" onClick={onClose}>
              Cancelar
            </button>
            <button type="submit" className="btn btn--primary" disabled={loading} id="btn-create-strategy">
              {loading ? <><span className="spinner spinner--sm" style={{ marginRight: '0.25rem' }}></span> Creando...</> : '✨ Crear Estrategia'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
