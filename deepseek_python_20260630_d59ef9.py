# metrics.py
# ============================================================
# MÉTRICAS COMPLETAS — POR CICLO, TRADE Y AGREGADO
# ============================================================
# Registra:
#   - por ciclo: timestamp, símbolos evaluados, best score, trade ejecutado, latency
#   - por trade: entry, exit, pnl, leverage, size, duración
#   - agregados: trades/min, pnl/h, win rate, profit factor, sharpe, max dd
# ============================================================

import json
import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Any
from collections import deque

import config


class MetricsCollector:
    def __init__(self):
        self.cycles = []
        self.trades = []
        self.equity_curve = [config.CAPITAL_INICIAL]
        self.peak_equity = config.CAPITAL_INICIAL
        self.max_drawdown = 0.0

        # Agregados en memoria
        self._trade_window = deque(maxlen=1000)
        self._cycle_window = deque(maxlen=1000)

        os.makedirs(config.METRICS_DIR, exist_ok=True)
        os.makedirs(config.LOGS_DIR, exist_ok=True)

    # ============================================================
    # REGISTRO POR CICLO
    # ============================================================

    def log_cycle(self, data: Dict):
        """Registra un ciclo de ejecución."""
        entry = {
            'timestamp': datetime.now().isoformat(),
            'timestamp_unix': time.time(),
            **data
        }
        self.cycles.append(entry)
        self._cycle_window.append(entry)

        # Guardar cada 10 ciclos
        if len(self.cycles) % 10 == 0:
            self._save_cycles()

    # ============================================================
    # REGISTRO POR TRADE
    # ============================================================

    def log_trade(self, data: Dict):
        """Registra un trade completado."""
        entry = {
            'timestamp': datetime.now().isoformat(),
            'timestamp_unix': time.time(),
            **data
        }
        self.trades.append(entry)
        self._trade_window.append(entry)

        # Actualizar equity curve
        if 'equity_after' in data:
            equity = data['equity_after']
            self.equity_curve.append(equity)
            if equity > self.peak_equity:
                self.peak_equity = equity
            dd = (self.peak_equity - equity) / self.peak_equity * 100 if self.peak_equity > 0 else 0
            if dd > self.max_drawdown:
                self.max_drawdown = dd

        # Guardar cada trade
        self._save_trade(entry)

    # ============================================================
    # MÉTRICAS AGREGADAS
    # ============================================================

    def get_aggregated_metrics(self) -> Dict:
        """Calcula métricas agregadas de todos los trades."""
        trades = self.trades
        n = len(trades)
        if n == 0:
            return {
                'total_trades': 0,
                'win_rate': 0.0,
                'profit_factor': 0.0,
                'avg_pnl': 0.0,
                'total_pnl': 0.0,
                'max_drawdown': self.max_drawdown,
                'sharpe_ratio': 0.0,
                'calmar_ratio': 0.0,
            }

        pnls = [t.get('pnl_pct', 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        win_rate = len(wins) / n * 100 if n > 0 else 0
        total_wins = sum(wins) if wins else 0
        total_losses = abs(sum(losses)) if losses else 0
        profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')

        avg_pnl = sum(pnls) / n if n > 0 else 0
        total_pnl = sum(pnls)

        # Sharpe (anualizado simplificado)
        if len(pnls) > 1:
            mean_pnl = sum(pnls) / len(pnls)
            std_pnl = (sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls)) ** 0.5 if len(pnls) > 1 else 0.01
            # Asumiendo 288 ciclos de 5 min por día ≈ 6.9 trades/día
            n_trades_per_year = len(pnls) / (self._get_days_elapsed() or 1) * 365
            sharpe = (mean_pnl / (std_pnl + 0.0001)) * (n_trades_per_year ** 0.5) if n_trades_per_year > 0 else 0
        else:
            sharpe = 0.0

        calmar = (total_pnl / 100) / (self.max_drawdown / 100) if self.max_drawdown > 0 else 0

        return {
            'total_trades': n,
            'win_rate': round(win_rate, 2),
            'profit_factor': round(profit_factor, 2),
            'avg_pnl': round(avg_pnl, 2),
            'total_pnl': round(total_pnl, 2),
            'max_drawdown': round(self.max_drawdown, 2),
            'sharpe_ratio': round(sharpe, 2),
            'calmar_ratio': round(calmar, 2),
            'total_wins': len(wins),
            'total_losses': len(losses),
            'equity_peak': round(self.peak_equity, 2),
            'equity_current': round(self.equity_curve[-1] if self.equity_curve else config.CAPITAL_INICIAL, 2),
        }

    def _get_days_elapsed(self) -> float:
        if not self.trades:
            return 0.001
        first = self.trades[0].get('timestamp_unix', time.time())
        last = self.trades[-1].get('timestamp_unix', time.time())
        return (last - first) / 86400.0 if (last - first) > 0 else 0.001

    # ============================================================
    # PERSISTENCIA
    # ============================================================

    def _save_cycles(self):
        """Guarda el historial de ciclos."""
        filename = f"{config.METRICS_DIR}/cycles_{datetime.now().strftime('%Y%m%d')}.json"
        try:
            existing = []
            if os.path.exists(filename):
                with open(filename, 'r') as f:
                    existing = json.load(f)
            all_data = existing + self.cycles[-100:]
            with open(filename, 'w') as f:
                json.dump(all_data[-1000:], f, indent=2)
        except Exception as e:
            pass

    def _save_trade(self, trade: Dict):
        """Guarda un trade individual."""
        filename = f"{config.METRICS_DIR}/trades_{datetime.now().strftime('%Y%m%d')}.json"
        try:
            existing = []
            if os.path.exists(filename):
                with open(filename, 'r') as f:
                    existing = json.load(f)
            existing.append(trade)
            with open(filename, 'w') as f:
                json.dump(existing[-500:], f, indent=2)
        except Exception as e:
            pass

    def save_final_report(self):
        """Guarda el reporte final agregado."""
        metrics = self.get_aggregated_metrics()
        filename = f"{config.METRICS_DIR}/report_final_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w') as f:
            json.dump(metrics, f, indent=2)

    def get_cycle_summary(self) -> Dict:
        """Resumen del último ciclo."""
        if not self._cycle_window:
            return {}
        last = self._cycle_window[-1]
        return {
            'timestamp': last.get('timestamp'),
            'symbols_scanned': last.get('symbols_scanned', 0),
            'best_score': last.get('best_score', 0),
            'trade_executed': last.get('trade_executed', False),
            'latency_ms': last.get('latency_ms', 0),
            'mode': last.get('mode', 'UNKNOWN'),
            'dd_actual': last.get('dd_actual', 0),
        }