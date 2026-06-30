# main.py
# ============================================================
# KRISHNA KILLING SPREE — BUCLE PRINCIPAL (STATELESS)
# ============================================================
# Flujo: init → cleanup → scoring → execute → log → exit
# ============================================================

import os
import sys
import time
from datetime import datetime
from typing import Dict

import config
from exchange import Exchange
from strategy import Strategy
from risk import RiskController
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
        self.metrics = None

        self.capital = config.CAPITAL_INICIAL
        self.last_equity = self.capital
        self.position = None
        self.instrument_info = {}
        self.trades_count = 0
        self.pnl_total = 0.0
        self._last_mode = "NORMAL"

        init_pnl_file()

    # ============================================================
    # INICIALIZACIÓN
    # ============================================================

    def init(self) -> bool:
        log_info("🔥 KRISHNA KILLING SPREE — INICIO")
        log_info(f"Timestamp: {datetime.now().isoformat()}")

        self.exchange = Exchange(self.api_key, self.secret_key, self.passphrase, self.demo)

        if not self.exchange.connect():
            log_error("Fallo en la conexión con OKX.")
            return False

        log_info("Conexión OKX establecida.")

        self.strategy = Strategy()
        self.metrics = MetricsCollector()

        bal = self.exchange.get_balance()
        if bal and 'USDT' in bal:
            self.capital = float(bal['USDT'].get('available', config.CAPITAL_INICIAL))
            self.last_equity = self.capital
            log_info(f"Capital disponible: {self.capital:.2f} USDT")

        self.risk = RiskController(self.capital)

        for sym in config.SYMBOLS:
            info = self.exchange.get_instrument_info(sym)
            self.instrument_info[sym] = {
                'ct_val': info.get('ctVal', 0.01),
                'lot_sz': info.get('lotSz', 0.001),
                'min_sz': info.get('minSz', 0.001),
            }

        log_info(f"Universo: {len(config.SYMBOLS)} activos")
        log_info(f"Apalancamiento base: {config.BASE_LEVERAGE}x")

        update_dashboard("INICIANDO", equity=self.capital, modo="NORMAL")
        return True

    # ============================================================
    # CLEANUP
    # ============================================================

    def phase_cleanup(self) -> Dict:
        log_debug("[CLEANUP] Reconciliación de estado")

        # Obtener posiciones
        positions = self.exchange.get_positions()
        pos_data = positions.get('data', []) if positions.get('ok') else []

        if pos_data:
            log_info(f"Posición encontrada en OKX: {len(pos_data)} activa(s)")

        # Órdenes pendientes
        pending = self.exchange.get_pending_orders()
        pending_data = pending.get('data', []) if pending.get('ok') else []

        # Órdenes algorítmicas
        algo = self.exchange.get_pending_algo_orders()
        algo_data = algo.get('data', []) if algo.get('ok') else []

        # Cancelar órdenes huérfanas (sin posición)
        pos_symbols = {p.get('instId') for p in pos_data if float(p.get('pos', 0)) > 0}
        cancelled = 0

        for order in pending_data:
            if order.get('instId') not in pos_symbols:
                self.exchange.cancel_order(order.get('ordId'))
                cancelled += 1
                log_debug(f"Orden huérfana cancelada: {order.get('ordId')}")

        for order in algo_data:
            if order.get('instId') not in pos_symbols:
                self.exchange.cancel_algo_order(order.get('algoId'))
                cancelled += 1
                log_debug(f"Orden algorítmica huérfana cancelada: {order.get('algoId')}")

        if cancelled:
            log_info(f"Órdenes huérfanas canceladas: {cancelled}")

        # Asegurar solo 1 posición
        if len(pos_data) > config.MAX_POSITIONS:
            log_warning(f"Más de {config.MAX_POSITIONS} posición. Cerrando excedentes.")
            for pos in pos_data[config.MAX_POSITIONS:]:
                self.exchange.close_position_market(
                    pos.get('instId'),
                    pos.get('posSide', 'long'),
                    abs(float(pos.get('pos', 0)))
                )

        return {'positions_found': len(pos_data), 'orders_cancelled': cancelled}

    # ============================================================
    # SCORING
    # ============================================================

    def phase_scoring(self) -> Dict:
        log_debug("[SCORING] Análisis de mercado")
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

        if result:
            symbol, score, features = result
            log_debug(f"Mejor activo: {symbol} (score: {score:.3f})")
            return {
                'symbol': symbol,
                'score': score,
                'features': features,
                'features_dict': features_dict,
                'symbols_scanned': len(features_dict),
            }
        else:
            log_debug("No se encontraron señales válidas")
            return {
                'symbol': None,
                'score': 0.0,
                'features': None,
                'features_dict': features_dict,
                'symbols_scanned': len(features_dict),
            }

    # ============================================================
    # EJECUCIÓN
    # ============================================================

    def phase_execute(self, scoring_result: Dict) -> Dict:
        log_debug("[EJECUCIÓN] Control de riesgo")

        # Actualizar balance
        bal = self.exchange.get_balance()
        if bal and 'USDT' in bal:
            equity = float(bal['USDT'].get('available', self.capital))
            self.capital = equity
            risk_metrics = self.risk.update(equity)
        else:
            risk_metrics = self.risk.get_metrics()

        # Detectar cambio de modo
        current_mode = risk_metrics['mode']
        if self._last_mode != current_mode:
            log_info(f"Cambio de modo: {self._last_mode} → {current_mode}")
            self._last_mode = current_mode

        # Kill switch
        if self.risk.is_kill_switch_activated():
            log_error(f"⛔ KILL SWITCH: {self.risk.get_kill_reason()}")
            update_dashboard("KILL", equity=self.capital, pnl_total=self.pnl_total,
                             trades=self.trades_count, modo="KILL")
            self._emergency_shutdown()
            return {'trade_executed': False, 'kill_switch': True}

        # Parámetros efectivos
        params = self.risk.get_effective_parameters()
        if not params['trading_enabled']:
            log_debug("Trading deshabilitado por modo de riesgo")
            return {'trade_executed': False}

        symbol = scoring_result.get('symbol')
        score = scoring_result.get('score', 0)
        features = scoring_result.get('features')

        if not symbol or score < config.MIN_SCORE + params.get('min_score_boost', 0):
            log_debug(f"Score insuficiente: {score:.3f}")
            return {'trade_executed': False}

        if self.strategy.is_on_cooldown(symbol):
            log_debug(f"{symbol} en cooldown")
            return {'trade_executed': False}

        # Verificar que no haya posición activa
        positions = self.exchange.get_positions()
        if positions.get('ok') and positions.get('data'):
            log_info(f"Posición activa detectada. No se abre nuevo trade.")
            return {'trade_executed': False}

        # Ejecutar trade
        success = self._execute_trade(
            symbol, score, features,
            params['leverage'],
            params['size_factor']
        )

        if success:
            self.strategy.set_cooldown(symbol)
            log_info(f"✅ Nueva posición abierta: {symbol}")
            update_dashboard("RUNNING", symbol=symbol,
                             side='long' if features.get('trend_direction', 1) == 1 else 'short',
                             equity=self.capital, pnl_total=self.pnl_total,
                             trades=self.trades_count, modo=self.risk.mode)
            return {'trade_executed': True, 'symbol': symbol, 'score': score}
        else:
            log_warning(f"❌ Falló ejecución de trade en {symbol}")
            return {'trade_executed': False}

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
    # EMERGENCIA
    # ============================================================

    def _emergency_shutdown(self):
        log_info("⛔ Cierre de emergencia...")
        self.exchange.close_all_positions()
        self.exchange.cancel_all_orders()
        log_info("✅ Cierre completado.")

    # ============================================================
    # RUN
    # ============================================================

    def run(self) -> Dict:
        start_time = time.time()

        if not self.init():
            return {'success': False, 'error': 'init_failed'}

        # Cleanup
        self.phase_cleanup()

        if self.risk.is_kill_switch_activated():
            self.metrics.save_final_report()
            log_info("🔥 KRISHNA KILLING SPREE — FIN (KILL)")
            return {'success': False, 'error': 'kill_switch'}

        # Scoring
        scoring_result = self.phase_scoring()

        # Ejecución
        execution_result = self.phase_execute(scoring_result)

        # Actualizar PnL
        if execution_result.get('trade_executed'):
            bal = self.exchange.get_balance()
            if bal and 'USDT' in bal:
                equity = float(bal['USDT'].get('available', self.capital))
                pnl_ejecucion = equity - self.last_equity
                self.pnl_total += pnl_ejecucion
                self.last_equity = equity

                append_pnl_row(
                    equity=equity,
                    pnl_total=self.pnl_total,
                    pnl_ejecucion=pnl_ejecucion,
                    trades=self.trades_count,
                    modo=self.risk.mode
                )

        # Métricas de ciclo
        cycle_data = {
            'symbols_scanned': scoring_result.get('symbols_scanned', 0),
            'best_symbol': scoring_result.get('symbol'),
            'best_score': scoring_result.get('score', 0),
            'trade_executed': execution_result.get('trade_executed', False),
            'trade_symbol': execution_result.get('symbol'),
            'latency_ms': (time.time() - start_time) * 1000,
            'mode': self.risk.mode,
            'dd_actual': self.risk.dd_actual,
        }
        self.metrics.log_cycle(cycle_data)

        elapsed = time.time() - start_time
        log_info(f"CICLO COMPLETADO en {elapsed:.2f}s")

        update_dashboard("FINALIZADO",
                         equity=self.capital,
                         pnl_total=self.pnl_total,
                         trades=self.trades_count,
                         modo=self.risk.mode)

        self.metrics.save_final_report()
        log_info("🔥 KRISHNA KILLING SPREE — FIN")

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
        log_info(f"Resultado: {result}")
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
