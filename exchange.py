# exchange.py
# ============================================================
# CLIENTE OKX V5 — COPIADO DEL REPOSITORIO ORIGINAL
# ============================================================
# https://github.com/oz10000/BlackBirdOfPrey
# ============================================================

import hmac
import hashlib
import base64
import time
import json
import requests
from datetime import datetime
from typing import Dict, List, Optional, Union

import config


class Exchange:
    def __init__(self, api_key: str, secret_key: str, passphrase: str, demo: bool = True):
        self.api_key = api_key
        self.secret_key = secret_key.encode('utf-8')
        self.passphrase = passphrase
        self.demo = demo
        self.base_url = "https://www.okx.com" if not demo else "https://www.okx.com"
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'BlackBird-Bot/2.0'
        })
        self.time_offset = 0
        self._instrument_cache = {}

    # ============================================================
    # AUTENTICACIÓN Y FIRMA
    # ============================================================

    def _iso_timestamp(self) -> str:
        return datetime.utcnow().isoformat(timespec='milliseconds') + 'Z'

    def _sync_time(self) -> int:
        """Sincroniza el tiempo con el servidor OKX."""
        try:
            resp = self.session.get(f"{self.base_url}/api/v5/public/time", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('code') == '0':
                    server_time = int(data['data'][0]['ts'])
                    local_time = int(time.time() * 1000)
                    self.time_offset = server_time - local_time
                    return server_time
        except Exception:
            pass
        return 0

    def _sign_request(self, method: str, path: str, body: str = '') -> Dict:
        """Genera firma HMAC-SHA256 para OKX V5."""
        timestamp = self._iso_timestamp()
        sign_str = timestamp + method + path + body
        signature = base64.b64encode(
            hmac.new(self.secret_key, sign_str.encode('utf-8'), hashlib.sha256).digest()
        ).decode('utf-8')

        return {
            'OK-ACCESS-KEY': self.api_key,
            'OK-ACCESS-SIGN': signature,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json'
        }

    def _request(self, method: str, path: str, params: dict = None, body: dict = None,
                 max_retries: int = 3) -> dict:
        """Realiza una petición autenticada o pública con reintentos."""
        url = f"{self.base_url}{path}"

        query_str = ''
        if params:
            import urllib.parse
            query_str = '?' + urllib.parse.urlencode(params)

        body_str = json.dumps(body) if body else ''
        headers = self._sign_request(method, path + query_str, body_str)

        for attempt in range(max_retries):
            try:
                if method.upper() == 'GET':
                    response = self.session.get(url + query_str, headers=headers, timeout=10)
                else:
                    response = self.session.post(url + query_str, headers=headers, data=body_str, timeout=10)

                response.raise_for_status()
                data = response.json()

                if data.get('code') != '0':
                    # Si es error de rate limit, esperar y reintentar
                    if data.get('code') == '429':
                        time.sleep(2 ** attempt)
                        continue
                    return {'ok': False, 'error': data.get('msg', 'Unknown error'), 'data': None}

                return {'ok': True, 'error': None, 'data': data.get('data', [])}

            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return {'ok': False, 'error': str(e), 'data': None}
            except Exception as e:
                return {'ok': False, 'error': str(e), 'data': None}

        return {'ok': False, 'error': 'Max retries exceeded', 'data': None}

    # ============================================================
    # MÉTODOS PÚBLICOS (sin autenticación)
    # ============================================================

    def fetch_historical_candles(self, symbol: str, limit: int = 100, bar: str = '5m') -> Optional[dict]:
        """
        Descarga velas históricas desde OKX Public API.
        Retorna dict con listas: ts, o, h, l, c, v.
        """
        url = f"{self.base_url}/api/v5/market/candles"
        params = {'instId': symbol, 'bar': bar, 'limit': limit}

        try:
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data.get('code') != '0' or not data.get('data'):
                return None

            candles = data['data']
            result = {
                'ts': [c[0] for c in candles],
                'o': [float(c[1]) for c in candles],
                'h': [float(c[2]) for c in candles],
                'l': [float(c[3]) for c in candles],
                'c': [float(c[4]) for c in candles],
                'v': [float(c[5]) for c in candles],
            }
            return result

        except Exception:
            return None

    def get_ticker(self, symbol: str) -> Optional[Dict]:
        """Obtiene el ticker actual de un símbolo."""
        url = f"{self.base_url}/api/v5/market/ticker"
        params = {'instId': symbol}
        try:
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data.get('code') != '0' or not data.get('data'):
                return None
            return data['data'][0]
        except Exception:
            return None

    def get_instrument_info(self, symbol: str) -> Dict:
        """Obtiene información del contrato (ctVal, lotSz, minSz)."""
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]

        url = f"{self.base_url}/api/v5/public/instruments"
        params = {'instType': 'SWAP', 'instId': symbol}
        try:
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data.get('code') != '0' or not data.get('data'):
                return {'ctVal': 0.01, 'lotSz': 0.001, 'minSz': 0.001}

            info = data['data'][0]
            result = {
                'ctVal': float(info.get('ctVal', 0.01)),
                'lotSz': float(info.get('lotSz', 0.001)),
                'minSz': float(info.get('minSz', 0.001)),
            }
            self._instrument_cache[symbol] = result
            return result
        except Exception:
            return {'ctVal': 0.01, 'lotSz': 0.001, 'minSz': 0.001}

    # ============================================================
    # MÉTODOS PRIVADOS (autenticados)
    # ============================================================

    def set_leverage(self, symbol: str, leverage: int, mgn_mode: str = 'isolated') -> bool:
        """Establece apalancamiento para un símbolo."""
        body = {'instId': symbol, 'lever': str(leverage), 'mgnMode': mgn_mode}
        result = self._request('POST', '/api/v5/account/set-leverage', body=body)
        return result.get('ok', False)

    def get_balance(self) -> Dict:
        """Obtiene el balance de la cuenta."""
        result = self._request('GET', '/api/v5/account/balance')
        if not result.get('ok'):
            return {}

        data = result.get('data', [])
        if not data:
            return {}

        for detail in data:
            if detail.get('uTime'):
                for asset in detail.get('details', []):
                    if asset.get('ccy') == 'USDT':
                        return {
                            'USDT': {
                                'available': float(asset.get('availBal', 0)),
                                'equity': float(asset.get('eq', 0))
                            }
                        }
        return {}

    def get_positions(self, symbol: str = None) -> Dict:
        """Obtiene posiciones abiertas."""
        params = {}
        if symbol:
            params['instId'] = symbol
        result = self._request('GET', '/api/v5/account/positions', params=params)
        if not result.get('ok'):
            return {'ok': False, 'data': []}
        return {'ok': True, 'data': result.get('data', [])}

    def get_pending_orders(self, symbol: str = None) -> Dict:
        """Obtiene órdenes pendientes (market/limit)."""
        params = {}
        if symbol:
            params['instId'] = symbol
        result = self._request('GET', '/api/v5/trade/orders-pending', params=params)
        if not result.get('ok'):
            return {'ok': False, 'data': []}
        return {'ok': True, 'data': result.get('data', [])}

    def get_pending_algo_orders(self, symbol: str = None) -> Dict:
        """Obtiene órdenes algorítmicas pendientes (TP/SL/trailing)."""
        params = {}
        if symbol:
            params['instId'] = symbol
        result = self._request('GET', '/api/v5/trade/orders-algo-pending', params=params)
        if not result.get('ok'):
            return {'ok': False, 'data': []}
        return {'ok': True, 'data': result.get('data', [])}

    def place_market_order(self, symbol: str, side: str, size: float) -> Dict:
        """Coloca una orden de mercado. side: 'buy' o 'sell'."""
        body = {'instId': symbol, 'side': side, 'ordType': 'market', 'size': str(size)}
        result = self._request('POST', '/api/v5/trade/order', body=body)
        if result.get('ok'):
            data = result.get('data', [{}])[0]
            return {'ok': True, 'order_id': data.get('ordId'), 'data': data}
        return {'ok': False, 'error': result.get('error')}

    def place_conditional_order(self, symbol: str, side: str, size: float,
                                trigger_price: float, order_price: str = '-1',
                                trigger_px_type: str = 'last', pos_side: str = 'long') -> Dict:
        """Coloca una orden condicional (TP/SL)."""
        body = {
            'instId': symbol,
            'side': side,
            'ordType': 'conditional',
            'size': str(size),
            'triggerPx': str(trigger_price),
            'ordPx': order_price,
            'triggerPxType': trigger_px_type,
            'posSide': pos_side,
        }
        result = self._request('POST', '/api/v5/trade/order-algo', body=body)
        if result.get('ok'):
            data = result.get('data', [{}])[0]
            return {'ok': True, 'algo_id': data.get('algoId'), 'data': data}
        return {'ok': False, 'error': result.get('error')}

    def place_trailing_order(self, symbol: str, side: str, size: float,
                             callback_ratio: float, trigger_price: float) -> Dict:
        """Coloca un trailing stop nativo de OKX."""
        body = {
            'instId': symbol,
            'side': side,
            'ordType': 'move_order_stop',
            'size': str(size),
            'callbackRatio': str(round(callback_ratio, 2)),
            'triggerPx': str(round(trigger_price, 2)),
            'ordPx': '-1',
        }
        result = self._request('POST', '/api/v5/trade/order-algo', body=body)
        if result.get('ok'):
            data = result.get('data', [{}])[0]
            return {'ok': True, 'algo_id': data.get('algoId'), 'data': data}
        return {'ok': False, 'error': result.get('error')}

    def cancel_order(self, order_id: str) -> bool:
        """Cancela una orden activa."""
        body = {'ordId': order_id}
        result = self._request('POST', '/api/v5/trade/cancel-order', body=body)
        return result.get('ok', False)

    def cancel_algo_order(self, algo_id: str) -> bool:
        """Cancela una orden algorítmica."""
        body = {'algoId': [algo_id]}
        result = self._request('POST', '/api/v5/trade/cancel-algos', body=body)
        return result.get('ok', False)

    def cancel_all_orders(self, symbol: str = None) -> int:
        """Cancela todas las órdenes (opcionalmente por símbolo)."""
        count = 0
        # Órdenes normales
        pending = self.get_pending_orders(symbol)
        if pending.get('ok'):
            for order in pending.get('data', []):
                if self.cancel_order(order.get('ordId')):
                    count += 1
        # Órdenes algorítmicas
        algo = self.get_pending_algo_orders(symbol)
        if algo.get('ok'):
            for order in algo.get('data', []):
                if self.cancel_algo_order(order.get('algoId')):
                    count += 1
        return count

    def close_position_market(self, symbol: str, side: str, size: float) -> Dict:
        """Cierra una posición con orden de mercado en sentido contrario."""
        close_side = 'sell' if side == 'long' else 'buy'
        return self.place_market_order(symbol, close_side, size)

    def close_all_positions(self) -> int:
        """Cierra todas las posiciones abiertas."""
        count = 0
        positions = self.get_positions()
        if not positions.get('ok'):
            return 0
        for pos in positions.get('data', []):
            symbol = pos.get('instId')
            side = pos.get('posSide', 'long')
            size = abs(float(pos.get('pos', 0)))
            if size > 0:
                self.close_position_market(symbol, side, size)
                count += 1
        return count

    def connect(self) -> bool:
        """Verifica la conexión con OKX."""
        self._sync_time()
        result = self._request('GET', '/api/v5/account/balance')
        return result.get('ok', False)
