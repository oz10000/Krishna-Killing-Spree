# main.py
# ============================================================
# BLACKBIRD BOT V2 — GITHUB ACTIONS (STATELESS)
# ============================================================
# Flujo: sync → cleanup → fetch → score → select → execute → log → exit
# ============================================================

import os
import sys
import time
import json
from datetime import datetime

import config
from exchange import Exchange
from strategy import Strategy
from risk import RiskController
from cleanup import CleanupEngine
from metrics import MetricsCollector
from utils import log_info, log_warning, log_error, log_debug, acquire_lock, release_lock


class BlackBirdBotV2:
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

    # ============================================================
    # INICIALIZACIÓN
    # ============================================================

    def init(self) -> bool:
        log_info("=" * 60)
        log_info("BLACKBIRD BOT V2 — INICIO DE EJECUCIÓN")
        log_info(f"Timestamp: {datetime.now().isoformat()}")
        log_info("=" * 60)

        self.exchange = Exchange(self.api_key, self.secret_key, self.passphrase, self.demo)

        if not self.exchange.connect():
            log_error("Fallo en la conexión con OKX.")
            return False

        log_info("Conexión con OKX establecida.")

        self.strategy = Strategy()
        self.metrics = MetricsCollector()

        # Obtener balance inicial
        bal = self.exchange.get_balance()
        if bal and 'USDT' in bal:
            self.capital = float(bal['USDT'].get('available', config.CAPITAL_INICIAL))
            log_info(f"Capital disponible: {self.capital:.2f} USDT")

        self.risk = RiskController(self.capital)
        self.cleanup = CleanupEngine(self.exchange)

        # Información de instrumentos
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
        log_info("=" * 60)

        return True

    # ============================================================
    # FASE 1: CLEANUP (RECONCILIACIÓN)
    # ============================================================

    def phase_cleanup(self) -> Dict:
        """Limpia posiciones y órdenes huérfanas."""
        log_info("[FASE 1] CLEANUP — Reconciliación de estado")
        start = time.time()

        result = self.cleanup.sync_and_cleanup()

        elapsed = (time.time() - start) * 1000
        log_info(f"Cleanup completado en {elapsed:.0f}ms")
        log_info(f"  Posiciones encontradas: {result['positions_found']}")
        log_info(f"  Inconsistencias corregidas: {result['inconsistencies_fixed']}")

        return result

    # ============================================================
    # FASE 2: FETCH + SCORING
    # ============================================================

    def phase_scoring(self) -> Dict:
        """Descarga datos y calcula scores."""
        log_info("[FASE 2] SCORING — Análisis de mercado")
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

        # Seleccionar top asset
        result = self.strategy.select_top_asset(features_dict)

        elapsed = (time.time() - start) * 1000

        if result:
            symbol, score, features = result
            log_info(f"  Mejor activo: {symbol} (score: {score:.3f})")
        else:
            log_info("  No se encontraron señales válidas")
            symbol, score, features = None, 0.0, None

        return {
            'symbol': symbol,
            'score': score,
            'features': features,
            'features_dict': features_dict,
            'latency_ms': elapsed,
            'symbols_scanned': len(features_dict),
        }

    # ============================================================
    # FASE 3: RIESGO Y EJECUCIÓN
    # ============================================================

    def phase_execute(self, scoring_result: Dict) -> Dict:
        """Aplica control de riesgo y ejecuta trade si procede."""
        log_info("[FASE 3] EJECUCIÓN — Control de riesgo y trading")

        # Actualizar riesgo con capital actual
        bal = self.exchange.get_balance()
        if bal and 'USDT' in bal:
            equity = float(bal['USDT'].get('available', self.capital))
            self.capital = equity
            risk_metrics = self.risk.update(equity)
        else:
            risk_metrics = self.risk.get_metrics()

        log_info(f"  Drawdown: {risk_metrics['dd_actual']:.2f}% | Modo: {risk_metrics['mode']}")
        log_info(f"  Leverage efectivo: {risk_metrics['leverage_effective']}× | Size factor: {risk_metrics['size_factor']*100:.0f}%")

        # Verificar kill switch
        if self.risk.is_kill_switch_activated():
            log_error(f"⛔ KILL SWITCH ACTIVADO: {self.risk.get_kill_reason()}")
            self._emergency_shutdown()
            return {'trade_executed': False, 'kill_switch': True, 'reason': self.risk.get_kill_reason()}

        # Obtener parámetros efectivos
        params = self.risk.get_effective_parameters()
        if not params['trading_enabled']:
            log_info("  Trading deshabilitado por modo de riesgo")
            return {'trade_executed': False, 'kill_switch': False, 'reason': 'trading_disabled'}

        # Verificar si hay señal
        symbol = scoring_result.get('symbol')
        score = scoring_result.get('score', 0)
        features = scoring_result.get('features')

        if not symbol or score < config.MIN_SCORE + params.get('min_score_boost', 0):
            log_info(f"  Score insuficiente: {score:.3f} < {config.MIN_SCORE + params.get('min_score_boost', 0):.3f}")
            return {'trade_executed': False, 'kill_switch': False, 'reason': 'score_too_low'}

        # Verificar cooldown
        if self.strategy.is_on_cooldown(symbol):
            log_info(f"  {symbol} en cooldown")
            return {'trade_executed': False, 'kill_switch': False, 'reason': 'cooldown'}

        # Ejecutar trade
        leverage = params['leverage']
        size_factor = params['size_factor']

        success = self._execute_trade(symbol, score, features, leverage, size_factor)

        if success:
            # Activar cooldown
            self.strategy.set_cooldown(symbol)
            log_info(f"  ✅ Trade ejecutado en {symbol} (cooldown activado)")
        else:
            log_warning(f"  ❌ Falló la ejecución del trade en {symbol}")

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
        """Ejecuta una orden de mercado con TP/SL y trailing."""
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

            # Calcular tamaño
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

            log_info(f"📈 TRADE: {symbol} | {side.upper()} | Entry: {entry:.2f} | Size: {size:.4f} | Score: {score:.3f}")

            # 1. Market order
            order_res = self.exchange.place_market_order(symbol, side, size)
            if not order_res.get('ok'):
                log_error(f"Error en market order: {order_res.get('error')}")
                return False

            # 2. TP y SL
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

            # 3. Trailing (opcional, solo en modo normal)
            if self.risk.mode == "NORMAL" and leverage >= 5:
                callback_ratio = (0.5 * atr / entry) * 100
                callback_ratio = max(0.3, min(5.0, callback_ratio))
                activation = entry + (tp_price - entry) * 0.5 if side == 'long' else entry - (entry - tp_price) * 0.5
                trail_res = self.exchange.place_trailing_order(symbol, tp_side, size, callback_ratio, activation)
                if trail_res.get('ok'):
                    log_info(f"  Trailing activado (callback: {callback_ratio:.2f}%)")

            # Registrar posición (para monitoreo futuro)
            self.position = {
                'symbol': symbol,
                'side': side,
                'entry': entry,
                'size': size,
                'tp_algo_id': tp_res.get('algo_id'),
                'sl_algo_id': sl_res.get('algo_id'),
                'open_time': time.time(),
            }

            # Registrar en métricas
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
                'equity_before': self.capital,
                'equity_after': self.capital,  # Se actualizará al cierre
                'pnl_pct': 0,  # Se actualizará al cierre
                'status': 'opened',
            })

            return True

        except Exception as e:
            log_error(f"Error en execute_trade: {e}")
            return False

    # ============================================================
    # EMERGENCIA Y CIERRE
    # ============================================================

    def _emergency_shutdown(self):
        """Cierre de emergencia por kill switch."""
        log_info("⛔ Ejecutando cierre de emergencia...")
        # Cerrar todas las posiciones
        self.exchange.close_all_positions()
        # Cancelar todas las órdenes
        self.exchange.cancel_all_orders()
        log_info("✅ Cierre de emergencia completado.")

    # ============================================================
    # RUN (ENTRY POINT)
    # ============================================================

    def run(self) -> Dict:
        """Ejecuta un ciclo completo."""
        start_time = time.time()

        if not self.init():
            return {'success': False, 'error': 'init_failed'}

        # FASE 1: Cleanup
        cleanup_result = self.phase_cleanup()

        # Verificar kill switch después de cleanup
        if self.risk.is_kill_switch_activated():
            self.metrics.save_final_report()
            return {'success': False, 'error': 'kill_switch', 'reason': self.risk.get_kill_reason()}

        # FASE 2: Scoring
        scoring_result = self.phase_scoring()

        # FASE 3: Ejecución
        execution_result = self.phase_execute(scoring_result)

        # Métricas del ciclo
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

        # Reporte final
        elapsed = (time.time() - start_time)
        log_info("=" * 60)
        log_info(f"CICLO COMPLETADO en {elapsed:.2f}s")
        log_info(f"  Modo riesgo: {self.risk.mode}")
        log_info(f"  DD actual: {self.risk.dd_actual:.2f}%")
        log_info(f"  Trade ejecutado: {'✅ SI' if execution_result.get('trade_executed') else '❌ NO'}")
        if execution_result.get('trade_executed'):
            log_info(f"  Símbolo: {execution_result.get('symbol')}")
            log_info(f"  Score: {execution_result.get('score', 0):.3f}")
        log_info("=" * 60)

        # Guardar métricas finales
        self.metrics.save_final_report()

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

    # Adquirir lock
    lock_fd = acquire_lock()
    if lock_fd is None:
        log_warning("Otra instancia del bot está ejecutándose. Saliendo.")
        sys.exit(0)

    try:
        bot = BlackBirdBotV2(API_KEY, SECRET_KEY, PASSPHRASE, DEMO)
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