# risk.py
# ============================================================
# CONTROL DE DRAWDOWN — 4 MODOS + KILL SWITCH
# ============================================================
# NORMAL (<8%) → REDUCIDO (8-12%) → PROTECCIÓN (12-15%) → KILL (≥15%)
# ============================================================

import json
import os
import time
from typing import Dict

import config


class RiskController:
    def __init__(self, capital_inicial: float = None):
        self.capital_inicial = capital_inicial or config.CAPITAL_INICIAL
        self.equity_peak = self.capital_inicial
        self.equity_current = self.capital_inicial
        self.dd_actual = 0.0
        self.dd_max_historico = 0.0
        self.mode = "NORMAL"
        self.kill_switch_activated = False
        self.kill_reason = ""
        self._history = []
        self._last_update = time.time()

        os.makedirs(config.SNAPSHOTS_DIR, exist_ok=True)

    # ============================================================
    # ACTUALIZACIÓN
    # ============================================================

    def update(self, equity_current: float) -> Dict:
        """Actualiza el estado y retorna métricas."""
        self.equity_current = equity_current

        # Actualizar peak
        if equity_current > self.equity_peak:
            self.equity_peak = equity_current

        # Calcular drawdown (%)
        if self.equity_peak > 0:
            self.dd_actual = ((self.equity_peak - self.equity_current) / self.equity_peak) * 100
        else:
            self.dd_actual = 0.0
        self.dd_actual = max(0.0, self.dd_actual)

        # Actualizar DD máximo histórico
        if self.dd_actual > self.dd_max_historico:
            self.dd_max_historico = self.dd_actual

        # Historial
        self._history.append(self.dd_actual)
        if len(self._history) > 100:
            self._history.pop(0)

        # Determinar modo
        self._determine_mode()

        # Verificar kill switch
        if config.KILL_SWITCH_ENABLED and self.dd_actual >= config.KILL_THRESHOLD:
            self._activate_kill_switch(
                f"Drawdown {self.dd_actual:.2f}% ≥ {config.KILL_THRESHOLD}%"
            )

        return self.get_metrics()

    # ============================================================
    # DETERMINACIÓN DE MODO
    # ============================================================

    def _determine_mode(self):
        if self.kill_switch_activated:
            self.mode = "KILL"
            return

        if self.dd_actual < config.DD_NORMAL_LIMIT:
            self.mode = "NORMAL"
        elif self.dd_actual < config.DD_REDUCED_LIMIT:
            self.mode = "REDUCIDO"
        else:
            self.mode = "PROTECCIÓN"

    # ============================================================
    # KILL SWITCH
    # ============================================================

    def _activate_kill_switch(self, reason: str):
        if self.kill_switch_activated:
            return
        self.kill_switch_activated = True
        self.kill_reason = reason
        self.mode = "KILL"
        self._save_snapshot(final=True)

    def is_kill_switch_activated(self) -> bool:
        return self.kill_switch_activated

    def get_kill_reason(self) -> str:
        return self.kill_reason

    # ============================================================
    # PARÁMETROS EFECTIVOS
    # ============================================================

    def get_effective_parameters(self) -> Dict:
        """Retorna leverage, size_factor y trading_enabled según modo."""
        if self.mode == "NORMAL":
            return {
                'leverage': config.LEVERAGE_NORMAL,
                'size_factor': config.SIZE_FACTOR_NORMAL,
                'mode': 'NORMAL',
                'trading_enabled': True,
                'min_score_boost': 0.0,
            }
        elif self.mode == "REDUCIDO":
            return {
                'leverage': config.LEVERAGE_REDUCED,
                'size_factor': config.SIZE_FACTOR_REDUCED,
                'mode': 'REDUCIDO',
                'trading_enabled': True,
                'min_score_boost': 0.05,
            }
        elif self.mode == "PROTECCIÓN":
            return {
                'leverage': config.LEVERAGE_PROTECTION,
                'size_factor': config.SIZE_FACTOR_PROTECTION,
                'mode': 'PROTECCIÓN',
                'trading_enabled': True,
                'min_score_boost': 0.15,
            }
        else:  # KILL
            return {
                'leverage': 0,
                'size_factor': 0.0,
                'mode': 'KILL',
                'trading_enabled': False,
                'min_score_boost': 1.0,  # Imposible de alcanzar
            }

    # ============================================================
    # MÉTRICAS
    # ============================================================

    def get_metrics(self) -> Dict:
        params = self.get_effective_parameters()
        return {
            'dd_actual': round(self.dd_actual, 2),
            'dd_max_historico': round(self.dd_max_historico, 2),
            'mode': self.mode,
            'leverage_effective': params['leverage'],
            'size_factor': round(params['size_factor'], 3),
            'trading_enabled': params['trading_enabled'],
            'min_score_boost': round(params.get('min_score_boost', 0.0), 2),
            'equity_peak': round(self.equity_peak, 2),
            'equity_current': round(self.equity_current, 2),
            'kill_switch_activated': self.kill_switch_activated,
            'kill_reason': self.kill_reason,
            'distance_to_kill': round(config.KILL_THRESHOLD - self.dd_actual, 2),
        }

    def _save_snapshot(self, final: bool = False):
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        suffix = "FINAL" if final else "STATE"
        filename = f"{config.SNAPSHOTS_DIR}/snapshot_{suffix}_{timestamp}.json"

        snapshot = {
            'timestamp': timestamp,
            'mode': self.mode,
            'dd_actual': self.dd_actual,
            'dd_max_historico': self.dd_max_historico,
            'equity_peak': self.equity_peak,
            'equity_current': self.equity_current,
            'kill_switch_activated': self.kill_switch_activated,
            'kill_reason': self.kill_reason,
            'dd_history': self._history[-50:],
        }
        with open(filename, 'w') as f:
            json.dump(snapshot, f, indent=2)

    def reset(self, new_capital: float = None):
        if new_capital is not None:
            self.capital_inicial = new_capital
            self.equity_peak = new_capital
            self.equity_current = new_capital
        else:
            self.equity_peak = self.equity_current
        self.dd_actual = 0.0
        self.dd_max_historico = 0.0
        self.mode = "NORMAL"
        self.kill_switch_activated = False
        self.kill_reason = ""
        self._history = []
