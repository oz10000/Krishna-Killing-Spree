# config.py
# ============================================================
# CONFIGURACIÓN DEL BOT — BLACKBIRD V2 (GITHUB ACTIONS)
# ============================================================

# ---- ACTIVOS ----
SYMBOLS = [
    'BTC-USDT-SWAP',
    'ETH-USDT-SWAP',
    'SOL-USDT-SWAP',
    'ADA-USDT-SWAP',
    'XRP-USDT-SWAP',
    'AVAX-USDT-SWAP',
]

# ---- APALANCAMIENTO BASE ----
BASE_LEVERAGE = 7

# ---- TAMAÑO DE POSICIÓN BASE (USDT nocionales) ----
BASE_POSITION_SIZE = 1000.0

# ---- CAPITAL INICIAL (USDT) ----
CAPITAL_INICIAL = 100.0

# ---- UMBRAL DE SCORE (0-1) ----
MIN_SCORE = 0.45

# ---- TP/SL MÚLTIPLOS DE ATR ----
TP_MULT = 1.2
SL_MULT = 1.0

# ---- PERIODOS DE INDICADORES ----
EMA_FAST = 20
EMA_SLOW = 50
ATR_PERIOD = 14
ADX_PERIOD = 14
MOMENTUM_PERIOD = 5

# ---- DRAWDOWN CONTROL ----
DD_NORMAL_LIMIT = 8.0       # 0-8%: operación normal
DD_REDUCED_LIMIT = 12.0     # 8-12%: modo reducido
DD_PROTECTION_LIMIT = 15.0  # ≥12%: modo protección / kill switch

# ---- APALANCAMIENTO POR MODO ----
LEVERAGE_NORMAL = 7
LEVERAGE_REDUCED = 3
LEVERAGE_PROTECTION = 1

# ---- FACTOR DE TAMAÑO POR MODO ----
SIZE_FACTOR_NORMAL = 1.0
SIZE_FACTOR_REDUCED = 0.6
SIZE_FACTOR_PROTECTION = 0.2

# ---- KILL SWITCH ----
KILL_THRESHOLD = 15.0  # % DD que activa el kill switch
KILL_SWITCH_ENABLED = True

# ---- COOLDOWN POR SÍMBOLO (segundos) ----
COOLDOWN_SECONDS = 15 * 60  # 15 minutos

# ---- MÁXIMO DE POSICIONES SIMULTÁNEAS ----
MAX_POSITIONS = 1  # 1 posición global

# ---- CICLO (para logging de frecuencia) ----
CYCLE_INTERVAL_SECONDS = 5 * 60  # 5 minutos (default, pero se usa para métricas)

# ---- DIRECTORIO DE MÉTRICAS ----
METRICS_DIR = "metrics"
LOGS_DIR = "logs"
SNAPSHOTS_DIR = "snapshots"

# ---- OKX API (variables de entorno) ----
# OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE, OKX_DEMO
