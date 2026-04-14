import { useState, useCallback, createContext, useContext } from 'react';

// ─── Toast Context ──────────────────────────────────────────────────────────

const ToastContext = createContext(null);
const ConfirmContext = createContext(null);

export function useToast() {
  return useContext(ToastContext);
}

export function useConfirm() {
  return useContext(ConfirmContext);
}

// ─── Toast Component ────────────────────────────────────────────────────────

function Toast({ toasts, removeToast }) {
  return (
    <div style={{
      position: 'fixed',
      top: '1rem',
      right: '1rem',
      zIndex: 9999,
      display: 'flex',
      flexDirection: 'column',
      gap: '0.5rem',
      maxWidth: '420px',
      width: '100%',
    }}>
      {toasts.map(t => (
        <div
          key={t.id}
          style={{
            padding: '0.85rem 1.15rem',
            borderRadius: 'var(--radius-md)',
            backdropFilter: 'blur(16px)',
            WebkitBackdropFilter: 'blur(16px)',
            border: '1px solid',
            borderColor: t.type === 'error' ? 'rgba(255,59,92,0.4)'
                       : t.type === 'success' ? 'rgba(0,217,126,0.4)'
                       : t.type === 'warning' ? 'rgba(255,170,0,0.4)'
                       : 'rgba(138,180,248,0.4)',
            background: t.type === 'error' ? 'rgba(255,59,92,0.12)'
                      : t.type === 'success' ? 'rgba(0,217,126,0.12)'
                      : t.type === 'warning' ? 'rgba(255,170,0,0.12)'
                      : 'rgba(138,180,248,0.12)',
            color: 'var(--text-primary)',
            fontSize: '0.85rem',
            display: 'flex',
            alignItems: 'flex-start',
            gap: '0.65rem',
            animation: 'toast-in 0.3s cubic-bezier(0.34, 1.56, 0.64, 1)',
            boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
          }}
        >
          <span style={{ fontSize: '1.1rem', lineHeight: 1, flexShrink: 0, marginTop: '0.05rem' }}>
            {t.type === 'error' ? '❌' : t.type === 'success' ? '✅' : t.type === 'warning' ? '⚠️' : 'ℹ️'}
          </span>
          <span style={{ flex: 1, lineHeight: 1.4 }}>{t.message}</span>
          <button
            onClick={() => removeToast(t.id)}
            style={{
              background: 'none', border: 'none', color: 'var(--text-muted)',
              cursor: 'pointer', fontSize: '1rem', padding: 0, lineHeight: 1,
              flexShrink: 0, marginTop: '-0.1rem',
            }}
          >×</button>
        </div>
      ))}
    </div>
  );
}

// ─── Confirm Modal ──────────────────────────────────────────────────────────

function ConfirmModal({ modal, onConfirm, onCancel }) {
  if (!modal) return null;

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 10000,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'rgba(0,0,0,0.6)',
      backdropFilter: 'blur(4px)',
      WebkitBackdropFilter: 'blur(4px)',
      animation: 'modal-bg-in 0.2s ease',
    }}>
      <div style={{
        background: 'var(--bg-secondary)',
        border: '1px solid var(--border-primary)',
        borderRadius: 'var(--radius-lg)',
        padding: '1.75rem',
        maxWidth: '440px',
        width: '90%',
        boxShadow: '0 16px 64px rgba(0,0,0,0.5)',
        animation: 'modal-in 0.25s cubic-bezier(0.34, 1.56, 0.64, 1)',
      }}>
        {/* Icon */}
        <div style={{
          width: '48px', height: '48px', borderRadius: '50%',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: '1.5rem', marginBottom: '1rem',
          background: modal.type === 'danger' ? 'rgba(255,59,92,0.15)' : 'rgba(138,180,248,0.15)',
          border: `1px solid ${modal.type === 'danger' ? 'rgba(255,59,92,0.3)' : 'rgba(138,180,248,0.3)'}`,
        }}>
          {modal.type === 'danger' ? '⚠️' : '❓'}
        </div>

        {/* Title */}
        <div style={{
          fontSize: '1.05rem', fontWeight: 700, color: 'var(--text-primary)',
          marginBottom: '0.5rem',
        }}>
          {modal.title || 'Confirmar acción'}
        </div>

        {/* Message */}
        <div style={{
          fontSize: '0.85rem', color: 'var(--text-secondary)',
          lineHeight: 1.5, marginBottom: '1.5rem',
        }}>
          {modal.message}
        </div>

        {/* Buttons */}
        <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end' }}>
          <button
            className="btn btn--ghost"
            onClick={onCancel}
            style={{ padding: '0.5rem 1.25rem', fontSize: '0.85rem' }}
          >
            Cancelar
          </button>
          <button
            className={`btn ${modal.type === 'danger' ? 'btn--danger' : 'btn--primary'}`}
            onClick={onConfirm}
            style={{
              padding: '0.5rem 1.25rem', fontSize: '0.85rem',
              ...(modal.type === 'danger' ? { background: 'var(--color-loss)', borderColor: 'var(--color-loss)', color: '#fff' } : {}),
            }}
          >
            {modal.confirmText || 'Confirmar'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Provider ───────────────────────────────────────────────────────────────

let _toastId = 0;

export function UIProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const [modal, setModal] = useState(null);
  const [modalResolve, setModalResolve] = useState(null);

  const addToast = useCallback((message, type = 'info', duration = 4000) => {
    const id = ++_toastId;
    setToasts(prev => [...prev, { id, message, type }]);
    if (duration > 0) {
      setTimeout(() => {
        setToasts(prev => prev.filter(t => t.id !== id));
      }, duration);
    }
  }, []);

  const removeToast = useCallback((id) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const toast = useCallback({
    success: (msg, dur) => addToast(msg, 'success', dur),
    error: (msg, dur) => addToast(msg, 'error', dur ?? 6000),
    warning: (msg, dur) => addToast(msg, 'warning', dur),
    info: (msg, dur) => addToast(msg, 'info', dur),
  }, [addToast]);

  // confirm() returns a Promise<boolean>
  const confirmAction = useCallback((opts) => {
    return new Promise((resolve) => {
      const config = typeof opts === 'string' ? { message: opts } : opts;
      setModal(config);
      setModalResolve(() => resolve);
    });
  }, []);

  function handleConfirm() {
    modalResolve?.(true);
    setModal(null);
    setModalResolve(null);
  }

  function handleCancel() {
    modalResolve?.(false);
    setModal(null);
    setModalResolve(null);
  }

  return (
    <ToastContext.Provider value={toast}>
      <ConfirmContext.Provider value={confirmAction}>
        {children}
        <Toast toasts={toasts} removeToast={removeToast} />
        <ConfirmModal modal={modal} onConfirm={handleConfirm} onCancel={handleCancel} />
      </ConfirmContext.Provider>
    </ToastContext.Provider>
  );
}
