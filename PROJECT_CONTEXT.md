# Contexto del Proyecto: Bot de Opciones GGAL — IOL API v2

## Resumen ejecutivo

Bot de trading algorítmico intradiario que opera la cadena de opciones de **Grupo Financiero Galicia (GGAL)** en la **Bolsa de Buenos Aires (ByMA)** a través de la API REST v2 de **InvertirOnline (IOL)**. La ventaja estructural del sistema es la **bonificación del 100% de la comisión IOL en la pata de cierre intradiario**, lo que reduce el costo del round-trip de ~1.09% a ~0.24% (solo Derechos ByMA).

La estrategia central es **provisión de liquidez pasiva**: inyectar órdenes limitadas en bid/ask aprovechando ineficiencias transitorias en la curva de volatilidad implícita. No compite en latencia (la API REST no lo permite).

---

## Estructura del proyecto

```
Conexion IOL/
├── server.py            # Punto de entrada — FastAPI + Uvicorn
├── engine.py            # Motor de orquestación multi-estrategia
├── db.py                # Persistencia SQLite (slots, historial, P&L)
├── api/                 # API REST + WebSocket
│   ├── __init__.py
│   ├── models.py        # Pydantic models (request/response)
│   └── routes.py        # Endpoints REST y WebSocket
├── iol_client.py        # Módulo 1 — Cliente API IOL
├── market_data.py       # Módulo 2 — Market Data
├── math_engine.py       # Módulo 3 — Motor Matemático
├── oms.py               # Módulo 4 — OMS (con soporte de fondos)
├── strategy.py          # Módulo 5 — Motor de Estrategia (Options Mispricing BSM)
├── strategy_bull_spread.py  # Módulo 5b — Bull Call Spread Direccional
├── main.py              # Loop CLI original (legacy, funcional)
├── frontend/            # Interfaz web (Vite + React)
│   ├── src/
│   │   ├── App.jsx
│   │   ├── index.css    # Design system (dark theme financiero)
│   │   ├── api/
│   │   │   └── client.js    # REST + WebSocket client
│   │   ├── components/
│   │   │   ├── Header.jsx
│   │   │   ├── StrategyCard.jsx
│   │   │   ├── StrategyForm.jsx
│   │   │   ├── PositionsTable.jsx
│   │   │   ├── SignalsList.jsx
│   │   │   └── LogViewer.jsx
│   │   └── pages/
│   │       ├── Dashboard.jsx
│   │       └── StrategyDetail.jsx
│   ├── dist/            # Build de producción (servido por FastAPI)
│   ├── vite.config.js
│   └── package.json
├── trading_platform.db  # SQLite (auto-generado, no commitear)
├── explore_ggal.py      # Script de exploración original
├── demo_market_data.py  # Demo Módulo 2
├── demo_math_engine.py  # Demo Módulo 3
├── demo_oms.py          # Demo integración M1+M2+M3+M4
├── debug_tickers.py     # Util: inspeccionar raw de la API
├── .env                 # Credenciales IOL (no commitear)
├── .env.example
├── requirements.txt
└── PROJECT_CONTEXT.md   # Este archivo
```

---

## Arquitectura de la plataforma

