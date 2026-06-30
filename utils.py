# utils.py
# ============================================================
# UTILIDADES — LOGGING, PNL HISTORY, DASHBOARD, LOCK
# ============================================================

import os
import csv
import time
import logging
from datetime import datetime
from typing import Dict, Optional

import config

# ============================================================
# CREAR DIRECTORIOS
# ============================================================

os.makedirs(config.LOGS_DIR, exist_ok=True)
os.makedirs(config.METRICS_DIR, exist_ok=True)
os.makedirs(config.SNAPSHOTS_DIR, exist_ok=True)

# ============================================================
# LOGGING SIMPLE
# ============================================================

LOG_FILE = os.path.join(config.LOGS_DIR, "bot.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

_logger = logging.getLogger('krishna')

def log_info(msg):
    _logger.info(msg)

def log_warning(msg):
    _logger.warning(msg)

def log_error(msg):
    _logger.error(msg)

def log_debug(msg):
    _logger.debug(msg)

# ============================================================
# HISTORIAL DE PNL
# ============================================================

PNL_FILE = os.path.join(config.METRICS_DIR, "pnl_history.csv")

def init_pnl_file():
    if not os.path.exists(PNL_FILE):
        with open(PNL_FILE, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'fecha', 'hora', 'equity', 'pnl_acumulado',
                'pnl_ejecucion', 'trades', 'modo_riesgo'
            ])

def append_pnl_row(equity: float, pnl_total: float, pnl_ejecucion: float,
                   trades: int, modo: str):
    init_pnl_file()
    now = datetime.now()
    with open(PNL_FILE, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            now.strftime('%Y-%m-%d'),
            now.strftime('%H:%M:%S'),
            round(equity, 2),
            round(pnl_total, 2),
            round(pnl_ejecucion, 2),
            trades,
            modo
        ])

# ============================================================
# DASHBOARD SIMPLE
# ============================================================

_last_dashboard = ""
_last_update = 0
_DASHBOARD_INTERVAL = 5

def update_dashboard(estado: str, symbol: str = None, side: str = None,
                     equity: float = 0.0, pnl_total: float = 0.0,
                     trades: int = 0, modo: str = "NORMAL"):
    global _last_dashboard, _last_update

    pos_line = f"Posición: {symbol or 'Ninguna'}"
    if symbol and side:
        pos_line += f" {side.upper()}"

    dashboard = f"""
========================================
KRISHNA KILLING SPREE

Estado: {estado}
{pos_line}
Equity: {equity:.2f} USDT
PnL Total: {'+' if pnl_total >= 0 else ''}{pnl_total:.2f} USDT
Trades: {trades}
Modo: {modo}
========================================
"""

    now = time.time()
    if dashboard != _last_dashboard or (now - _last_update) > _DASHBOARD_INTERVAL:
        os.system('cls' if os.name == 'nt' else 'clear')
        print(dashboard)
        _last_dashboard = dashboard
        _last_update = now

# ============================================================
# LOCK
# ============================================================

LOCK_FILE = '/tmp/krishna_killing_spree.lock'

def acquire_lock(timeout: int = 5):
    try:
        import fcntl
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fd
    except ImportError:
        return True
    except Exception:
        return None

def release_lock(lock_fd):
    if lock_fd and hasattr(lock_fd, 'close'):
        try:
            import fcntl
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
        except Exception:
            pass

# ============================================================
# HELPERS
# ============================================================

def safe_float(value, default=0.0):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default

def now():
    return time.time()

def datetime_now():
    return datetime.now().isoformat()
