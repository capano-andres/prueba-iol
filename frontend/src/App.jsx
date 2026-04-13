import { useState, useEffect, useCallback, useRef } from 'react';
import './index.css';
import { api, createWebSocket } from './api/client';
import Header from './components/Header';
import AccountPanel from './components/AccountPanel';
import Dashboard from './pages/Dashboard';
import StrategyDetail from './pages/StrategyDetail';
import Portfolio from './pages/Portfolio';
import Trading from './pages/Trading';

export default function App() {
  const [page, setPage] = useState('dashboard');           // 'dashboard' | 'detail' | 'portfolio' | 'trading'
  const [selectedId, setSelectedId] = useState(null);
  const [strategies, setStrategies] = useState([]);
  const [strategyTypes, setStrategyTypes] = useState({});
  const [status, setStatus] = useState(null);
  const [connected, setConnected] = useState(false);
  const [time, setTime] = useState(Date.now());
  const wsRef = useRef(null);

  // ── Fetch data ────────────────────────────────────────────────────────

  const fetchStrategies = useCallback(async () => {
    try {
      const data = await api.getStrategies();
      setStrategies(data);
    } catch (err) {
      console.error('Error fetching strategies:', err);
    }
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await api.getStatus();
      setStatus(data);
      setConnected(data.connected);
    } catch {
      setConnected(false);
    }
  }, []);

  // ── Init ──────────────────────────────────────────────────────────────

  useEffect(() => {
    fetchStrategies();
    fetchStatus();
    api.getStrategyTypes().then(setStrategyTypes).catch(console.error);

    // Poll status every 10s
    const interval = setInterval(() => {
      fetchStatus();
      fetchStrategies();
    }, 10000);

    return () => clearInterval(interval);
  }, [fetchStrategies, fetchStatus]);

  // ── WebSocket ─────────────────────────────────────────────────────────

  useEffect(() => {
    const ws = createWebSocket((msg) => {
      if (msg.type === 'ws_connected') {
        setConnected(true);
      } else if (msg.type === 'ws_disconnected') {
        setConnected(false);
      } else if (msg.type === 'init') {
        setStrategies(msg.strategies || []);
      } else if (msg.type === 'snapshot') {
        setStrategies(prev =>
          prev.map(s => s.id === msg.slot_id ? msg.data : s)
        );
      } else if (msg.type === 'slot_created' || msg.type === 'slot_updated' ||
                 msg.type === 'slot_started' || msg.type === 'slot_paused' ||
                 msg.type === 'slot_stopped') {
        fetchStrategies();
      } else if (msg.type === 'slot_removed') {
        setStrategies(prev => prev.filter(s => s.id !== msg.slot_id));
        if (selectedId === msg.slot_id) {
          setPage('dashboard');
          setSelectedId(null);
        }
      }
    });
    wsRef.current = ws;
    return () => ws.close();
  }, [fetchStrategies, selectedId]);

  // ── Clock ──────────────────────────────────────────────────────────────

  useEffect(() => {
    const t = setInterval(() => setTime(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  // ── Navigation ────────────────────────────────────────────────────────

  function handleSelectStrategy(id) {
    setSelectedId(id);
    setPage('detail');
  }

  function handleBack() {
    setPage('dashboard');
    setSelectedId(null);
  }

  // ── Render ────────────────────────────────────────────────────────────

  const selectedStrategy = strategies.find(s => s.id === selectedId);

  return (
    <div className="app-layout">
      <Header connected={connected} status={status} time={time} page={page} onNavigate={setPage} />
      {connected && page !== 'portfolio' && <AccountPanel />}

      <main className="app-main">
        {page === 'trading' ? (
          <Trading />
        ) : page === 'portfolio' ? (
          <Portfolio />
        ) : page === 'dashboard' ? (
          <Dashboard
            strategies={strategies}
            strategyTypes={strategyTypes}
            onRefresh={fetchStrategies}
            onSelectStrategy={handleSelectStrategy}
          />
        ) : (
          <StrategyDetail
            strategyId={selectedId}
            strategy={selectedStrategy}
            strategyTypes={strategyTypes}
            onBack={handleBack}
            onRefresh={fetchStrategies}
          />
        )}
      </main>
    </div>
  );
}