```
┌─────────────────────────────────────────────────────────┐
│                    FRONTEND (Vite + React)               │
│   Dashboard: estrategias, posiciones, P&L, logs, config  │
│                   Puerto :5173 (dev) / :8000 (prod)      │
└───────────────────────┬─────────────────────────────────┘
                        │ REST + WebSocket
┌───────────────────────┴─────────────────────────────────┐
│                   BACKEND (FastAPI) :8000                 │
│                                                          │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ Strategy     │  │ OMS (por     │  │ MarketDataFeed │  │
│  │ (N slots,    │  │  slot, con   │  │ (compartido x  │  │
│  │  c/u con su  │  │  max_nominal)│  │  activo)       │  │
│  │  config +    │  │              │  │                │  │
│  │  fondos)     │  │              │  │                │  │
│  └─────────────┘  └──────────────┘  └────────────────┘  │
│                                                          │
│  ┌──────────────────────┐  ┌──────────────────────────┐  │
│  │ IOLClient (singleton)│  │ SQLite (db.py)           │  │
│  └──────────────────────┘  └──────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

### Concepto clave: StrategySlot

Un **StrategySlot** es una instancia de estrategia en ejecución. El engine (ver `engine.py`)
gestiona N slots simultáneos, cada uno con su propio OMS y Strategy, pero compartiendo
el IOLClient y los MarketDataFeeds por activo.

| Campo | Ejemplo |
|-------|---------|
| `id` | `"561ff963"` |
| `nombre` | `"GGAL Conservadora"` |
| `tipo_estrategia` | `"options_mispricing"` |
| `activo` | `"GGAL"` |
| `mercado` | `"bCBA"` |
| `fondos_asignados` | `500000.0` ARS |
| `config` | `{min_mispricing_pct: 0.05, ...}` |
| `dry_run` | `true` |
| `estado` | `"running"` / `"paused"` / `"stopped"` |

### Ejecutar la plataforma

**Producción** (un solo comando):
```
python server.py
# Abre http://localhost:8000
```

**Desarrollo** (dos terminales):
```
# Terminal 1 — Backend
python server.py

