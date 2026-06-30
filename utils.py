# utils.py
# ============================================================
# UTILIDADES — LOGGING, HELPERS, LOCK
# ============================================================

import logging
import os
import time
import json
from datetime import datetime

import config

# ============================================================
# LOGGING
# ============================================================

os.makedirs(config.LOGS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f"{config.LOGS_DIR}/bot.log"),
        logging.StreamHandler()
    ]
)

_logger = logging.getLogger('blackbird')

def log_info(msg):
    _logger.info(msg)

def log_warning(msg):
    _logger.warning(msg)

def log_error(msg):
    _logger.error(msg)

def log_debug(msg):
    _logger.debug(msg)

# ============================================================
# LOCK (para evitar ejecuciones simultáneas)
# ============================================================

LOCK_FILE = '/tmp/blackbird_v2.lock'

def acquire_lock(timeout: int = 5) -> bool:
    """Adquiere un lock para evitar ejecuciones simultáneas."""
    try:
        import fcntl
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fd
    except ImportError:
        # Windows o entorno sin fcntl
        return True
    except Exception:
        return None

def release_lock(lock_fd):
    """Libera el lock."""
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

def format_pnl(pnl_pct: float) -> str:
    if pnl_pct > 0:
        return f"+{pnl_pct:.2f}%"
    return f"{pnl_pct:.2f}%"
