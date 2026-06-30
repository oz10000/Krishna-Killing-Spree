# exchange.py
# ============================================================
# CLIENTE OKX V5 — CLONADO FUNCIONAL DE BLACKBIRDOFPREY
# ============================================================
# 100% equivalente en comportamiento de API
# ============================================================

import hmac
import hashlib
import base64
import time
import math
import json
import requests
from datetime import datetime
from typing import Dict, List, Optional, Union, Tuple

import config
from utils import log_info, log_error, log_debug


class Exchange:
    def __init__(self, api_key: str, secret_key: str, passphrase: str, demo: bool = True):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.demo = demo

        self.base_url = "https://www.okx.com"
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'Krishna-Killing-Spree/2.0'
        })

        self._connected = False
        self._time_offset = 0
        self._last_sync_time = 0
        self._sync_interval = 60
        self._instrument_cache = {}

    # ============================================================
    # UTILIDADES (IDÉNTICAS A BLACKBIRDOFPREY)
    # ============================================================

    def _instrument_id(self, symbol: str) -> str:
        symbol = symbol.upper().strip()
        if symbol.endswith("-USDT-SWAP"):
            return symbol
        return f"{symbol}-USDT-SWAP"

    def _to_str_size(self, size: float, lot_sz: float) -> str:
        """Formatea el tamaño según el lot size (exactamente igual que BlackBirdOfPrey)."""
        if lot_sz == 0:
            return str(int(size))
        decimals = 0
        while (lot_sz * 10 ** decimals) % 1 != 0 and decimals < 10:
            decimals += 1
        return f"{size:.{decimals}f}"

    # ============================================================
    # AUTENTICACIÓN (IDÉNTICA A BLACKBIRDOFPREY)
    # ============================================================

    def _iso_timestamp(self) -> str:
        return datetime.utcnow().isoformat(timespec='milliseconds') + 'Z'

    def _sync_time(self, force: bool = False) -> bool:
        now = time.time()
        if not force and (now - self._last_sync_time) < self._sync_interval:
            return True
        try:
            response = self.session.get(f"{self.base_url}/api/v5/public/time", timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '0':
                    server_time = int(data['data'][0]['ts'])
                    local_time = int(now * 1000)
                    self._time_offset = server_time - local_time
                    self._last_sync_time = now
                    return True
        except Exception:
            pass
        return False

    def _ensure_time_synced(self) -> None:
        if not self._sync_time(force=False):
            self._sync_time(force=True)

    def _sign_request(self, method: str, path: str, params: dict = None, body: dict = None) -> Tuple[Dict, str]:
        self._ensure_time_synced()
        timestamp = self._iso_timestamp()

        if body:
            body_str = json.dumps(body, separators=(', ', ': '), sort_keys=True)
        else:
            body_str = ""

        if params:
            query = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
            full_path = f"{path}?{query}"
        else:
            full_path = path

        sign_str = timestamp + method + full_path + body_str

        signature = base64.b64encode(
            hmac.new(
                self.secret_key.encode('utf-8'),
                sign_str.encode('utf-8'),
                hashlib.sha256
            ).digest()
        ).decode()

        headers = {
            'OK-ACCESS-KEY': self.api_key,
            'OK-ACCESS-SIGN': signature,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json'
        }

        if self.demo:
            headers["x-simulated-trading"] = "1"

        return headers, body_str

    def _request(self, method: str, path: str, params: dict = None, body: dict = None,
                 max_retries: int = 3, retry_delay: float = 1.0) -> dict:
        url = f"{self.base_url}{path}"
        query_str = ''
        if params:
            import urllib.parse
            query_str = '?' + urllib.parse.urlencode(params)

        headers, body_str = self._sign_request(method, path, params, body)

        for attempt in range(max_retries):
            try:
                if method.upper() == 'GET':
                    response = self.session.get(url + query_str, headers=headers, timeout=10)
                else:
                    response = self.session.post(url + query_str, headers=headers, data=body_str, timeout=10)

                if response.status_code == 429:
                    time.sleep(retry_delay * (2 ** attempt))
                    continue

                response.raise_for_status()
                data = response.json()

                if data.get('code') != '0':
                    if data.get('code') == '429':
                        time.sleep(retry_delay * (2 ** attempt))
                        continue
                    # 🔥 LOG DETALLADO DEL ERROR (igual que BlackBird)
                    error_msg = data.get('msg', 'Unknown error')
                    log_error(f"OKX error: {error_msg} (code: {data.get('code')})")
                    return {
                        'ok': False,
                        'error': error_msg,
                        'data': None,
                        'code': data.get('code')
                    }

                return {'ok': True, 'error': None, 'data': data.get('data', [])}

            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))
                    continue
                return {'ok': False, 'error': 'Timeout', 'data': None}

            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))
                    continue
                return {'ok': False, 'error': str(e), 'data': None}

            except Exception as e:
                return {'ok': False, 'error': str(e), 'data': None}

        return {'ok': False, 'error': 'Max retries exceeded', 'data': None}

    # ============================================================
    # MÉTODOS PÚBLICOS (IDÉNTICOS)
    # ============================================================

    def fetch_historical_candles(self, symbol: str, limit: int = 100, bar: str = '5m') -> Optional[dict]:
        url = f"{self.base_url}/api/v5/market/candles"
        params = {'instId': symbol, 'bar': bar, 'limit': limit}
        try:
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data.get('code') != '0' or not data.get('data'):
                return None
            candles = data['data']
            return {
                'ts': [c[0] for c in candles],
                'o': [float(c[1]) for c in candles],
                'h': [float(c[2]) for c in candles],
                'l': [float(c[3]) for c in candles],
                'c': [float(c[4]) for c in candles],
                'v': [float(c[5]) for c in candles],
            }
        except Exception:
            return None

    def get_ticker(self, symbol: str) -> Optional[Dict]:
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
    # ÓRDENES — CLONADAS EXACTAMENTE DE BLACKBIRDOFPREY
    # ============================================================

    def place_market_order(self, symbol: str, side: str, size: float) -> Dict:
        inst = self._instrument_id(symbol)
        pos_side = "long" if side.lower() == "buy" else "short"
        lot_sz = self.get_instrument_info(symbol).get('lotSz', 0.001)

        body = {
            "instId": inst,
            "tdMode": "cross",
            "side": side.lower(),
            "posSide": pos_side,
            "ordType": "market",
            "sz": self._to_str_size(size, lot_sz),
        }

        result = self._request("POST", "/api/v5/trade/order", body=body)
        if result.get('ok'):
            data = result.get('data', [{}])[0]
            return {'ok': True, 'order_id': data.get('ordId'), 'data': data}
        return {'ok': False, 'error': result.get('error')}

    def place_conditional_order(self, symbol: str, side: str, size: float,
                                trigger_price: float, order_price: str = '-1',
                                trigger_px_type: str = 'last', pos_side: str = 'long') -> Dict:
        inst = self._instrument_id(symbol)
        lot_sz = self.get_instrument_info(symbol).get('lotSz', 0.001)

        body = {
            "instId": inst,
            "tdMode": "cross",
            "side": side.lower(),
            "ordType": "trigger",
            "sz": self._to_str_size(size, lot_sz),
            "triggerPx": str(trigger_price),
            "orderPx": str(order_price),
            "triggerPxType": trigger_px_type,
            "posSide": pos_side,
        }

        result = self._request("POST", "/api/v5/trade/order-algo", body=body)
        if result.get('ok'):
            data = result.get('data', [{}])[0]
            return {'ok': True, 'algo_id': data.get('algoId'), 'data': data}
        return {'ok': False, 'error': result.get('error')}

    def place_trailing_order(self, symbol: str, side: str, size: float,
                             callback_ratio: float, trigger_price: float) -> Dict:
        inst = self._instrument_id(symbol)
        pos_side = "long" if side.lower() == "sell" else "short"
        lot_sz = self.get_instrument_info(symbol).get('lotSz', 0.001)

        body = {
            "instId": inst,
            "tdMode": "cross",
            "side": side.lower(),
            "ordType": "move_order_stop",
            "sz": self._to_str_size(size, lot_sz),
            "callbackRatio": str(round(callback_ratio, 2)),
            "triggerPx": str(round(trigger_price, 2)),
            "posSide": pos_side,
        }

        result = self._request("POST", "/api/v5/trade/order-algo", body=body)
        if result.get('ok'):
            data = result.get('data', [{}])[0]
            return {'ok': True, 'algo_id': data.get('algoId'), 'data': data}
        return {'ok': False, 'error': result.get('error')}

    # ============================================================
    # CANCELACIÓN Y CIERRE (IDÉNTICOS)
    # ============================================================

    def cancel_order(self, order_id: str) -> bool:
        body = {'ordId': order_id}
        result = self._request('POST', '/api/v5/trade/cancel-order', body=body)
        return result.get('ok', False)

    def cancel_algo_order(self, algo_id: str) -> bool:
        body = {'algoId': [algo_id]}
        result = self._request('POST', '/api/v5/trade/cancel-algos', body=body)
        return result.get('ok', False)

    def cancel_all_orders(self, symbol: str = None) -> int:
        count = 0
        pending = self.get_pending_orders(symbol)
        if pending.get('ok'):
            for order in pending.get('data', []):
                if self.cancel_order(order.get('ordId')):
                    count += 1
        algo = self.get_pending_algo_orders(symbol)
        if algo.get('ok'):
            for order in algo.get('data', []):
                if self.cancel_algo_order(order.get('algoId')):
                    count += 1
        return count

    def close_position_market(self, symbol: str, side: str, size: float) -> Dict:
        close_side = 'sell' if side == 'long' else 'buy'
        return self.place_market_order(symbol, close_side, size)

    def close_all_positions(self) -> int:
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

    # ============================================================
    # CONEXIÓN
    # ============================================================

    def get_balance(self) -> Dict:
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
        params = {}
        if symbol:
            params['instId'] = symbol
        result = self._request('GET', '/api/v5/account/positions', params=params)
        if not result.get('ok'):
            return {'ok': False, 'data': []}
        return {'ok': True, 'data': result.get('data', [])}

    def get_pending_orders(self, symbol: str = None) -> Dict:
        params = {}
        if symbol:
            params['instId'] = symbol
        result = self._request('GET', '/api/v5/trade/orders-pending', params=params)
        if not result.get('ok'):
            return {'ok': False, 'data': []}
        return {'ok': True, 'data': result.get('data', [])}

    def get_pending_algo_orders(self, symbol: str = None) -> Dict:
        params = {}
        if symbol:
            params['instId'] = symbol
        result = self._request('GET', '/api/v5/trade/orders-algo-pending', params=params)
        if not result.get('ok'):
            return {'ok': False, 'data': []}
        return {'ok': True, 'data': result.get('data', [])}

    def set_leverage(self, symbol: str, leverage: int, mgn_mode: str = 'isolated') -> bool:
        body = {'instId': symbol, 'lever': str(leverage), 'mgnMode': mgn_mode}
        result = self._request('POST', '/api/v5/account/set-leverage', body=body)
        return result.get('ok', False)

    def connect(self) -> bool:
        self._sync_time(force=True)
        for attempt in range(3):
            result = self._request('GET', '/api/v5/account/balance')
            if result.get('ok'):
                self._connected = True
                return True
            time.sleep(2 ** attempt)
        self._connected = False
        return False