# Terminal 2 — Frontend con HMR
cd frontend && npm run dev
# Abre http://localhost:5173
```

---

## `server.py` — Servidor FastAPI

### Propósito
Punto de entrada principal. Reemplaza `main.py` como forma de correr la plataforma.

### Funcionalidad
- Inicializa `TradingEngine` (IOLClient + DB) en el `lifespan`
- Monta rutas REST (`/api/*`) y WebSocket (`/ws/live`)
- Sirve el frontend compilado desde `frontend/dist/` como archivos estáticos
- CORS habilitado para desarrollo con Vite en `:5173`
- Puerto: **8000**

---

## `engine.py` — Motor de Orquestación Multi-Estrategia

### Propósito
Cerebro de la plataforma. Coordina múltiples StrategySlots concurrentes.

### Clase: `TradingEngine`

**Recursos compartidos:**
- Un `IOLClient` singleton (una sola conexión OAuth a IOL)
- Un `MarketDataFeed` por activo (si 3 slots operan GGAL, comparten un feed)

**Recursos por slot:**
- Un `OMS` con `max_nominal` para limitar fondos
- Un `Strategy` con su propia `StrategyConfig`

### Métodos principales
| Método | Descripción |
|--------|-------------|
| `initialize()` | Conecta IOLClient, inicializa DB, carga slots guardados |
| `shutdown()` | Cierra posiciones, detiene feeds, desconecta IOL |
| `add_strategy(data)` | Crea slot, lo persiste en SQLite, retorna `slot_id` |
| `update_strategy(slot_id, data)` | Modifica config (solo si detenido) |
| `remove_strategy(slot_id)` | Cierra posiciones y elimina slot |
| `start_strategy(slot_id)` | Arranca feed + OMS + Strategy |
| `pause_strategy(slot_id)` | Pausa evaluación (feed sigue corriendo) |
| `stop_strategy(slot_id)` | Cierra posiciones y detiene todo |
| `get_all_slots()` | Estado serializado de todos los slots |
| `get_account_info()` | Consulta saldo de cuenta IOL |

### Tipos de estrategia disponibles (`STRATEGY_TYPES`)
- `options_mispricing` — Provisión de liquidez pasiva BSM (mispricing entre precio mercado e IV teórica)
- `bull_call_spread` — Spread vertical alcista: compra Call ATM + venta Call OTM. Basado en análisis cuantitativo COME abril 2026.

---

## `db.py` — Persistencia SQLite

### Propósito
Almacena configuraciones de StrategySlots y historial de operaciones.
Archivo: `trading_platform.db` (auto-generado en la raíz del proyecto).

### Tablas
| Tabla | Descripción |
|-------|-------------|
| `strategy_slots` | Configuración guardada de cada slot |
| `positions_history` | Historial de posiciones cerradas |
| `daily_pnl` | P&L agregado por día por slot |

### Funciones principales
- `init_db()` — crea tablas si no existen
- `save_slot()` / `load_all_slots()` / `delete_slot()` — CRUD de slots
- `save_position()` / `get_positions_history()` — historial de posiciones
- `upsert_daily_pnl()` / `get_daily_pnl()` — P&L diario

---

## `api/` — API REST y WebSocket

### Endpoints REST (`api/routes.py`)

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/status` | Estado global (conexión, # estrategias, P&L total) |
| `GET` | `/api/account` | Saldo de la cuenta IOL |
| `GET` | `/api/strategy-types` | Tipos de estrategia disponibles con params |
| `GET` | `/api/strategies` | Lista de todos los StrategySlots |
| `POST` | `/api/strategies` | Crear nuevo slot |
| `GET` | `/api/strategies/{id}` | Detalle de un slot |
| `PUT` | `/api/strategies/{id}` | Modificar config/fondos de un slot |
| `DELETE` | `/api/strategies/{id}` | Eliminar slot |
| `POST` | `/api/strategies/{id}/start` | Arrancar estrategia |
| `POST` | `/api/strategies/{id}/pause` | Pausar estrategia |
| `POST` | `/api/strategies/{id}/stop` | Detener estrategia |
| `GET` | `/api/strategies/{id}/logs` | Últimos N logs del slot |

### WebSocket (`/ws/live`)
- Envía snapshots en tiempo real por cada slot activo
- Eventos: `init`, `snapshot`, `slot_created`, `slot_started`, `slot_paused`, `slot_stopped`, `slot_removed`
- Heartbeat cada 30s para mantener la conexión

### Pydantic Models (`api/models.py`)
- `CreateStrategyRequest` — body para crear slot
- `UpdateStrategyRequest` — body para actualizar slot
- `StrategyResponse` — serialización completa de un slot
- `StatusResponse` — estado global

---

## Frontend (`frontend/`)

### Stack
- **Vite** — bundler + dev server con HMR
- **React** — componentes funcionales con hooks
- **Vanilla CSS** — design system propio (sin Tailwind)

### Diseño
Dashboard financiero oscuro tipo Bloomberg:
- Fondo `#0a0e17`, acentos púrpura (`#7c5cff`)
- Verde `#00d97e` para ganancias, rojo `#ff3b5c` para pérdidas
- Tipografía: Inter (UI) + JetBrains Mono (números/datos)
- Animaciones sutiles: pulse en status dots, slide-in en logs, hover elevation en cards

### Páginas
1. **Dashboard** (`pages/Dashboard.jsx`):
   - Grid de StrategyCards con métricas y controles
   - Summary stats (total estrategias, ejecutando, posiciones, P&L)
   - Botón "+ Nueva Estrategia" → modal con formulario
   - Empty state atractivo si no hay estrategias

2. **Strategy Detail** (`pages/StrategyDetail.jsx`):
   - Stats de la estrategia (P&L, posiciones, fondos, spot)
   - Tabla de posiciones abiertas con P&L no realizado
   - Feed de señales activas
   - Log viewer estilo terminal
   - Config display con todos los parámetros

### Componentes
| Componente | Función |
|------------|---------|
| `Header.jsx` | Barra superior: conexión, hora, mercado, stats |
| `StrategyCard.jsx` | Card con estado, P&L, controles start/pause/stop |
| `StrategyForm.jsx` | Modal de creación con params dinámicos según tipo |
| `PositionsTable.jsx` | Tabla de posiciones con color coding |
| `SignalsList.jsx` | Lista de señales con badges LONG/SHORT |
| `LogViewer.jsx` | Terminal de logs con auto-scroll |

### API Client (`api/client.js`)
- REST: wrapper de `fetch()` con error handling
- WebSocket: auto-reconnect cada 3s si se desconecta

---

## Módulo 1: `iol_client.py` — Cliente API IOL

### Propósito
Cliente HTTP asíncrono con autenticación OAuth2 y resiliencia ante rate limiting.

### Clases y funciones clave
- `IOLClient(username, password)` — cliente principal, usar como context manager async
- `IOLClient.authenticate()` — obtiene access_token + refresh_token
- `IOLClient._auto_refresh()` — tarea en background que renueva el token 60s antes de expirar
- `IOLClient._do_refresh()` — usa refresh_token para renovar sin reingreso de credenciales
- `IOLClient._request()` — wrapper HTTP con Exponential Backoff + Jitter para HTTP 429

### Endpoints implementados
| Método | Ruta | Función |
|--------|------|---------|
| GET | `/api/v2/datos-perfil` | `get_profile()` |
| GET | `/api/v2/{mercado}/Titulos/{simbolo}/Cotizacion` | `get_quote()` |
| GET | `/api/v2/{mercado}/Titulos/{simbolo}/Opciones` | `get_options_chain()` |
| GET | `/api/v2/portafolio/{pais}` | `get_portfolio()` |
| GET | `/api/v2/estadocuenta` | `get_account_state()` |
| GET | `/api/v2/operaciones` | `get_operations()` |
| GET | `/api/v2/Cotizaciones/{inst}/{panel}/{pais}` | `get_cotizaciones_panel()` |
| GET | `/api/v2/{mercado}/Titulos/{simbolo}` | `get_titulo()` |
| POST | `/api/v2/operar/v2/Ordenes` | `place_order()` |
| DELETE | `/api/v2/operar/v2/Ordenes/{id}` | `cancel_order()` |
| GET | `/api/v2/operaciones/{id}` | `get_order()` |

### Parámetros OAuth
- URL del token: `POST https://api.invertironline.com/token`
- Content-Type: `application/x-www-form-urlencoded`
- Body: `username`, `password`, `grant_type=password`
- `expires_in`: **1200 segundos** (20 minutos, no 30 como se podría asumir)
- `refresh_token` válido 20 minutos adicionales después del vencimiento del access_token

### Backoff configurado (BACKOFF_RANGES)
| Intento | Rango de espera |
|---------|----------------|
| 1 | 500–1000 ms |
| 2 | 1000–2000 ms |
| 3 | 2000–4000 ms |
| 4 | 4000–8000 ms |
| 5 | 8000–16000 ms |

---

## Módulo 2: `market_data.py` — Market Data

### Propósito
Polling configurable de la cadena de opciones. Parser de tickers ByMA. Emite snapshots tipados para M3 y M4.

### Clases clave
- `OptionQuote` — cotización de una opción (simbolo, tipo, strike, expiry, bid, ask, ultimo, volumen). Propiedades calculadas: `mid`, `spread_pct`, `dias_al_vencimiento`
- `MarketSnapshot` — foto completa (spot + lista de OptionQuote). Métodos: `calls()`, `puts()`, `by_expiry()`, `expiries()`, `resumen()`
- `MarketDataFeed(client, mercado, subyacente, interval)` — servicio de polling configurable por activo

### Función `parse_ticker(simbolo)`
Parsea tickers ByMA. Retorna `(tipo, strike, expiry)` o `None`.
- Formato: `{base}{C|V}{strike_entero}{codigo_mes}`
- Ejemplo: `GFGC71487A` → Call, 7148.7, 2026-04-17
- Calls: códigos A–L = meses 1–12
- Puts: códigos M–X = meses 1–12

### Gotchas críticos de la API (descubiertos empíricamente)

1. **Estructura nested**: los precios están en `raw["cotizacion"]["ultimoPrecio"]`, NO en el root del objeto. El root solo tiene metadatos (simbolo, tipoOpcion, fechaVencimiento, descripcion).

2. **Strike en el ticker**: el valor entero del ticker está desplazado 1 decimal (`43487` = `4348.70 ARS`). La fuente primaria confiable es `raw["descripcion"]` → `"Call GGAL 4,348.70 Vencimiento: 17/04/2026"` → splitear por espacios, índice 2.

3. **`tipoOpcion`**: la API devuelve `"Call"` y `"Put"` (capitalizados, no uppercase). Manejar case-insensitive.

4. **`puntas`**: durante el horario fuera de mercado vale `null`. Solo tiene bid/ask durante la rueda.

5. **168 opciones** en la cadena GGAL (84 calls + 84 puts, 3 vencimientos activos en abril 2026).

6. **Vencimientos activos** (al 11/04/2026): 17/04/2026 (6 DTE), 15/05/2026 (34 DTE), 19/06/2026 (69 DTE).

---

## Módulo 3: `math_engine.py` — Motor Matemático

### Propósito
Valuación teórica BSM, griegas de primer orden y volatilidad implícita. Sin dependencias externas (usa `math.erfc` para la normal).

### Funciones clave
- `bsm_price(tipo, S, K, T, r, sigma, q=0)` → float
- `bsm_greeks(tipo, S, K, T, r, sigma, q=0)` → `GreeksResult`
- `implied_vol(tipo, market_price, S, K, T, r, q=0)` → float | None
- `adjust_spot_for_dividends(S, r, dividends, hoy, expiry)` → float ajustado
- `enrich_snapshot(snapshot, r, dividends, q)` → `list[OptionPricing]`

### `GreeksResult` (dataclass)
| Campo | Descripción |
|-------|-------------|
| `price` | Precio teórico BSM en ARS |
| `delta` | Δ — var. precio por ARS de movimiento del spot |
| `gamma` | Γ — var. de delta por ARS de movimiento del spot |
| `theta` | Θ — decaimiento temporal en ARS por día calendario |
| `vega` | ν — var. precio por 1% de cambio en σ |
| `rho` | ρ — var. precio por 1% de cambio en r |
| `iv` | Volatilidad implícita (ej: 0.846 = 84.6%) o None si no calculable |

### `OptionPricing` (dataclass)
Par `(quote: OptionQuote, greeks: GreeksResult)`. Propiedad `mispricing` = `mid_mercado − precio_BSM`.

### Parámetros de configuración
- `DEFAULT_RISK_FREE_RATE = 0.45` — **45% anual** en ARS. Actualizar con tasa de cauciones bursátiles vigente.
- `_IV_INITIAL_GUESS = 0.80` — 80% anual (rango típico GGAL: 60–120%)
- `MIN_OPTION_PRICE = 0.01` — precio mínimo para calcular IV

### IV — Newton-Raphson + Bisección fallback
- NR: máx 100 iteraciones, tolerancia 1e-6
- Bisección fallback: 300 iteraciones
- Límites σ: [1e-6, 20.0]
- Retorna None si precio fuera de límites BSM o T ≤ 0

### Contexto ByMA 2026 — Dividendos
Desde series con vencimiento en **julio 2026**, ByMA ya **no** ajusta el strike por dividendos ordinarios. La función `adjust_spot_for_dividends()` resta el valor presente de los dividendos al spot antes de valuar. Ignorar este ajuste genera señales falsas de arbitraje.

### Resultados observados (11/04/2026, mercado cerrado)
- ATM (GFGC71487A, strike 7149, spot 7085): IV = 34.6%, Δ = 0.495, Θ = -14.6 ARS/día
- Skew visible: IV aumenta para strikes OTM (deep OTM puede superar 100%)
- Puts ATM tienen IV mayor que calls ATM (~42% vs ~35%) — consistente con mercado argentino

---

## Módulo 4: `oms.py` — Order Management System

### Propósito
Ejecución de órdenes, seguimiento de posiciones, cálculo de comisiones y cierre automático intradiario. **Dry-run por defecto** (nunca envía órdenes reales sin `dry_run=False`).

### Clases clave
- `IOLProfile` (enum): GOLD (0.5%), PLATINUM (0.3%), BLACK (0.1%)
- `calc_commission(nominal, profile, intraday_close)` — calcula comisión total incluyendo IVA
- `Order` (dataclass) — orden con estado, ID local/remoto, precio de ejecución
- `Position` (dataclass) — posición con apertura, cierre, P&L
- `OMS(client, mercado, profile, dry_run=True, max_nominal=None)` — orquestador principal

### Control de fondos (max_nominal)
El parámetro `max_nominal` limita el nominal total de posiciones abiertas simultáneas.
Cuando una nueva orden haría que `nominal_en_uso + nominal_nueva > max_nominal`, la orden
se rechaza con `OrderStatus.RECHAZADA`. Esto permite asignar fondos por estrategia sin
necesidad de subcuentas en IOL.

### Métodos principales de OMS
| Método | Descripción |
|--------|-------------|
| `open_position(simbolo, tipo, lado, cantidad, precio_limite)` | Apertura de posición |
| `close_position(pos_id, precio_limite)` | Cierre individual (aplica bonus intraday) |
| `cancel_order(local_id)` | Cancelar orden pendiente |
| `poll_orders()` | Consultar estado de órdenes en IOL (solo LIVE) |
| `nominal_en_uso` | Propiedad: nominal total de posiciones abiertas |
| `close_all_intraday(snapshot)` | Cierre de emergencia de todas las posiciones abiertas hoy |
| `audit_itm_risk(pricings, spot, dte_umbral=1)` | Detecta opciones ITM próximas a vencer |
| `on_snapshot(snapshot)` | Hook para conectar al MarketDataFeed |
| `start_auto_close_scheduler()` | Tarea async que dispara cierre a las 16:45 ARG |
| `reporte(snapshot)` | Resumen de posiciones y P&L |

### Modelo de comisiones IOL
| Perfil | Comisión IOL | Derechos ByMA | Costo apertura (~) | Costo cierre intraday |
|--------|-------------|---------------|-------------------|----------------------|
| GOLD | 0.50% | 0.20% | 0.847% | 0.242% |
| PLATINUM | 0.30% | 0.20% | 0.605% | 0.242% |
| BLACK | 0.10% | 0.20% | 0.363% | 0.242% |
- IVA (21%) se aplica sobre la suma de comisiones.
- La bonificación intradiaria exige: mismo símbolo, mismo día, cantidad ≤ apertura.

### Horario de mercado
- Apertura: 11:00 hs Argentina (UTC-3)
- Cierre: 17:00 hs Argentina
- Cutoff auto-cierre: **16:45 hs Argentina** (`_PRECLOSE_CUTOFF`)

---

## Módulo 5: `strategy.py` — Motor de Estrategia

### Propósito
Evalúa la cadena de opciones enriquecida (BSM + IV) y genera señales de trading
basadas en mispricing. Ejecuta órdenes via OMS respetando límites de portafolio.

### Clases clave
- `StrategyConfig` (dataclass) — todos los parámetros con defaults conservadores
- `TradeSignal` (dataclass) — señal con lado, precio límite, motivo y score
- `Strategy(oms, config)` — motor principal

### Métodos de Strategy
| Método | Descripción |
|--------|-------------|
| `evaluar(pricings)` | Aplica 6 filtros, retorna señales ordenadas por score. No ejecuta. |
| `ejecutar_signals(signals)` | Abre posiciones via OMS respetando `max_posiciones_abiertas` |
| `on_snapshot(snapshot)` | Callback para MarketDataFeed — enriquece, evalúa, ejecuta |

### Filtros de señal (en orden, falla-rápido)
1. Bid y ask no son `None` (liquidez mínima — fuera de rueda descarta todo)
2. `min_spread_pct (2%) ≤ spread_pct ≤ max_spread_pct (30%)`
3. `min_dte (5) ≤ dias_al_vencimiento ≤ max_dte (45)`
4. IV calculable (no `None`)
5. `min_delta_abs (0.15) ≤ |delta| ≤ max_delta_abs (0.85)`
6. `|mispricing / precio_BSM| ≥ min_mispricing_pct (5%)`

### Dirección de la señal
- `mispricing > 0` (mercado sobrevalora) → **SHORT**, precio límite = bid
- `mispricing < 0` (mercado subvalora) → **LONG**, precio límite = ask

### Parámetros de StrategyConfig (defaults)
| Parámetro | Default | Descripción |
|-----------|---------|-------------|
| `min_mispricing_pct` | 0.05 | 5% del precio BSM |
| `min_spread_pct` | 0.02 | Spread mínimo 2% |
| `max_spread_pct` | 0.30 | Spread máximo 30% |
| `min_dte` | 5 | Mínimo 5 días al vencimiento |
| `max_dte` | 45 | Máximo 45 días al vencimiento |
| `min_delta_abs` | 0.15 | Delta mínimo absoluto |
| `max_delta_abs` | 0.85 | Delta máximo absoluto |
| `lote_base` | 1 | Contratos por orden |
| `max_posiciones_abiertas` | 5 | Límite de posiciones simultáneas |

---

## Integración multi-estrategia (flujo de datos)

```
                         ┌──────────────────────────────┐
                         │   TradingEngine (engine.py)   │
                         │   IOLClient singleton         │
                         └──────┬───────────────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                  ▼
    ┌─────────────────┐ ┌─────────────────┐ ┌──────────────┐
    │ MarketDataFeed  │ │ MarketDataFeed  │ │   ...otros   │
    │ bCBA/GGAL       │ │ bCBA/YPFD       │ │   feeds      │
    └────────┬────────┘ └────────┬────────┘ └──────────────┘
             │                   │
    ┌────────┴────────┐          │
    ▼                 ▼          ▼
┌────────┐ ┌────────┐ ┌────────┐
│ Slot 1 │ │ Slot 2 │ │ Slot 3 │
│ OMS    │ │ OMS    │ │ OMS    │
│Strategy│ │Strategy│ │Strategy│
│$500k   │ │$300k   │ │$200k   │
└────────┘ └────────┘ └────────┘
     │           │          │
     └───────────┴──────────┘
               │
     ┌─────────┴─────────┐
     ▼                   ▼
 WebSocket          REST API
 (real-time)        (CRUD, control)
     │                   │
     └───────────────────┘
               │
     ┌─────────┴─────────┐
     │  React Frontend    │
     │  Dashboard + Detail│
     └────────────────────┘
```

---

## Datos de mercado observados (referencia al 11/04/2026)

- Spot GGAL: **7085 ARS**
- Vencimientos activos: 17/04, 15/05, 19/06/2026
- IV ATM (6 DTE): ~35–42%
- IV ATM (69 DTE): ~45–50%
- Estructura de IV: skew pronunciado, OTM profundo puede superar 100%
- Bid/Ask: null fuera de horario de rueda (11–17 hs ARG)

---

## Stack tecnológico

### Backend
- Python 3.11+
- `aiohttp` — cliente HTTP asíncrono (IOL API)
- `asyncio` — concurrencia
- `fastapi` — framework API REST + WebSocket
- `uvicorn` — servidor ASGI
- `aiosqlite` — SQLite asíncrono
- `pydantic` — validación de datos
- `python-dotenv` — variables de entorno
- `tzdata` — base de datos de zonas horarias (requerido en Windows)
- Sin dependencias externas para matemática (usa `math` stdlib)

### Frontend
- Node.js 18+
- Vite — bundler + dev server
- React — componentes funcionales con hooks
- Vanilla CSS — design system personalizado

### Nota sobre zona horaria
`oms.py` usa `timezone(timedelta(hours=-3))` (UTC-3 fijo) en lugar de
`ZoneInfo("America/Argentina/Buenos_Aires")`. Argentina no observa DST,
por lo que UTC-3 fijo es exacto y elimina la dependencia de `tzdata`
en el código. El paquete `tzdata` sigue en `requirements.txt` por
compatibilidad general.

## Credenciales

Las credenciales de IOL se leen desde `.env`:
```
IOL_USERNAME=...
IOL_PASSWORD=...
```
Nunca commitear `.env`. El archivo `.env.example` sirve como plantilla.
