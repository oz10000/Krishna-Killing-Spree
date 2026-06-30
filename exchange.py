# exchange.py
# ============================================================
# CLIENTE OKX V5 — CON ATTACHALGOORDS (VERSIÓN COMPLETA)
# ============================================================
# Basado en el Position Manager certificado.
# Compatible con OKX API V5, Demo y Producción.
# Incluye attachAlgoOrds para TP/SL adjuntos (corregido).
# ============================================================

import hmac
import hashlib
import base64
import time
import json
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union, Tuple, Any

# ============================================================
# UTILIDADES
# ============================================================
def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (ValueError, TypeError):
        return default

# ============================================================
# CLIENTE EXCHANGE — OKX V5
# ============================================================
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
        self._account_mode = None
        self._account_mode_fetched = False

    # ============================================================
    # UTILIDADES DE SÍMBOLOS
    # ============================================================
    def _instrument_id(self, symbol: str) -> str:
        """Convierte símbolo al formato OKX V5 (BTC → BTC-USDT-SWAP)."""
        symbol = symbol.upper().strip()
        if symbol.endswith("-USDT-SWAP"):
            return symbol
        return f"{symbol}-USDT-SWAP"

    # ============================================================
    # AUTENTICACIÓN Y FIRMA
    # ============================================================
    def _iso_timestamp(self) -> str:
        now_ms = int(time.time() * 1000) + self._time_offset
        dt = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _sync_time(self, force: bool = False) -> bool:
        """Sincroniza el tiempo con el servidor OKX."""
        now = time.time()
        if not force and (now - self._last_sync_time) < self._sync_interval:
            return True
        try:
            resp = self.session.get(f"{self.base_url}/api/v5/public/time", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "0":
                    server_ts = int(data['data'][0]['ts'])
                    local_ts = int(time.time() * 1000)
                    self._time_offset = server_ts - local_ts
                    self._last_sync_time = now
                    return True
        except Exception as e:
            pass
        return False

    def _ensure_time_synced(self) -> None:
        if not self._sync_time(force=True):
            raise RuntimeError("No se pudo sincronizar el tiempo con OKX")

    def _get_account_mode(self) -> str:
        """Obtiene el modo de cuenta (net o long_short)."""
        if self._account_mode_fetched:
            return self._account_mode
        resp = self._request("GET", "/api/v5/account/config")
        if resp.get('ok') and resp.get('data'):
            config = resp['data'][0]
            pos_mode = config.get('posMode', 'net_mode')
            self._account_mode = 'long_short' if 'long_short' in pos_mode else 'net'
            self._account_mode_fetched = True
        else:
            self._account_mode = 'net'
            self._account_mode_fetched = True
        return self._account_mode

    def _sign_request(self, method: str, path: str, params: Optional[Dict] = None, body: Optional[Dict] = None) -> Tuple[Dict, str]:
        """Genera firma HMAC-SHA256 para OKX V5 con soporte para query string."""
        self._ensure_time_synced()
        timestamp = self._iso_timestamp()

        if body:
            body_str = json.dumps(body, separators=(",", ":"))
        else:
            body_str = ""

        # Incluir query params en la firma (CRÍTICO)
        if params:
            query = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
            full_path = f"{path}?{query}"
        else:
            full_path = path

        sign_str = timestamp + method + full_path + body_str
        signature = base64.b64encode(
            hmac.new(self.secret_key.encode(), sign_str.encode(), hashlib.sha256).digest()
        ).decode()

        headers = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }
        if self.demo:
            headers["x-simulated-trading"] = "1"

        return headers, body_str

    def _handle_response(self, response: requests.Response) -> Dict:
        """Procesa la respuesta de OKX."""
        try:
            data = response.json()
        except:
            return {"ok": False, "error": "Invalid JSON", "raw": response.text}

        if data.get("code") != "0":
            msg = data.get("msg", "Unknown error")
            if "sMsg" in data:
                msg = data["sMsg"]
            return {"ok": False, "error": msg, "raw": data, "code": data.get("code")}

        return {"ok": True, "data": data.get("data", [])}

    def _request(self, method: str, path: str, params: Optional[Dict] = None, body: Optional[Dict] = None, retry: bool = True) -> Dict:
        """Realiza una petición autenticada a OKX con reintentos."""
        self._ensure_time_synced()
        headers, body_str = self._sign_request(method, path, params, body)
        url = f"{self.base_url}{path}"

        if params:
            import urllib.parse
            query_str = '?' + urllib.parse.urlencode(params)
        else:
            query_str = ''

        try:
            if method == "GET":
                resp = self.session.get(url + query_str, headers=headers, timeout=15)
            else:
                resp = self.session.post(url + query_str, headers=headers, data=body_str, timeout=15)
        except Exception as e:
            return {"ok": False, "error": str(e)}

        result = self._handle_response(resp)

        if not result.get('ok') and retry:
            code = result.get('code', '')
            if code in ['50102', '50111', '50112']:
                self._sync_time(force=True)
                return self._request(method, path, params, body, retry=False)

        return result

    # ============================================================
    # MÉTODOS PÚBLICOS (sin autenticación)
    # ============================================================
    def connect(self) -> bool:
        """Verifica la conexión con OKX."""
        try:
            self._ensure_time_synced()
            resp = self._request("GET", "/api/v5/account/balance")
            if resp.get('ok'):
                self._connected = True
                self._get_account_mode()
                return True
        except Exception:
            pass
        return False

    def get_instrument_info(self, symbol: str) -> Dict:
        """Obtiene información del contrato (ctVal, lotSz, minSz, tick_size)."""
        inst = self._instrument_id(symbol)
        if inst in self._instrument_cache:
            return self._instrument_cache[inst]

        resp = self._request("GET", "/api/v5/public/instruments", params={"instId": inst, "instType": "SWAP"})
        if resp.get('ok') and resp.get('data'):
            info = resp['data'][0]
            result = {
                'tick_size': safe_float(info.get('tickSz')),
                'lot_size': safe_float(info.get('lotSz')),
                'min_sz': safe_float(info.get('minSz')),
                'ct_val': safe_float(info.get('ctVal')),
                'max_leverage': safe_float(info.get('lever')),
            }
            self._instrument_cache[inst] = result
            return result
        return {'tick_size': 0.01, 'lot_size': 0.001, 'min_sz': 0.001, 'ct_val': 0.01, 'max_leverage': 20}

    def get_last_price(self, symbol: str) -> Optional[float]:
        """Obtiene el último precio de un símbolo."""
        inst = self._instrument_id(symbol)
        resp = self._request("GET", "/api/v5/market/ticker", params={"instId": inst})
        if resp.get('ok') and resp.get('data'):
            return safe_float(resp['data'][0].get('last'))
        return None

    def get_mark_price(self, symbol: str) -> Optional[float]:
        """Obtiene el precio de marca de un símbolo."""
        inst = self._instrument_id(symbol)
        resp = self._request("GET", "/api/v5/public/mark-price", params={"instId": inst})
        if resp.get('ok') and resp.get('data'):
            return safe_float(resp['data'][0].get('markPx'))
        return None

    def get_balance(self) -> Dict:
        """Obtiene el balance de la cuenta."""
        if not self._connected:
            return {"ok": False, "error": "No conectado"}
        return self._request("GET", "/api/v5/account/balance")

    def get_positions(self, symbol: Optional[str] = None) -> Dict:
        """Obtiene posiciones abiertas."""
        if not self._connected:
            return {"ok": False, "error": "No conectado"}
        params = {}
        if symbol:
            params["instId"] = self._instrument_id(symbol)
        return self._request("GET", "/api/v5/account/positions", params=params)

    def get_pending_orders(self, symbol: Optional[str] = None) -> Dict:
        """Obtiene órdenes pendientes (market/limit)."""
        if not self._connected:
            return {"ok": False, "error": "No conectado"}
        params = {}
        if symbol:
            params["instId"] = self._instrument_id(symbol)
        return self._request("GET", "/api/v5/trade/orders-pending", params=params)

    def get_pending_algo_orders(self, symbol: Optional[str] = None, ord_type: Optional[str] = None) -> Dict:
        """Obtiene órdenes algorítmicas pendientes (TP/SL/trailing)."""
        if not self._connected:
            return {"ok": False, "error": "No conectado"}
        params = {}
        if symbol:
            params["instId"] = self._instrument_id(symbol)
        if ord_type:
            params["ordType"] = ord_type
        return self._request("GET", "/api/v5/trade/orders-algo-pending", params=params)

    def get_all_pending_algo_orders(self, symbol: Optional[str] = None) -> Dict:
        """Obtiene todas las órdenes algorítmicas pendientes."""
        all_orders = []
        for ord_type in ["trigger", "oco", "move_order_stop"]:
            resp = self.get_pending_algo_orders(symbol, ord_type)
            if resp.get('ok'):
                all_orders.extend(resp.get('data', []))
        return {"ok": True, "data": all_orders}

    # ============================================================
    # ÓRDENES NORMALES
    # ============================================================
    def place_market_order(self, symbol: str, side: str, size: float) -> Dict:
        """Coloca una orden de mercado."""
        if not self._connected:
            return {"ok": False, "error": "No conectado"}
        inst = self._instrument_id(symbol)
        pos_side = "long" if side.lower() == "buy" else "short"
        body = {
            "instId": inst,
            "tdMode": "cross",
            "side": side.lower(),
            "posSide": pos_side,
            "ordType": "market",
            "sz": str(size),
        }
        return self._request("POST", "/api/v5/trade/order", body=body)

    def place_limit_order(self, symbol: str, side: str, price: float, size: float) -> Dict:
        """Coloca una orden límite."""
        if not self._connected:
            return {"ok": False, "error": "No conectado"}
        inst = self._instrument_id(symbol)
        pos_side = "long" if side.lower() == "buy" else "short"
        body = {
            "instId": inst,
            "tdMode": "cross",
            "side": side.lower(),
            "posSide": pos_side,
            "ordType": "limit",
            "px": str(price),
            "sz": str(size),
        }
        return self._request("POST", "/api/v5/trade/order", body=body)

    # ============================================================
    # 🔥 ÓRDENES CON TP/SL ADJUNTOS (ATTACHALGOORDS)
    # ============================================================
    def place_market_order_with_tp_sl(self, symbol: str, side: str, size: float,
                                      tp_price: float, sl_price: float) -> Dict:
        """
        Coloca una orden de mercado con TP/SL adjuntos usando attachAlgoOrds.
        🔥 Esta es la versión CORREGIDA que OKX acepta.
        """
        if not self._connected:
            return {"ok": False, "error": "No conectado"}
        inst = self._instrument_id(symbol)
        pos_side = "long" if side.lower() == "buy" else "short"

        # Construir attachAlgoOrds (TP y SL adjuntos)
        attach_algo_ords = []
        if tp_price and tp_price > 0:
            attach_algo_ords.append({
                "tpTriggerPx": str(tp_price),
                "tpOrdPx": "-1",
                "tpTriggerPxType": "last"
            })
        if sl_price and sl_price > 0:
            attach_algo_ords.append({
                "slTriggerPx": str(sl_price),
                "slOrdPx": "-1",
                "slTriggerPxType": "last"
            })

        body = {
            "instId": inst,
            "tdMode": "cross",
            "side": side.lower(),
            "posSide": pos_side,
            "ordType": "market",
            "sz": str(size),
            "attachAlgoOrds": attach_algo_ords
        }
        return self._request("POST", "/api/v5/trade/order", body=body)

    def place_limit_order_with_tp_sl(self, symbol: str, side: str, price: float, size: float,
                                     tp_price: float, sl_price: float) -> Dict:
        """
        Coloca una orden límite con TP/SL adjuntos usando attachAlgoOrds.
        """
        if not self._connected:
            return {"ok": False, "error": "No conectado"}
        inst = self._instrument_id(symbol)
        pos_side = "long" if side.lower() == "buy" else "short"

        attach_algo_ords = []
        if tp_price and tp_price > 0:
            attach_algo_ords.append({
                "tpTriggerPx": str(tp_price),
                "tpOrdPx": str(price),
                "tpTriggerPxType": "last"
            })
        if sl_price and sl_price > 0:
            attach_algo_ords.append({
                "slTriggerPx": str(sl_price),
                "slOrdPx": str(price),
                "slTriggerPxType": "last"
            })

        body = {
            "instId": inst,
            "tdMode": "cross",
            "side": side.lower(),
            "posSide": pos_side,
            "ordType": "limit",
            "px": str(price),
            "sz": str(size),
            "attachAlgoOrds": attach_algo_ords
        }
        return self._request("POST", "/api/v5/trade/order", body=body)

    # ============================================================
    # ÓRDENES ALGORÍTMICAS (POST /api/v5/trade/order-algo)
    # ============================================================
    def place_conditional_order(self, symbol: str, side: str, size: float,
                                trigger_price: float, order_price: float = -1,
                                trigger_px_type: str = "last", pos_side: Optional[str] = None) -> Dict:
        """Coloca una orden condicional (TP/SL individual)."""
        if not self._connected:
            return {"ok": False, "error": "No conectado"}
        inst = self._instrument_id(symbol)
        if pos_side is None:
            pos_side = "long" if side.lower() == "sell" else "short"

        body = {
            "instId": inst,
            "tdMode": "cross",
            "side": side.lower(),
            "ordType": "trigger",
            "sz": str(size),
            "triggerPx": str(trigger_price),
            "orderPx": str(order_price),
            "triggerPxType": trigger_px_type,
        }
        if self._get_account_mode() == "long_short":
            body["posSide"] = pos_side

        return self._request("POST", "/api/v5/trade/order-algo", body=body)

    def place_oco_order(self, symbol: str, side: str, size: float,
                        tp_trigger: float, tp_price: float,
                        sl_trigger: float, sl_price: float,
                        tp_trigger_px_type: str = "last",
                        sl_trigger_px_type: str = "last") -> Dict:
        """Coloca una orden OCO (One-Cancels-Other)."""
        if not self._connected:
            return {"ok": False, "error": "No conectado"}
        inst = self._instrument_id(symbol)

        body = {
            "instId": inst,
            "tdMode": "cross",
            "side": side.lower(),
            "ordType": "oco",
            "sz": str(size),
            "tpTriggerPx": str(tp_trigger),
            "tpOrdPx": str(tp_price),
            "tpTriggerPxType": tp_trigger_px_type,
            "slTriggerPx": str(sl_trigger),
            "slOrdPx": str(sl_price),
            "slTriggerPxType": sl_trigger_px_type,
        }
        if self._get_account_mode() == "long_short":
            pos_side = "long" if side.lower() == "sell" else "short"
            body["posSide"] = pos_side

        return self._request("POST", "/api/v5/trade/order-algo", body=body)

    def place_trailing_order(self, symbol: str, side: str, size: float,
                             callback_ratio: float, trigger_px_type: str = "last",
                             pos_side: Optional[str] = None) -> Dict:
        """Coloca un trailing stop nativo de OKX."""
        if not self._connected:
            return {"ok": False, "error": "No conectado"}
        inst = self._instrument_id(symbol)
        if pos_side is None:
            pos_side = "long" if side.lower() == "sell" else "short"

        body = {
            "instId": inst,
            "tdMode": "cross",
            "side": side.lower(),
            "ordType": "move_order_stop",
            "sz": str(size),
            "callbackRatio": str(callback_ratio),
            "triggerPxType": trigger_px_type,
        }
        if self._get_account_mode() == "long_short":
            body["posSide"] = pos_side

        return self._request("POST", "/api/v5/trade/order-algo", body=body)

    # ============================================================
    # CANCELACIÓN Y CIERRE
    # ============================================================
    def cancel_order(self, order_id: str, symbol: str) -> Dict:
        """Cancela una orden activa."""
        if not self._connected:
            return {"ok": False, "error": "No conectado"}
        inst = self._instrument_id(symbol)
        body = {"ordId": order_id, "instId": inst}
        return self._request("POST", "/api/v5/trade/cancel-order", body=body)

    def cancel_algo_order(self, algo_id: str, symbol: str) -> Dict:
        """Cancela una orden algorítmica."""
        if not self._connected:
            return {"ok": False, "error": "No conectado"}
        inst = self._instrument_id(symbol)
        body = [{"algoId": algo_id, "instId": inst}]
        return self._request("POST", "/api/v5/trade/cancel-algos", body=body)

    def cancel_all_orders(self, symbol: Optional[str] = None) -> int:
        """Cancela todas las órdenes (opcionalmente por símbolo)."""
        if not self._connected:
            return 0
        count = 0

        pending = self.get_pending_orders(symbol)
        if pending.get('ok'):
            for order in pending.get('data', []):
                if self.cancel_order(order.get('ordId'), order.get('instId')):
                    count += 1

        algo = self.get_all_pending_algo_orders(symbol)
        if algo.get('ok'):
            for order in algo.get('data', []):
                if self.cancel_algo_order(order.get('algoId'), order.get('instId')):
                    count += 1

        return count

    def close_position_market(self, symbol: str, side: str, size: float) -> Dict:
        """Cierra una posición con orden de mercado."""
        close_side = "sell" if side == "long" else "buy"
        return self.place_market_order(symbol, close_side, size)

    def close_all_positions(self) -> int:
        """Cierra todas las posiciones abiertas."""
        if not self._connected:
            return 0
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

    def set_leverage(self, symbol: str, leverage: int, mgn_mode: str = 'isolated') -> bool:
        """Establece apalancamiento para un símbolo."""
        if not self._connected:
            return False
        inst = self._instrument_id(symbol)
        body = {"instId": inst, "lever": str(leverage), "mgnMode": mgn_mode}
        result = self._request("POST", "/api/v5/account/set-leverage", body=body)
        return result.get('ok', False)
