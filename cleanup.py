# cleanup.py
# ============================================================
# CLEANUP ENGINE — RECONCILIACIÓN DE POSICIONES Y ÓRDENES
# ============================================================
# Detecta y corrige:
#   - posiciones huérfanas
#   - órdenes huérfanas
#   - posiciones sin protección (TP/SL)
#   - duplicados
#   - inconsistencias long/short
# ============================================================

import time
from typing import Dict, List, Tuple

import config
from exchange import Exchange
from utils import log_info, log_warning, log_error, log_debug


class CleanupEngine:
    def __init__(self, exchange: Exchange):
        self.exchange = exchange

    # ============================================================
    # SINCRONIZACIÓN COMPLETA
    # ============================================================

    def sync_and_cleanup(self) -> Dict:
        """
        Ejecuta reconciliación completa.
        Retorna resumen de acciones tomadas.
        """
        result = {
            'positions_found': 0,
            'positions_closed': 0,
            'orders_cancelled': 0,
            'algo_orders_cancelled': 0,
            'protections_repaired': 0,
            'inconsistencies_fixed': 0,
            'symbols_affected': [],
        }

        # 1. Obtener estado actual
        positions = self.exchange.get_positions()
        if not positions.get('ok'):
            log_error("No se pudo obtener posiciones para cleanup")
            return result

        pos_data = positions.get('data', [])
        result['positions_found'] = len(pos_data)

        # 2. Obtener órdenes pendientes
        pending = self.exchange.get_pending_orders()
        pending_data = pending.get('data', []) if pending.get('ok') else []

        # 3. Obtener órdenes algorítmicas
        algo = self.exchange.get_pending_algo_orders()
        algo_data = algo.get('data', []) if algo.get('ok') else []

        # 4. Verificar límite de posiciones
        if len(pos_data) > config.MAX_POSITIONS:
            log_warning(f"Más de {config.MAX_POSITIONS} posiciones abiertas. Cerrando excedentes.")
            for pos in pos_data[config.MAX_POSITIONS:]:
                symbol = pos.get('instId')
                side = pos.get('posSide', 'long')
                size = abs(float(pos.get('pos', 0)))
                if size > 0:
                    self.exchange.close_position_market(symbol, side, size)
                    result['positions_closed'] += 1
                    result['symbols_affected'].append(symbol)
                    result['inconsistencies_fixed'] += 1

        # 5. Verificar órdenes huérfanas (sin posición)
        pos_symbols = {p.get('instId') for p in pos_data if float(p.get('pos', 0)) > 0}
        for order in pending_data:
            sym = order.get('instId')
            if sym not in pos_symbols:
                log_warning(f"Orden huérfana detectada: {order.get('ordId')} en {sym}")
                self.exchange.cancel_order(order.get('ordId'))
                result['orders_cancelled'] += 1
                result['inconsistencies_fixed'] += 1

        for algo_order in algo_data:
            sym = algo_order.get('instId')
            if sym not in pos_symbols:
                log_warning(f"Orden algorítmica huérfana: {algo_order.get('algoId')} en {sym}")
                self.exchange.cancel_algo_order(algo_order.get('algoId'))
                result['algo_orders_cancelled'] += 1
                result['inconsistencies_fixed'] += 1

        # 6. Verificar posiciones sin protección (TP/SL)
        for pos in pos_data:
            symbol = pos.get('instId')
            size = abs(float(pos.get('pos', 0)))
            if size <= 0:
                continue

            # Verificar si tiene TP/SL
            has_tp = False
            has_sl = False
            for algo_order in algo_data:
                if algo_order.get('instId') != symbol:
                    continue
                ord_type = algo_order.get('ordType')
                if ord_type in ['conditional', 'trigger']:
                    # Determinar si es TP o SL por precio relativo
                    if pos.get('posSide') == 'long':
                        if float(algo_order.get('triggerPx', 0)) > float(pos.get('avgPx', 0)):
                            has_tp = True
                        else:
                            has_sl = True
                    else:  # short
                        if float(algo_order.get('triggerPx', 0)) < float(pos.get('avgPx', 0)):
                            has_tp = True
                        else:
                            has_sl = True

            if not has_tp or not has_sl:
                log_warning(f"Posición sin protección: {symbol} (TP: {has_tp}, SL: {has_sl})")
                # Recrear protecciones
                self._repair_protections(pos, has_tp, has_sl)
                result['protections_repaired'] += 1
                result['inconsistencies_fixed'] += 1

        # 7. Verificar conflictos (long + short mismo símbolo)
        symbols_seen = {}
        for pos in pos_data:
            sym = pos.get('instId')
            side = pos.get('posSide')
            if sym in symbols_seen:
                log_warning(f"Conflicto long/short en {sym}. Cerrando ambas.")
                # Cerrar ambas posiciones
                for p in pos_data:
                    if p.get('instId') == sym:
                        self.exchange.close_position_market(
                            sym, p.get('posSide'), abs(float(p.get('pos', 0)))
                        )
                        result['positions_closed'] += 1
                result['inconsistencies_fixed'] += 1
            else:
                symbols_seen[sym] = side

        log_info(f"Cleanup completado: {result['inconsistencies_fixed']} inconsistencias corregidas")
        return result

    # ============================================================
    # REPARACIÓN DE PROTECCIONES
    # ============================================================

    def _repair_protections(self, position: Dict, has_tp: bool, has_sl: bool):
        """Recrea TP y SL para una posición sin protección."""
        symbol = position.get('instId')
        side = position.get('posSide', 'long')
        size = abs(float(position.get('pos', 0)))
        entry = float(position.get('avgPx', 0))
        mark = float(position.get('markPx', entry))

        if size <= 0:
            return

        # Calcular TP/SL con ATR aproximado
        # Usar un ATR estimado (5% de entrada como aproximación)
        atr = abs(mark - entry) * 0.8 if abs(mark - entry) > 0 else entry * 0.01

        if side == 'long':
            tp_price = entry + atr * config.TP_MULT
            sl_price = entry - atr * config.SL_MULT
            tp_side = 'sell'
        else:
            tp_price = entry - atr * config.TP_MULT
            sl_price = entry + atr * config.SL_MULT
            tp_side = 'buy'

        if not has_tp:
            log_info(f"Reparando TP para {symbol}")
            self.exchange.place_conditional_order(symbol, tp_side, size, tp_price, pos_side=side)

        if not has_sl:
            log_info(f"Reparando SL para {symbol}")
            self.exchange.place_conditional_order(symbol, tp_side, size, sl_price, pos_side=side)
