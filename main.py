# main.py
# ============================================================
# KRISHNA KILLING SPREE — BUCLE PRINCIPAL (STATELESS)
# ============================================================
# Flujo: sync → cleanup → fetch → score → select → execute → log → exit
# ============================================================

import os
import sys
import time
from datetime import datetime
from typing import Dict, Optional

import config
from exchange import Exchange
from strategy import Strategy
from risk import RiskController
from cleanup import CleanupEngine
from metrics import MetricsCollector
from utils import (
    log_info, log_warning, log_error, log_debug,
    update_dashboard, append_pnl_row, init_pnl_file,
    acquire_lock, release_lock
)


class KrishnaKillingSpree:
    def __init__(self, api_key: str, secret_key: str, passphrase: str, demo: bool = True):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.demo = demo

        self.exchange = None
        self.strategy = None
        self.risk = None
        self.cleanup = None
        self.metrics = None

        self.capital = config.CAPITAL_INICIAL
        self.position = None
        self.instrument_info = {}
        self.trades_count = 0
        self.pnl_total = 0.0
        self.last_equity = self.capital

        # Inicializar archivo de PnL
        init_pnl_file()

    # ============================================================
    # INICIALIZACIÓN
    # ============================================================

    def init(self) -> bool:
        log_info("🔥 KRISHNA KILLING SPREE — INICIO DE EJECUCIÓN")
        log_info(f"Timestamp: {datetime.now().isoformat()}")

        self.exchange = Exchange(self.api_key, self.secret_key, self.passphrase, self.demo)

        if not self.exchange.connect():
            log_error("Fallo en la conexión con OKX.")
            return False

        log_info("Conexión con OKX establecida.")

        self.strategy = Strategy()
        self.metrics = MetricsCollector()

        bal = self.exchange.get_balance()
        if bal and 'USDT' in bal:
            self.capital = float(bal['USDT'].get('available', config.CAPITAL_INICIAL))
            self.last_equity = self.capital
            log_info(f"Capital disponible: {self.capital:.2f} USDT")

        self.risk = RiskController(self.capital)
        self.cleanup = CleanupEngine(self.exchange)

        for sym in config.SYMBOLS:
            info = self.exchange.get_instrument_info(sym)
            self.instrument_info[sym] = {
                'ct_val': info.get('ctVal', 0.01),
                'lot_sz': info.get('lotSz', 0.001),
                'min_sz': info.get('minSz', 0.001),
            }

        log_info(f"Universo: {len(config.SYMBOLS)} activos")
        log_info(f"Posiciones máximas: {config.MAX_POSITIONS}")
        log_info(f"Apalancamiento base: {config.BASE_LEVERAGE}x")

        # Dashboard inicial
        update_dashboard("INICIANDO", equity=self.capital, modo="NORMAL")

        return True

    # ============================================================
    # FASE 1: CLEANUP
    # ============================================================

    def phase_cleanup(self) -> Dict:
        log_info("[FASE 1] CLEANUP — Reconciliación de estado")
        result = self.cleanup.sync_and_cleanup()

        # Si se encontraron posiciones, registrar
        if result['positions_found'] > 0:
            log_info(f"Posición encontrada en OKX: {result['positions_found']} activas")

        if result['inconsistencies_fixed'] > 0:
            log_warning(f"Inconsistencias corregidas: {result['inconsistencies_fixed']}")

        return result

    # ============================================================
    # FASE 2: SCORING
    # ============================================================

    def phase_scoring(self) -> Dict:
        log_debug("[FASE 2] SCORING — Análisis de mercado")
        start = time.time()

        features_dict = {}
        for sym in config.SYMBOLS:
            try:
                candles = self.exchange.fetch_historical_candles(sym, limit=100)
                if candles:
                    feat = self.strategy.compute_features(candles)
                    if feat:
                        features_dict[sym] = feat
            except Exception as e:
                log_debug(f"Error fetching {sym}: {e}")

        result = self.strategy.select_top_asset(features_dict)

        elapsed = (time.time() - start) * 1000

        if result:
            symbol, score, features = result
            log_debug(f"Mejor activo: {symbol} (score: {score:.3f})")
            return {
                'symbol': symbol,
                'score': score,
                'features': features,
                'features_dict': features_dict,
                'latency_ms': elapsed,
                'symbols_scanned': len(features_dict),
            }
        else:
            log_debug("No se encontraron señales válidas")
            return {
                'symbol': None,
                'score': 0.0,
                'features': None,
                'features_dict': features_dict,
                'latency_ms': elapsed,
                'symbols_scanned': len(features_dict),
            }

    # ============================================================
    # FASE 3: EJECUCIÓN
    # ============================================================

    def phase_execute(self, scoring_result: Dict) -> Dict:
        log_debug("[FASE 3] EJECUCIÓN — Control de riesgo")

        bal = self.exchange.get_balance()
        if bal and 'USDT' in bal:
            equity = float(bal['USDT'].get('available', self.capital))
            self.capital = equity
            risk_metrics = self.risk.update(equity)
        else:
            risk_metrics = self.risk.get_metrics()

        # Detectar cambio de modo de riesgo
        current_mode = risk_metrics['mode']
        if hasattr(self, '_last_mode') and self._last_mode != current_mode:
            log_info(f"Cambio de modo de riesgo: {self._last_mode} → {current_mode}")
        self._last_mode = current_mode

        # Verificar kill switch
        if self.risk.is_kill_switch_activated():
            log_error(f"⛔ KILL SWITCH ACTIVADO: {self.risk.get_kill_reason()}")
            update_dashboard("KILL", equity=self.capital, pnl_total=self.pnl_total,
                             trades=self.trades_count, modo="KILL")
            self._emergency_shutdown()
            return {'trade_executed': False, 'kill_switch': True, 'reason': self.risk.get_kill_reason()}

        params = self.risk.get_effective_parameters()
        if not params['trading_enabled']:
            log_debug("Trading deshabilitado por modo de riesgo")
            return {'trade_executed': False, 'kill_switch': False, 'reason': 'trading_disabled'}

        symbol = scoring_result.get('symbol')
        score = scoring_result.get('score', 0)
        features = scoring_result.get('features')

        if not symbol or score < config.MIN_SCORE + params.get('min_score_boost', 0):
            log_debug(f"Score insuficiente: {score:.3f}")
            return {'trade_executed': False, 'kill_switch': False, 'reason': 'score_too_low'}

        if self.strategy.is_on_cooldown(symbol):
            log_debug(f"{symbol} en cooldown")
            return {'trade_executed': False, 'kill_switch': False, 'reason': 'cooldown'}

        leverage = params['leverage']
        size_factor = params['size_factor']

        success = self._execute_trade(symbol, score, features, leverage, size_factor)

        if success:
            self.strategy.set_cooldown(symbol)
            log_info(f"✅ Nueva posición abierta: {symbol} {features.get('trend_direction', 1)}")
            update_dashboard("RUNNING", symbol=symbol,
                             side='long' if features.get('trend_direction', 1) == 1 else 'short',
                             equity=self.capital, pnl_total=self.pnl_total,
                             trades=self.trades_count, modo=self.risk.mode)
            # Actualizar PnL en la próxima iteración
        else:
            log_warning(f"❌ Falló la ejecución del trade en {symbol}")

        return {
            'trade_executed': success,
            'kill_switch': False,
            'symbol': symbol,
            'score': score,
            'leverage': leverage,
            'size_factor': size_factor,
        }

    # ============================================================
    # EJECUCIÓN DE TRADE
    # ============================================================

    def _execute_trade(self, symbol: str, score: float, features: Dict,
                       leverage: int, size_factor: float) -> bool:
        try:
            ticker = self.exchange.get_ticker(symbol)
            if not ticker:
                log_error(f"No se pudo obtener ticker para {symbol}")
                return False

            entry = float(ticker.get('last', 0))
            if entry <= 0:
                log_error(f"Precio inválido para {symbol}: {entry}")
                return False

            direction = features.get('trend_direction', 1)
            side = 'long' if direction == 1 else 'short'

            info = self.instrument_info.get(symbol, {'ct_val': 0.01, 'lot_sz': 0.001, 'min_sz': 0.001})
            ct_val = info['ct_val']
            lot_sz = info['lot_sz']
            min_sz = info['min_sz']

            available = self.capital * 0.98
            desired_notional = available * leverage * size_factor
            size = desired_notional / (entry * ct_val)
            size = max(min_sz, round(size / lot_sz) * lot_sz)

            if size <= 0:
                log_error(f"Tamaño inválido para {symbol}: {size}")
                return False

            log_info(f"TRADE: {symbol} | {side.upper()} | Entry: {entry:.2f} | Size: {size:.4f}")

            # Market order
            order_res = self.exchange.place_market_order(symbol, side, size)
            if not order_res.get('ok'):
                log_error(f"Error en market order: {order_res.get('error')}")
                return False

            # TP y SL
            atr = features.get('atr', entry * 0.01)
            if side == 'long':
                tp_price = entry + atr * config.TP_MULT
                sl_price = entry - atr * config.SL_MULT
                tp_side = 'sell'
            else:
                tp_price = entry - atr * config.TP_MULT
                sl_price = entry + atr * config.SL_MULT
                tp_side = 'buy'

            tp_res = self.exchange.place_conditional_order(symbol, tp_side, size, tp_price, pos_side=side)
            if not tp_res.get('ok'):
                log_error(f"Error en TP: {tp_res.get('error')}")
                self.exchange.close_position_market(symbol, side, size)
                return False

            sl_res = self.exchange.place_conditional_order(symbol, tp_side, size, sl_price, pos_side=side)
            if not sl_res.get('ok'):
                log_error(f"Error en SL: {sl_res.get('error')}")
                self.exchange.cancel_algo_order(tp_res.get('algo_id'))
                self.exchange.close_position_market(symbol, side, size)
                return False

            # Trailing solo en modo NORMAL y leverage >= 5
            if self.risk.mode == "NORMAL" and leverage >= 5:
                callback_ratio = (0.5 * atr / entry) * 100
                callback_ratio = max(0.3, min(5.0, callback_ratio))
                activation = entry + (tp_price - entry) * 0.5 if side == 'long' else entry - (entry - tp_price) * 0.5
                self.exchange.place_trailing_order(symbol, tp_side, size, callback_ratio, activation)

            self.position = {
                'symbol': symbol,
                'side': side,
                'entry': entry,
                'size': size,
                'tp_algo_id': tp_res.get('algo_id'),
                'sl_algo_id': sl_res.get('algo_id'),
                'open_time': time.time(),
            }

            self.trades_count += 1
            self.metrics.log_trade({
                'symbol': symbol,
                'side': side,
                'entry': entry,
                'size': size,
                'leverage': leverage,
                'size_factor': size_factor,
                'score': score,
                'tp': tp_price,
                'sl': sl_price,
            })

            return True

        except Exception as e:
            log_error(f"Error en execute_trade: {e}")
            return False

    # ============================================================
    # EMERGENCIA Y CIERRE
    # ============================================================

    def _emergency_shutdown(self):
        log_info("⛔ Ejecutando cierre de emergencia...")
        self.exchange.close_all_positions()
        self.exchange.cancel_all_orders()
        log_info("✅ Cierre de emergencia completado.")

    # ============================================================
    # RUN
    # ============================================================

    def run(self) -> Dict:
        start_time = time.time()

        if not self.init():
            return {'success': False, 'error': 'init_failed'}

        # Cleanup
        cleanup_result = self.phase_cleanup()
        if cleanup_result['positions_found'] > 0:
            # Si había posiciones, actualizar dashboard y registrar
            update_dashboard("RUNNING", symbol="(posición activa)",
                             equity=self.capital, pnl_total=self.pnl_total,
                             trades=self.trades_count, modo=self.risk.mode)

        if self.risk.is_kill_switch_activated():
            self.metrics.save_final_report()
            return {'success': False, 'error': 'kill_switch', 'reason': self.risk.get_kill_reason()}

        # Scoring
        scoring_result = self.phase_scoring()

        # Ejecución
        execution_result = self.phase_execute(scoring_result)

        # Actualizar PnL si hubo trade
        if execution_result.get('trade_executed'):
            # Calcular PnL desde el balance actual
            bal = self.exchange.get_balance()
            if bal and 'USDT' in bal:
                equity = float(bal['USDT'].get('available', self.capital))
                pnl_ejecucion = equity - self.last_equity
                self.pnl_total += pnl_ejecucion
                self.last_equity = equity

                # Registrar en CSV
                append_pnl_row(
                    equity=equity,
                    pnl_total=self.pnl_total,
                    pnl_ejecucion=pnl_ejecucion,
                    trades=self.trades_count,
                    modo=self.risk.mode
                )

        # Guardar métricas del ciclo
        cycle_data = {
            'symbols_scanned': scoring_result.get('symbols_scanned', 0),
            'best_symbol': scoring_result.get('symbol'),
            'best_score': scoring_result.get('score', 0),
            'trade_executed': execution_result.get('trade_executed', False),
            'trade_symbol': execution_result.get('symbol'),
            'latency_ms': (time.time() - start_time) * 1000,
            'mode': self.risk.mode,
            'dd_actual': self.risk.dd_actual,
            'kill_switch': self.risk.is_kill_switch_activated(),
        }
        self.metrics.log_cycle(cycle_data)

        elapsed = (time.time() - start_time)
        log_info(f"CICLO COMPLETADO en {elapsed:.2f}s")

        # Dashboard final
        update_dashboard("FINALIZADO",
                         equity=self.capital,
                         pnl_total=self.pnl_total,
                         trades=self.trades_count,
                         modo=self.risk.mode)

        # Guardar reporte final
        self.metrics.save_final_report()

        # Registrar fin del bot
        log_info("🔥 KRISHNA KILLING SPREE — FIN DE EJECUCIÓN")

        return {
            'success': True,
            'mode': self.risk.mode,
            'dd': self.risk.dd_actual,
            'trade_executed': execution_result.get('trade_executed', False),
            'symbol': execution_result.get('symbol'),
            'elapsed_seconds': elapsed,
        }


# ============================================================
# ENTRY POINT
# ============================================================

def main():
    API_KEY = os.environ.get('OKX_API_KEY')
    SECRET_KEY = os.environ.get('OKX_SECRET_KEY')
    PASSPHRASE = os.environ.get('OKX_PASSPHRASE')
    DEMO = os.environ.get('OKX_DEMO', 'true').lower() == 'true'

    if not all([API_KEY, SECRET_KEY, PASSPHRASE]):
        log_error("Faltan credenciales OKX.")
        log_error("Set: OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE")
        sys.exit(1)

    lock_fd = acquire_lock()
    if lock_fd is None:
        log_warning("Otra instancia del bot está ejecutándose. Saliendo.")
        sys.exit(0)

    try:
        bot = KrishnaKillingSpree(API_KEY, SECRET_KEY, PASSPHRASE, DEMO)
        result = bot.run()
        log_info(f"Resultado final: {result}")
    except Exception as e:
        log_error(f"Error inesperado: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        release_lock(lock_fd)
        log_info("Ejecución finalizada.")


if __name__ == "__main__":
    main()
