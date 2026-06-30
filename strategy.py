# strategy.py
# ============================================================
# LÓGICA DE TRADING — SCORING SIMPLIFICADO
# ============================================================
# Features: EMA fast/slow, ATR, ADX aproximado, momentum
# Score: trend (40%) + strength (35%) + momentum (25%)
# ============================================================

import time
from typing import Dict, Optional, List, Tuple

import config


class Strategy:
    def __init__(self):
        self.cooldown = {}  # symbol -> timestamp de expiración

    # ============================================================
    # INDICADORES
    # ============================================================

    def compute_ema(self, prices: List[float], period: int) -> List[float]:
        """Exponential Moving Average."""
        if len(prices) < period:
            return prices
        ema = [prices[0]]
        multiplier = 2 / (period + 1)
        for price in prices[1:]:
            ema.append(price * multiplier + ema[-1] * (1 - multiplier))
        return ema

    def compute_atr(self, highs: List[float], lows: List[float], closes: List[float],
                    period: int = 14) -> float:
        """Average True Range (último valor)."""
        if len(closes) < period + 1:
            return 0.01
        trs = []
        for i in range(1, len(closes)):
            tr1 = highs[i] - lows[i]
            tr2 = abs(highs[i] - closes[i-1])
            tr3 = abs(lows[i] - closes[i-1])
            trs.append(max(tr1, tr2, tr3))
        if len(trs) < period:
            return 0.01
        return sum(trs[-period:]) / period

    def compute_adx(self, highs: List[float], lows: List[float], closes: List[float],
                    period: int = 14) -> float:
        """ADX simplificado (aproximación)."""
        if len(closes) < period * 2:
            return 20.0

        atr = self.compute_atr(highs, lows, closes, period)
        if atr == 0:
            return 20.0

        # +DM y -DM
        up = [max(0, highs[i] - highs[i-1]) for i in range(1, len(highs))]
        down = [max(0, lows[i-1] - lows[i]) for i in range(1, len(lows))]
        plus_dm = sum(up[-period:]) / period if len(up) >= period else 0
        minus_dm = sum(down[-period:]) / period if len(down) >= period else 0

        plus_di = plus_dm / atr * 100 if atr > 0 else 0
        minus_di = minus_dm / atr * 100 if atr > 0 else 0
        dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
        return dx  # ADX ≈ DX (simplificado)

    def compute_momentum(self, closes: List[float], period: int = 5) -> float:
        if len(closes) < period + 1:
            return 0.0
        return (closes[-1] / closes[-period-1] - 1) * 100 if closes[-period-1] != 0 else 0.0

    # ============================================================
    # FEATURES
    # ============================================================

    def compute_features(self, candles: dict) -> Optional[Dict]:
        """Extrae features de los datos de velas."""
        if not candles or len(candles['c']) < 50:
            return None

        closes = candles['c']
        highs = candles['h']
        lows = candles['l']

        ema_fast = self.compute_ema(closes, config.EMA_FAST)
        ema_slow = self.compute_ema(closes, config.EMA_SLOW)
        atr = self.compute_atr(highs, lows, closes, config.ATR_PERIOD)
        adx = self.compute_adx(highs, lows, closes, config.ADX_PERIOD)
        momentum = self.compute_momentum(closes, config.MOMENTUM_PERIOD)

        return {
            'close': closes[-1],
            'ema_fast': ema_fast[-1] if ema_fast else closes[-1],
            'ema_slow': ema_slow[-1] if ema_slow else closes[-1],
            'atr': atr,
            'atr_pct': atr / closes[-1] * 100 if closes[-1] != 0 else 0,
            'adx': adx,
            'momentum': momentum,
            'trend_direction': 1 if (ema_fast and ema_slow and ema_fast[-1] > ema_slow[-1]) else -1 if (ema_fast and ema_slow) else 0,
        }

    # ============================================================
    # SCORING
    # ============================================================

    def compute_score(self, features: Dict) -> float:
        """
        Score combinado (0-1):
        - Trend (EMA cruz): 40%
        - Strength (ADX): 35%
        - Momentum: 25%
        """
        if not features:
            return 0.0

        # 1. Trend: 1 si EMA_fast > EMA_slow
        trend = 1 if features.get('ema_fast', 0) > features.get('ema_slow', 0) else 0

        # 2. Strength: ADX normalizado (0-1)
        adx = features.get('adx', 0)
        adx_score = min(1.0, adx / 40.0)

        # 3. Momentum: absoluto normalizado
        momentum = abs(features.get('momentum', 0))
        mom_score = min(1.0, momentum / 5.0)

        # Score ponderado
        score = trend * 0.40 + adx_score * 0.35 + mom_score * 0.25

        # Penalizaciones
        if adx < 20:
            score *= 0.5
        if abs(momentum) < 0.5:
            score *= 0.7

        return min(1.0, max(0.0, score))

    # ============================================================
    # SELECCIÓN DE ACTIVOS
    # ============================================================

    def select_top_asset(self, features_dict: Dict[str, Dict]) -> Optional[Tuple[str, float, Dict]]:
        """
        Selecciona el activo con mayor score, aplicando cooldown y umbral mínimo.
        """
        now = time.time()
        best_symbol = None
        best_score = 0.0
        best_features = None

        for symbol, features in features_dict.items():
            if features is None:
                continue

            # Cooldown
            if symbol in self.cooldown and self.cooldown[symbol] > now:
                continue

            score = self.compute_score(features)
            if score < config.MIN_SCORE:
                continue

            if score > best_score:
                best_score = score
                best_symbol = symbol
                best_features = features

        if best_symbol is None:
            return None

        return (best_symbol, best_score, best_features)

    def set_cooldown(self, symbol: str, duration: int = None):
        if duration is None:
            duration = config.COOLDOWN_SECONDS
        self.cooldown[symbol] = time.time() + duration

    def clear_cooldown(self, symbol: str):
        self.cooldown.pop(symbol, None)

    def is_on_cooldown(self, symbol: str) -> bool:
        now = time.time()
        return symbol in self.cooldown and self.cooldown[symbol] > now
