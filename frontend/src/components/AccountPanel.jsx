import { useState, useEffect } from 'react';
import { api } from '../api/client';

export default function AccountPanel() {
  const [accountInfo, setAccountInfo] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    const fetchAccount = async () => {
      try {
        const data = await api.getAccount();
        if (mounted && data && !data.error) {
          setAccountInfo(data);
        }
      } catch (err) {
        console.error("Error al obtener la cuenta:", err);
      } finally {
        if (mounted) setLoading(false);
      }
    };

    // Al cargar la app y luego cada 15 segundos
    fetchAccount();
    const timer = setInterval(fetchAccount, 15000);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, []);

  if (loading) {
    return (
      <div className="account-panel-container fade-in">
        <div className="glass-panel" style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)' }}>
          <span className="spinner spinner--primary" style={{ marginRight: '0.75rem' }}></span>
          Cargando datos de la cuenta...
        </div>
      </div>
    );
  }

  // IOL Estadísticas
  const cuentas = accountInfo?.cuentas || [];
  
  // Buscar cuenta en ARS (IOL las llama a veces "Peso Argentino" o "ARS")
  const arsAccount = cuentas.find(c => 
    String(c.moneda).toLowerCase().includes('peso') || 
    String(c.moneda).toUpperCase() === 'ARS'
  ) || cuentas[0]; // fallback a la primera que vuelva

  const formatARS = (val) => new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'ARS' }).format(val || 0);

  if (!arsAccount) {
    return null;
  }

  return (
    <div className="account-panel-container fade-in">
      <div className="glass-panel account-panel-grid">
        <div className="account-panel-item">
            <span className="account-label">Valor Total de Cuenta</span>
            <span className="account-value">{formatARS(arsAccount.total)}</span>
        </div>
        <div className="account-panel-divider" />
        <div className="account-panel-item">
            <span className="account-label">Poder de Compra (Líquido)</span>
            <span className="account-value value-green">{formatARS(arsAccount.disponible)}</span>
        </div>
        <div className="account-panel-divider" />
        <div className="account-panel-item">
            <span className="account-label">Capital en Estrategias</span>
            <span className="account-value">{formatARS(arsAccount.titulos)}</span>
        </div>
      </div>
    </div>
  );
}
