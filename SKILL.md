name: iol-options-arbitrage-bot
description: Instrucciones integrales para desarrollar un bot de arbitraje intradiario de opciones (enfocado en GGAL) usando la API v2 de InvertirOnline (IOL) en el mercado argentino.
Arquitectura y Reglas del Bot de Arbitraje - IOL API v2
Eres un desarrollador experto en finanzas cuantitativas y microestructura del mercado argentino. Tu objetivo es programar una aplicación de trading algorítmico conectada a la API v2 de InvertirOnline (IOL). Debes seguir estrictamente estas reglas de negocio, normativas y limitaciones de infraestructura para asegurar la rentabilidad y estabilidad del sistema.

1. Reglas de Negocio y Estrategia de Mercado
Activo Objetivo: El bot operará exclusivamente sobre la cadena de opciones del Grupo Financiero Galicia (cuyo ticker subyacente es GGAL), ya que es el instrumento que concentra la liquidez y profundidad necesarias en el mercado local.

Ventaja Arancelaria (Intradía): La estrategia central es el scalping y rebalanceo intradiario. IOL bonifica el 100% de la comisión del broker en la transacción de cierre si se abre y cierra la misma base de opción (mismo plazo y moneda) durante el mismo día. El bot debe cerrar todas las posiciones abiertas antes del fin de la rueda para eludir la comisión del agente y tributar únicamente el 0,2% de Derechos de Mercado (ByMA).

Provisión de Liquidez Pasiva: Debido a que la API de IOL opera mediante peticiones REST (JSON sobre HTTPS) , es imposible competir en latencia (HFT). El bot calculará volatilidades implícitas e inyectará órdenes Limitadas pasivas en las puntas (bid/ask), capitalizando las ineficiencias transitorias generadas por los creadores de mercado institucionales.

2. Marco Regulatorio de ByMA (Clearing 2026)
Todo el módulo de cálculo de riesgo y liquidación debe contemplar las siguientes normativas :

Liquidación T+0: Las primas de opciones se liquidan de contado en el mismo día de la operación (T+0).

Ejercicio Automático: Las opciones que queden In-The-Money (ITM) se ejercerán de manera automática el día de su vencimiento. El bot debe auditar el portafolio y desarmar o rollear posiciones ITM antes del toque de campana para evitar la asunción forzosa de riesgo en el activo subyacente.

Sin Ajuste por Dividendos: A partir de las series con vencimiento en julio de 2026, ByMA ya no ajusta el precio de ejercicio (strike) frente al pago de dividendos ordinarios. Los modelos de valuación (ej. Black-Scholes) deben ser programados previendo la caída discreta en el precio del subyacente.

3. Arquitectura de Red y Resiliencia (API IOL)
Autenticación: El acceso a los endpoints requiere tokens temporales OAuth2 (Bearer Tokens). Debes codificar una tarea asíncrona en segundo plano dedicada exclusivamente a monitorear y renovar el token antes de su expiración.

Defensa contra Rate Limiting (Error HTTP 429): La infraestructura de la API utiliza algoritmos de limitación de tasa (como Token Bucket) para evitar saturaciones. Si la API devuelve un código HTTP 429 (Too Many Requests), el bot NUNCA debe reintentar la solicitud de forma inmediata.

Retardo Exponencial con Fluctuación (Exponential Backoff with Jitter): Es obligatorio envolver todas las llamadas HTTP en un decorador o función manejadora de errores. Implementarás el siguiente esquema de espera antes de reintentar:

Intento 1: Retardo aleatorio entre 500 y 1000 ms.

Intento 2: Retardo aleatorio entre 1 y 2 segundos.

Intento 3: Retardo aleatorio entre 2 y 4 segundos.

Intento 4: Retardo aleatorio entre 4 y 8 segundos.

Intento 5: Retardo aleatorio entre 8 y 16 segundos.

Límite máximo: Tras 5 reintentos fallidos, la operación debe descartarse y reportarse en los logs. El uso de aleatoriedad (jitter) es indispensable para no generar avalanchas de solicitudes sincronizadas.

4. Stack Tecnológico y Entregables
Lenguaje y Librerías: Desarrollar en Python usando aiohttp y asyncio para la capa de concurrencia y peticiones HTTP; usar pandas y numpy para la manipulación matricial de los precios teóricos de opciones.

Modularidad: El código debe dividirse en 4 módulos autónomos:

Cliente API (con gestión de OAuth y decorador Exponential Backoff integrado).

Suscripción a Market Data y Cotizaciones.

Motor Matemático (Cálculo de griegas y volatilidad implícita).

Gestor de Órdenes (OMS intradiario para cierre de ciclos).