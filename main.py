#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
KRISHNA KILLING SPREE — MAIN.PY (CONTINUO)
Versión con loop infinito para GitHub Actions (reinicio cada 5h).
"""

import os
import sys
import time
import json
import csv
import traceback
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple, Union
from collections import deque, defaultdict

from exchange import Exchange, safe_float
from strategy import Strategy
from risk import RiskController
from utils import log_info, log_warning, log_error, log_debug, log_success

# ============================================================
# CONFIGURACIÓN
# ============================================================
SYMBOLS = [
    "BTC-USDT-SWAP",
    "ETH-USDT-SWAP",
    "SOL-USDT-SWAP",
    "ADA-USDT-SWAP",
    "XRP-USDT-SWAP",
    "AVAX-USDT-SWAP",
]

CAPITAL_INICIAL = 100.0
BASE_LEVERAGE = 7
MIN_SCORE = 0.45
TP_MULT = 1.2
SL_MULT = 1.0
COOLDOWN_SECONDS = 15 * 60

METRICS_DIR = "metrics"
LOGS_DIR = "logs"
SNAPSHOTS_DIR = "snapshots"

# ============================================================
# TRACE ENGINE (AUDITORÍA)
# ============================================================
class TradeTrace:
    """Sistema de auditoría paso a paso para cada trade."""

    STEPS = [
        "SYMBOL_SELECTED",
        "MARKET_DATA_LOADED",
        "SIGNAL_GENERATED",
        "SIGNAL_VALIDATION",
        "RISK_CHECK",
        "ORDER_BUILT",
        "EXCHANGE_VALIDATION",
        "ORDER_SENT",
        "OKX_RESPONSE"
    ]

    def __init__(self):
        self.reset()

    def reset(self):
        self.steps = {}
        self.fail_reason = None
        self.fail_step = None
        self.success = False

    def log_step(self, step: str, data: Any) -> None:
        if step not in self.STEPS:
            return
        self.steps[step] = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'data': data
        }
        log_debug(f"[TRACE] {step}: {str(data)[:200]}")

    def log_fail(self, step: str, reason: str) -> None:
        self.fail_step = step
        self.fail_reason = reason
        self.success = False
        log_warning(f"[TRACE] ❌ FALLÓ en {step}: {reason}")

    def log_success(self, step: str, data: Any) -> None:
        self.steps[step] = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'data': data
        }
        self.success = True
        log_debug(f"[TRACE] ✅ {step}: {str(data)[:200]}")

    def get_summary(self) -> Dict:
        return {
            'success': self.success,
            'fail_step': self.fail_step,
            'fail_reason': self.fail_reason,
            'steps_completed': list(self.steps.keys())
        }

    def diagnose(self) -> str:
        if self.success:
            return "OK"
        if self.fail_step is None:
            return "UNKNOWN (no steps logged)"

        mapping = {
            "SYMBOL_SELECTED": "STRATEGY_ISSUE: No se seleccionó ningún símbolo",
            "MARKET_DATA_LOADED": "DATA_ISSUE: No se pudieron cargar datos de mercado",
            "SIGNAL_GENERATED": "STRATEGY_ISSUE: No se generó señal válida",
            "SIGNAL_VALIDATION": "FILTER_ISSUE: La señal fue bloqueada por filtros internos",
            "RISK_CHECK": "RISK_ISSUE: El control de riesgo bloqueó la operación",
            "ORDER_BUILT": "VALIDATION_ISSUE: Error en la construcción de la orden",
            "EXCHANGE_VALIDATION": "EXCHANGE_ISSUE: Validación previa a OKX falló",
            "ORDER_SENT": "EXCHANGE_ISSUE: OKX rechazó la orden",
            "OKX_RESPONSE": "EXCHANGE_ISSUE: Respuesta de OKX con error"
        }
        return mapping.get(self.fail_step, f"UNKNOWN (step: {self.fail_step})")

    def to_dict(self) -> Dict:
        return {
            'success': self.success,
            'fail_step': self.fail_step,
            'fail_reason': self.fail_reason,
            'steps_completed': list(self.steps.keys()),
            'diagnosis': self.diagnose()
        }

# ============================================================
# BOT PRINCIPAL
# ============================================================
class KrishnaKillingSpree:
    def __init__(self, api_key: str, secret_key: str, passphrase: str, demo: bool = True):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.demo = demo

        self.exchange = Exchange(api_key, secret_key, passphrase, demo)
        self.strategy = Strategy()
        self.risk = None

        self.capital = CAPITAL_INICIAL
        self.last_equity = self.capital
        self.pnl_total = 0.0
        self.trades_count = 0
        self.position = None
        self.instrument_info = {}
        self._last_mode = "NORMAL"

        self.stats = {
            'symbols_processed': 0,
            'signals_generated': 0,
            'orders_attempted': 0,
            'orders_sent': 0,
            'okx_rejections': 0,
            'blocked_by_strategy': 0,
            'blocked_by_validator': 0,
            'blocked_by_risk': 0,
            'blocked_by_cooldown': 0,
            'invalid_symbols': 0,
            'traces': []
        }

        self.valid_instruments = {}

    # ============================================================
    # INICIALIZACIÓN
    # ============================================================
    def init(self) -> bool:
        log_info("🔥 KRISHNA KILLING SPREE — INICIO")
        log_info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")

        if not self.exchange.connect():
            log_error("Fallo en la conexión con OKX.")
            return False
        log_info("Conexión OKX establecida.")

        bal = self.exchange.get_balance()
        log_debug(f"Balance response: {bal}")

        if bal.get('ok'):
            data = bal.get('data', [])
            found = False
            for detail in data:
                for asset in detail.get('details', []):
                    if asset.get('ccy') == 'USDT':
                        self.capital = safe_float(asset.get('eq'))
                        self.last_equity = self.capital
                        log_info(f"✅ Capital disponible (equity): {self.capital:.2f} USDT")
                        found = True
                        break
                if found:
                    break
            if not found and 'USDT' in bal:
                self.capital = safe_float(bal['USDT'].get('equity'))
                self.last_equity = self.capital
                log_info(f"✅ Capital disponible (equity alternativo): {self.capital:.2f} USDT")
                found = True
            if not found:
                log_warning("No se encontró USDT en la respuesta de balance.")
                self.capital = CAPITAL_INICIAL
                self.last_equity = self.capital
        else:
            log_error(f"Error al obtener balance: {bal.get('error')}")
            self.capital = CAPITAL_INICIAL
            self.last_equity = self.capital

        if self.capital == CAPITAL_INICIAL:
            log_warning(f"⚠️ Usando capital inicial por defecto: {CAPITAL_INICIAL:.2f} USDT")

        log_info("Obteniendo información de instrumentos...")
        for sym in SYMBOLS:
            try:
                info = self.exchange.get_instrument_info(sym)
                if info and info.get('lot_size', 0) > 0:
                    self.instrument_info[sym] = info
                    self.valid_instruments[sym] = True
                    log_debug(f"✅ {sym}: lotSize={info.get('lot_size')}, minSz={info.get('min_sz')}")
                else:
                    self.valid_instruments[sym] = False
                    log_warning(f"❌ {sym}: INSTRUMENTO INVÁLIDO o sin información")
                    self.stats['invalid_symbols'] += 1
            except Exception as e:
                self.valid_instruments[sym] = False
                log_error(f"Error obteniendo info de {sym}: {e}")
                self.stats['invalid_symbols'] += 1

        self.risk = RiskController(self.capital)
        log_info(f"Universo: {len(SYMBOLS)} activos (válidos: {sum(1 for v in self.valid_instruments.values() if v)})")
        log_info(f"Apalancamiento base: {BASE_LEVERAGE}x")
        return True

    def validate_symbol(self, symbol: str) -> bool:
        return self.valid_instruments.get(symbol, False)

    # ============================================================
    # PROCESAMIENTO DE SÍMBOLO (RESILIENTE)
    # ============================================================
    def process_symbol(self, symbol: str) -> Dict:
        trace = TradeTrace()
        result = {
            'symbol': symbol,
            'executed': False,
            'trace': trace,
            'reason': None
        }

        try:
            trace.log_step("SYMBOL_SELECTED", {"symbol": symbol})
            self.stats['symbols_processed'] += 1

            if not self.validate_symbol(symbol):
                trace.log_fail("SYMBOL_SELECTED", f"Símbolo {symbol} inválido o no encontrado")
                self.stats['blocked_by_validator'] += 1
                result['reason'] = 'INVALID_SYMBOL'
                return result

            if self.strategy.is_on_cooldown(symbol):
                trace.log_fail("SYMBOL_SELECTED", f"Símbolo {symbol} en cooldown")
                self.stats['blocked_by_cooldown'] += 1
                result['reason'] = 'COOLDOWN'
                return result

            # MARKET_DATA_LOADED
            try:
                candles = self.exchange._request("GET", "/api/v5/market/candles",
                                                 params={"instId": symbol, "bar": "5m", "limit": 100})
                if not candles.get('ok') or not candles.get('data'):
                    trace.log_fail("MARKET_DATA_LOADED", "No se pudieron obtener velas")
                    self.stats['blocked_by_strategy'] += 1
                    result['reason'] = 'NO_DATA'
                    return result

                candles_data = candles['data']
                if len(candles_data) < 50:
                    trace.log_fail("MARKET_DATA_LOADED", f"Solo {len(candles_data)} velas (mínimo 50)")
                    self.stats['blocked_by_strategy'] += 1
                    result['reason'] = 'INSUFFICIENT_DATA'
                    return result

                candle_dict = {
                    'ts': [c[0] for c in candles_data],
                    'o': [float(c[1]) for c in candles_data],
                    'h': [float(c[2]) for c in candles_data],
                    'l': [float(c[3]) for c in candles_data],
                    'c': [float(c[4]) for c in candles_data],
                    'v': [float(c[5]) for c in candles_data],
                }
                trace.log_step("MARKET_DATA_LOADED", {"count": len(candles_data), "last_close": candle_dict['c'][-1]})
            except Exception as e:
                trace.log_fail("MARKET_DATA_LOADED", str(e))
                self.stats['blocked_by_strategy'] += 1
                result['reason'] = 'DATA_FETCH_ERROR'
                return result

            # SIGNAL_GENERATED
            try:
                features = self.strategy.compute_features(candle_dict)
                if not features:
                    trace.log_fail("SIGNAL_GENERATED", "No se pudieron calcular features")
                    self.stats['blocked_by_strategy'] += 1
                    result['reason'] = 'FEATURES_ERROR'
                    return result

                score = self.strategy.compute_score(features)
                trace.log_step("SIGNAL_GENERATED", {"score": score, "direction": features.get('trend_direction')})
                self.stats['signals_generated'] += 1

                if score < MIN_SCORE:
                    trace.log_fail("SIGNAL_VALIDATION", f"Score {score:.3f} < {MIN_SCORE}")
                    self.stats['blocked_by_strategy'] += 1
                    result['reason'] = 'LOW_SCORE'
                    return result

                trace.log_success("SIGNAL_VALIDATION", f"Score {score:.3f} > {MIN_SCORE}")

                # RISK_CHECK
                params = self.risk.get_effective_parameters()
                if not params['trading_enabled']:
                    trace.log_fail("RISK_CHECK", "Trading deshabilitado por modo de riesgo")
                    self.stats['blocked_by_risk'] += 1
                    result['reason'] = 'RISK_DISABLED'
                    return result

                if score < MIN_SCORE + params.get('min_score_boost', 0):
                    trace.log_fail("RISK_CHECK", f"Score {score:.3f} < {MIN_SCORE + params.get('min_score_boost', 0):.3f} (con boost)")
                    self.stats['blocked_by_risk'] += 1
                    result['reason'] = 'RISK_BOOST'
                    return result

                trace.log_success("RISK_CHECK", {"mode": self.risk.mode, "leverage": params['leverage']})

                # ORDER_BUILT
                try:
                    ticker = self.exchange._request("GET", "/api/v5/market/ticker", params={"instId": symbol})
                    if not ticker.get('ok') or not ticker.get('data'):
                        trace.log_fail("ORDER_BUILT", "No se pudo obtener ticker")
                        self.stats['blocked_by_validator'] += 1
                        result['reason'] = 'NO_TICKER'
                        return result

                    entry = safe_float(ticker['data'][0].get('last'))
                    if entry <= 0:
                        trace.log_fail("ORDER_BUILT", f"Precio inválido: {entry}")
                        self.stats['blocked_by_validator'] += 1
                        result['reason'] = 'INVALID_PRICE'
                        return result

                    direction = features.get('trend_direction', 1)
                    side = 'buy' if direction == 1 else 'sell'
                    pos_side = "long" if side == 'buy' else "short"

                    info = self.instrument_info.get(symbol, {})
                    ct_val = info.get('ct_val', 0.01)
                    lot_sz = info.get('lot_size', 0.001)
                    min_sz = info.get('min_sz', 0.001)

                    capital_factor = 0.85
                    available = self.capital * capital_factor
                    desired_notional = available * params['leverage'] * params['size_factor']

                    size = desired_notional / (entry * ct_val)
                    size = max(min_sz, round(size / lot_sz) * lot_sz)

                    # Verificar margen
                    estimated_margin = (entry * size * ct_val) / params['leverage']
                    required_margin = estimated_margin * 1.1

                    if required_margin > self.capital:
                        trace.log_fail("ORDER_BUILT", f"Margen insuficiente: {required_margin:.2f} USDT > {self.capital:.2f} USDT")
                        self.stats['blocked_by_validator'] += 1
                        result['reason'] = 'INSUFFICIENT_MARGIN'
                        return result

                    if size <= 0:
                        trace.log_fail("ORDER_BUILT", f"Tamaño inválido: {size}")
                        self.stats['blocked_by_validator'] += 1
                        result['reason'] = 'INVALID_SIZE'
                        return result

                    atr = features.get('atr', entry * 0.01)
                    tp_base = entry + atr * TP_MULT if side == 'buy' else entry - atr * TP_MULT
                    sl_base = entry - atr * SL_MULT if side == 'buy' else entry + atr * SL_MULT

                    tick_size = info.get('tick_size', 0.01)
                    tp_price = round(tp_base / tick_size) * tick_size
                    sl_price = round(sl_base / tick_size) * tick_size

                    min_distance = entry * 0.01
                    if side == 'buy':
                        if tp_price <= entry + min_distance:
                            tp_price = entry + min_distance * 2
                        if sl_price >= entry - min_distance:
                            sl_price = entry - min_distance * 2
                    else:
                        if tp_price >= entry - min_distance:
                            tp_price = entry - min_distance * 2
                        if sl_price <= entry + min_distance:
                            sl_price = entry + min_distance * 2

                    trace.log_step("ORDER_BUILT", {
                        'entry': entry,
                        'size': size,
                        'side': side,
                        'tp': tp_price,
                        'sl': sl_price,
                        'leverage': params['leverage'],
                        'estimated_margin': required_margin
                    })
                    self.stats['orders_attempted'] += 1

                    # EXCHANGE_VALIDATION
                    inst_info = self.exchange.get_instrument_info(symbol)
                    if not inst_info or inst_info.get('lot_size', 0) <= 0:
                        trace.log_fail("EXCHANGE_VALIDATION", "Instrumento no válido en OKX")
                        self.stats['blocked_by_validator'] += 1
                        result['reason'] = 'INSTRUMENT_INVALID'
                        return result

                    if size < inst_info.get('min_sz', 0):
                        trace.log_fail("EXCHANGE_VALIDATION", f"Size {size} < min_sz {inst_info.get('min_sz')}")
                        self.stats['blocked_by_validator'] += 1
                        result['reason'] = 'SIZE_TOO_SMALL'
                        return result

                    trace.log_success("EXCHANGE_VALIDATION", "Validación OK")

                    # ORDER_SENT
                    log_info(f"📈 TRADE: {symbol} | {side.upper()} | Entry: {entry:.2f} | Size: {size:.4f} | TP: {tp_price:.2f} | SL: {sl_price:.2f}")

                    order_res = self.exchange.place_market_order_with_tp_sl(
                        symbol, side, size, tp_price, sl_price
                    )

                    trace.log_step("ORDER_SENT", {"response": order_res})
                    self.stats['orders_sent'] += 1

                    # OKX_RESPONSE
                    if not order_res.get('ok'):
                        error_msg = order_res.get('error', 'Unknown error')
                        raw = order_res.get('raw')
                        if raw:
                            sMsg = ''
                            if 'data' in raw and raw['data']:
                                sMsg = raw['data'][0].get('sMsg', '')
                            if sMsg:
                                error_msg = f"{error_msg} | sMsg: {sMsg}"
                            log_error(f"OKX Raw: {json.dumps(raw, indent=2)}")
                        trace.log_fail("OKX_RESPONSE", f"OKX error: {error_msg}")
                        self.stats['okx_rejections'] += 1
                        result['reason'] = f'OKX_REJECTED: {error_msg}'
                        return result

                    trace.log_success("OKX_RESPONSE", {"ordId": order_res.get('data', [{}])[0].get('ordId')})
                    self.trades_count += 1
                    self.position = {'symbol': symbol, 'side': side}
                    result['executed'] = True
                    log_success(f"✅ Trade ejecutado en {symbol}")
                    return result

                except Exception as e:
                    trace.log_fail("ORDER_BUILT", str(e))
                    self.stats['blocked_by_validator'] += 1
                    result['reason'] = 'ORDER_BUILD_ERROR'
                    log_error(f"Error en ORDER_BUILT: {e}")
                    traceback.print_exc()
                    return result

            except Exception as e:
                trace.log_fail("SIGNAL_GENERATED", str(e))
                self.stats['blocked_by_strategy'] += 1
                result['reason'] = 'SIGNAL_ERROR'
                log_error(f"Error en SIGNAL_GENERATED: {e}")
                return result

        except Exception as e:
            trace.log_fail("SYMBOL_SELECTED", f"Error general: {e}")
            result['reason'] = 'GENERAL_ERROR'
            log_error(f"Error procesando {symbol}: {e}")
            traceback.print_exc()
            return result

        finally:
            self.stats['traces'].append(trace)
            result['trace'] = trace
            return result

    # ============================================================
    # CLEANUP
    # ============================================================
    def _cleanup(self) -> None:
        log_debug("[CLEANUP] Reconciliación de estado")
        try:
            positions = self.exchange.get_positions()
            pos_data = positions.get('data', []) if positions.get('ok') else []
            if pos_data:
                log_info(f"Posiciones encontradas: {len(pos_data)}")

            pos_symbols = {p.get('instId') for p in pos_data if safe_float(p.get('pos', 0)) > 0}

            pending = self.exchange._request("GET", "/api/v5/trade/orders-pending")
            if pending.get('ok'):
                for order in pending.get('data', []):
                    if order.get('instId') not in pos_symbols:
                        self.exchange.cancel_order(order.get('ordId'), order.get('instId'))
                        log_debug(f"Orden huérfana cancelada: {order.get('ordId')}")

            algo = self.exchange.get_all_pending_algo_orders()
            if algo.get('ok'):
                for order in algo.get('data', []):
                    if order.get('instId') not in pos_symbols:
                        self.exchange.cancel_algo_order(order.get('algoId'), order.get('instId'))
                        log_debug(f"Orden algorítmica huérfana cancelada: {order.get('algoId')}")

        except Exception as e:
            log_error(f"Error en cleanup: {e}")

    # ============================================================
    # PNL Y MÉTRICAS
    # ============================================================
    def _append_pnl_row(self, equity: float, pnl_total: float, pnl_ejecucion: float,
                        trades: int, modo: str) -> None:
        os.makedirs(METRICS_DIR, exist_ok=True)
        filename = f"{METRICS_DIR}/pnl_history.csv"
        file_exists = os.path.exists(filename)
        with open(filename, 'a', newline='') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['fecha', 'hora', 'equity', 'pnl_acumulado', 'pnl_ejecucion', 'trades', 'modo_riesgo'])
            now = datetime.now(timezone.utc)
            writer.writerow([
                now.strftime('%Y-%m-%d'),
                now.strftime('%H:%M:%S'),
                round(equity, 2),
                round(pnl_total, 2),
                round(pnl_ejecucion, 2),
                trades,
                modo
            ])

    def _save_metrics(self) -> None:
        os.makedirs(METRICS_DIR, exist_ok=True)
        filename = f"{METRICS_DIR}/report_final_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"

        traces_serializable = [trace.to_dict() for trace in self.stats['traces']]

        stats_serializable = {
            'symbols_processed': self.stats['symbols_processed'],
            'signals_generated': self.stats['signals_generated'],
            'orders_attempted': self.stats['orders_attempted'],
            'orders_sent': self.stats['orders_sent'],
            'okx_rejections': self.stats['okx_rejections'],
            'blocked_by_strategy': self.stats['blocked_by_strategy'],
            'blocked_by_validator': self.stats['blocked_by_validator'],
            'blocked_by_risk': self.stats['blocked_by_risk'],
            'blocked_by_cooldown': self.stats['blocked_by_cooldown'],
            'invalid_symbols': self.stats['invalid_symbols'],
            'traces': traces_serializable
        }

        with open(filename, 'w') as f:
            json.dump({
                'trades_count': self.trades_count,
                'pnl_total': self.pnl_total,
                'capital': self.capital,
                'stats': stats_serializable
            }, f, indent=2, default=str)

    # ============================================================
    # DIAGNÓSTICO
    # ============================================================
    def _diagnose_no_trades(self) -> None:
        log_info("=" * 60)
        log_info("🔍 DIAGNÓSTICO: No se ejecutaron trades")
        log_info("=" * 60)

        reasons = defaultdict(int)
        for trace in self.stats['traces']:
            if not trace.success:
                diag = trace.diagnose()
                reasons[diag] += 1

        log_info("Causas detectadas:")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            log_info(f"  {count}x → {reason}")

        log_info("")
        log_info("Estadísticas detalladas:")
        log_info(f"  Símbolos procesados: {self.stats['symbols_processed']}")
        log_info(f"  Señales generadas: {self.stats['signals_generated']}")
        log_info(f"  Órdenes intentadas: {self.stats['orders_attempted']}")
        log_info(f"  Órdenes enviadas: {self.stats['orders_sent']}")
        log_info(f"  Rechazos OKX: {self.stats['okx_rejections']}")
        log_info(f"  Bloqueados por estrategia: {self.stats['blocked_by_strategy']}")
        log_info(f"  Bloqueados por validador: {self.stats['blocked_by_validator']}")
        log_info(f"  Bloqueados por riesgo: {self.stats['blocked_by_risk']}")
        log_info(f"  Bloqueados por cooldown: {self.stats['blocked_by_cooldown']}")
        log_info(f"  Símbolos inválidos: {self.stats['invalid_symbols']}")

        log_info("")
        log_info("📌 Recomendaciones:")
        if self.stats['blocked_by_strategy'] > 0 and self.stats['signals_generated'] == 0:
            log_info("  • El scoring no genera señales. Revisa MIN_SCORE o los indicadores.")
        if self.stats['blocked_by_validator'] > 0:
            log_info("  • La validación previa bloqueó órdenes. Verifica símbolos y tamaños.")
        if self.stats['okx_rejections'] > 0:
            log_info("  • OKX rechazó órdenes. Revisa los logs de error para más detalles.")
        if self.stats['invalid_symbols'] > 0:
            log_info("  • Algunos símbolos son inválidos. Verifica SYMBOLS en config.py.")
        log_info("=" * 60)

    def _print_summary(self) -> None:
        log_info("=" * 60)
        log_info("📊 RESUMEN DEL CICLO")
        log_info("=" * 60)
        log_info(f"  Capital actual (equity): {self.capital:.2f} USDT")
        log_info(f"  Trades ejecutados: {self.trades_count}")
        log_info(f"  PnL total: {self.pnl_total:.2f} USDT")
        log_info(f"  Modo riesgo: {self.risk.mode}")
        log_info(f"  Drawdown: {self.risk.dd_actual:.2f}%")
        log_info("=" * 60)

    # ============================================================
    # 🆕 ACTUALIZACIÓN PNL TRAS CIERRE
    # ============================================================
    def _update_pnl_after_close(self) -> None:
        """Actualiza PnL cuando una posición se cierra."""
        bal = self.exchange.get_balance()
        if bal.get('ok'):
            data = bal.get('data', [])
            for detail in data:
                for asset in detail.get('details', []):
                    if asset.get('ccy') == 'USDT':
                        equity = safe_float(asset.get('eq'))
                        pnl_ejecucion = equity - self.last_equity
                        if abs(pnl_ejecucion) > 0.01:
                            self.pnl_total += pnl_ejecucion
                            self.last_equity = equity
                            self.capital = equity
                            self._append_pnl_row(equity, self.pnl_total, pnl_ejecucion, self.trades_count, self.risk.mode)
                            log_info(f"📈 PnL del trade: {pnl_ejecucion:.2f} USDT | PnL total: {self.pnl_total:.2f} USDT")
                        break

    # ============================================================
    # 🆕 BUCLE INFINITO (PARA GITHUB ACTIONS)
    # ============================================================
    def run(self) -> Dict:
        """Bucle principal INFINITO — para GitHub Actions (reinicio cada 5h)."""
        log_info("🔥 KRISHNA KILLING SPREE — INICIO (MODO CONTINUO)")

        if not self.init():
            log_error("Fallo en la inicialización. Saliendo.")
            return {'success': False, 'error': 'init_failed'}

        self._cleanup()

        if self.risk.is_kill_switch_activated():
            log_error("Kill switch activado al inicio. Saliendo.")
            return {'success': False, 'error': 'kill_switch'}

        log_info("🔄 Bucle principal iniciado. Esperando oportunidades...")

        position_open = False
        last_position_check = 0
        trade_count = 0

        while True:
            try:
                # 1. Verificar posiciones activas en OKX
                positions = self.exchange.get_positions()
                pos_data = positions.get('data', []) if positions.get('ok') else []
                active_positions = [p for p in pos_data if safe_float(p.get('pos', 0)) > 0]

                if active_positions:
                    if not position_open:
                        log_info(f"📊 Posición activa: {active_positions[0].get('instId')}")
                        position_open = True

                    # Mostrar PnL cada 30 segundos
                    now = time.time()
                    if now - last_position_check > 30:
                        for p in active_positions:
                            pnl = safe_float(p.get('upl'))
                            log_info(f"💹 PnL: {pnl:.2f} USDT")
                        last_position_check = now

                    time.sleep(5)  # Esperar antes de volver a verificar
                    continue

                # 2. No hay posición → buscar señal
                if position_open:
                    log_info("✅ Posición cerrada. Buscando nueva oportunidad...")
                    position_open = False
                    self._update_pnl_after_close()

                time.sleep(2)

                # 3. Escanear símbolos y ejecutar máximo 1 trade
                trade_executed = False
                for symbol in SYMBOLS:
                    log_debug(f"--- Procesando {symbol} ---")
                    try:
                        result = self.process_symbol(symbol)
                        if result.get('executed'):
                            trade_count += 1
                            trade_executed = True
                            position_open = True
                            log_info(f"🚀 Trade #{trade_count} ejecutado en {symbol}")
                            break  # Solo 1 trade por ciclo
                    except Exception as e:
                        log_error(f"Error procesando {symbol}: {e}")
                        continue  # Siguiente símbolo

                if not trade_executed:
                    log_debug("No se encontraron señales válidas. Esperando...")
                    time.sleep(30)  # Esperar 30s antes de re-escanear

            except KeyboardInterrupt:
                log_info("⏹️ Interrupción manual. Cerrando...")
                break
            except Exception as e:
                log_error(f"Error en bucle principal: {e}")
                traceback.print_exc()
                time.sleep(10)  # Esperar y reintentar

        self._save_metrics()
        log_info("🔥 KRISHNA KILLING SPREE — FIN (LOOP DETENIDO)")
        return {'success': True, 'mode': self.risk.mode, 'trade_executed': False}

# ============================================================
# ENTRY POINT
# ============================================================
def main():
    API_KEY = os.environ.get('OKX_API_KEY', "2d57031a-deb4-438e-9449-6dc3e525f2fb")
    SECRET_KEY = os.environ.get('OKX_SECRET_KEY', "2CEFC57765518B204872EF804910ECEF")
    PASSPHRASE = os.environ.get('OKX_PASSPHRASE', "Waly200381!")
    DEMO = os.environ.get('OKX_DEMO', 'true').lower() == 'true'

    if not all([API_KEY, SECRET_KEY, PASSPHRASE]):
        log_error("Faltan credenciales OKX.")
        sys.exit(1)

    bot = KrishnaKillingSpree(API_KEY, SECRET_KEY, PASSPHRASE, DEMO)
    result = bot.run()
    log_info(f"Resultado: {result}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log_info("Interrupción manual")
    except Exception as e:
        log_error(f"Error inesperado: {e}")
        traceback.print_exc()
