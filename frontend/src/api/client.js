const isLocalhost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
const API_BASE = isLocalhost ? 'http://localhost:8000/api' : '/api';
const WS_BASE = isLocalhost 
  ? 'ws://localhost:8000/ws/live' 
  : `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/live`;

// ─── REST Client ────────────────────────────────────────────────────────────

async function request(path, options = {}, timeoutMs = 15000) {
  const url = `${API_BASE}${path}`;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      headers: { 'Content-Type': 'application/json', ...options.headers },
      signal: controller.signal,
      ...options,
    });
    clearTimeout(timeoutId);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  } catch (err) {
    clearTimeout(timeoutId);
    if (err.name === 'AbortError') {
      const secs = Math.round(timeoutMs / 1000);
      throw new Error(`Timeout: la solicitud tardó más de ${secs} segundos`);
    }
    throw err;
  }
}

export const api = {
  getStatus: () => request('/status'),
  getAccount: () => request('/account'),
  getAvailableFunds: () => request('/available-funds'),
  getPortfolio: () => request('/portfolio'),
  getOperations: () => request('/operations'),
  getStrategyTypes: () => request('/strategy-types'),
  getStrategies: () => request('/strategies'),
  getStrategy: (id) => request(`/strategies/${id}`),
  createStrategy: (data) => request('/strategies', { method: 'POST', body: JSON.stringify(data) }),
  updateStrategy: (id, data) => request(`/strategies/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteStrategy: (id) => request(`/strategies/${id}`, { method: 'DELETE' }),
  startStrategy: (id) => request(`/strategies/${id}/start`, { method: 'POST' }),
  pauseStrategy: (id) => request(`/strategies/${id}/pause`, { method: 'POST' }),
  stopStrategy: (id) => request(`/strategies/${id}/stop`, { method: 'POST' }),
  getStrategyLogs: (id, limit = 50) => request(`/strategies/${id}/logs?limit=${limit}`),
  configureAI: (data) => request('/ai/configure', { method: 'POST', body: JSON.stringify(data) }),
  // Trading manual — órdenes usan timeout extendido (45s) por latencia IOL
  puedeOperar: () => request('/trading/puede-operar'),
  getQuote: (mercado, simbolo) => request('/trading/quote', { method: 'POST', body: JSON.stringify({ mercado, simbolo }) }),
  placeOrder: (data) => request('/trading/order', { method: 'POST', body: JSON.stringify(data) }, 45000),
  cancelOrder: (orderId) => request(`/trading/order/${orderId}`, { method: 'DELETE' }, 45000),
  getTradingOperations: () => request('/trading/operations'),
  getPanel: (instrumento, panel) => request(`/trading/panel/${instrumento}/${panel}`),
  // Reconnect
  reconnect: () => request('/reconnect', { method: 'POST' }),
};

// ─── WebSocket Hook ─────────────────────────────────────────────────────────

export function createWebSocket(onMessage) {
  let ws = null;
  let reconnectTimer = null;
  let isClosing = false;

  function connect() {
    if (isClosing) return;
    ws = new WebSocket(WS_BASE);

    ws.onopen = () => {
      console.log('[WS] Connected');
      if (onMessage) onMessage({ type: 'ws_connected' });
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'heartbeat') return;
        if (onMessage) onMessage(data);
      } catch (e) {
        console.warn('[WS] Parse error:', e);
      }
    };

    ws.onclose = () => {
      console.log('[WS] Disconnected');
      if (onMessage) onMessage({ type: 'ws_disconnected' });
      if (!isClosing) {
        reconnectTimer = setTimeout(connect, 3000);
      }
    };

    ws.onerror = (err) => {
      console.error('[WS] Error:', err);
      ws.close();
    };
  }

  function close() {
    isClosing = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (ws) ws.close();
  }

  connect();
  return { close };
}
