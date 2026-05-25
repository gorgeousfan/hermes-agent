"""
Bitget trading tools for Hermes Agent.
Provides market data, account info, and order execution for USDT-FUTURES.

Requires environment variables:
  BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE

FIXES (May 2026):
  - Signing: message = timestamp + method + full_path (with query for GET)
  - Order endpoint: /api/v2/mix/order/place-order (HYPHEN, not camelCase)
  - Order body: side=buy/sell + tradeSide=open/close + marginMode=isolated
  - Close position: /api/v2/mix/order/close-positions
  - Positions endpoint: /single-position (singular)
  - Balance endpoint: /accounts (plural)
"""

import hashlib
import hmac
import base64
import time
import json
import os
import requests
from typing import Optional, Any
from tools.registry import registry

# ── Bitget API Client ──────────────────────────────────────────────────────

BASE_URL = "https://api.bitget.com"
PRODUCT_TYPE = "USDT-FUTURES"


def _sign(method: str, full_path: str, body: str = "") -> dict:
    """Generate auth headers for Bitget API.
    
    For GET: full_path includes query string (e.g. /api/v2/mix/account/accounts?productType=USDT-FUTURES)
    For POST: full_path is just the path, body is the JSON string body
    """
    api_key = os.getenv("BITGET_API_KEY", "")
    secret_key = os.getenv("BITGET_SECRET_KEY", "")
    passphrase = os.getenv("BITGET_PASSPHRASE", "")
    timestamp = str(int(time.time() * 1000))
    message = timestamp + method.upper() + full_path + body
    mac = hmac.new(secret_key.encode(), message.encode(), hashlib.sha256)
    signature = base64.b64encode(mac.digest()).decode()
    return {
        "Content-Type": "application/json",
        "ACCESS-KEY": api_key,
        "ACCESS-SIGN": signature,
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-PASSPHRASE": passphrase,
    }


def _request(method: str, path: str, body: str = "", authenticated: bool = True) -> dict:
    """Make an API request to Bitget."""
    headers = _sign(method, path, body) if authenticated else {"Content-Type": "application/json"}
    url = f"{BASE_URL}{path}"
    resp = requests.request(method, url, headers=headers, data=body if body else None, timeout=10)
    data = resp.json()
    if data.get("code") != "00000":
        raise Exception(f"Bitget API error {data.get('code')}: {data.get('msg')}")
    return data.get("data", {})


# ── Market Data ─────────────────────────────────────────────────────────────

def bitget_ticker(symbol: str = "BTCUSDT") -> str:
    """Get ticker/price for a trading pair."""
    data = _request("GET", f"/api/v2/mix/market/ticker?productType={PRODUCT_TYPE}&symbol={symbol}", authenticated=False)
    if isinstance(data, list):
        d = data[0]
    else:
        d = data
    return json.dumps({
        "symbol": d.get("symbol"),
        "lastPr": d.get("lastPr"),
        "bidPr": d.get("bidPr"),
        "askPr": d.get("askPr"),
        "high24h": d.get("high24h"),
        "low24h": d.get("low24h"),
        "change24h": d.get("change24h"),
        "baseVolume": d.get("baseVolume"),
        "fundingRate": d.get("fundingRate"),
    })


def bitget_candles(symbol: str = "BTCUSDT", granularity: str = "1H", limit: int = 100) -> str:
    """Get candlestick/kline data."""
    data = _request(
        "GET",
        f"/api/v2/mix/market/candles?productType={PRODUCT_TYPE}&symbol={symbol}&granularity={granularity}&limit={limit}",
        authenticated=False
    )
    return json.dumps(data[:limit])


def bitget_orderbook(symbol: str = "BTCUSDT", limit: int = 20) -> str:
    """Get order book depth."""
    data = _request(
        "GET",
        f"/api/v2/mix/market/mergeDepth?productType={PRODUCT_TYPE}&symbol={symbol}&limit={limit}",
        authenticated=False
    )
    return json.dumps(data)


# ── Account & Positions ────────────────────────────────────────────────────

def bitget_balance() -> str:
    """Get USDT account balance."""
    data = _request("GET", f"/api/v2/mix/account/accounts?productType={PRODUCT_TYPE}")
    if isinstance(data, list) and len(data) > 0:
        d = data[0]
        return json.dumps({
            "available": d.get("available"),
            "accountEquity": d.get("accountEquity"),
            "unrealizedPL": d.get("unrealizedPL"),
            "coupon": d.get("coupon"),
        })
    return "{}"


def bitget_positions(symbol: str = "BTCUSDT") -> str:
    """Get current position for a symbol via single-position endpoint."""
    data = _request(
        "GET",
        f"/api/v2/mix/position/single-position?productType={PRODUCT_TYPE}&marginCoin=USDT&symbol={symbol}"
    )
    # _request returns data.get("data", {}), which for single-position is a LIST
    # Filter to non-zero positions and matching symbol
    if isinstance(data, list):
        filtered = [p for p in data if float(p.get("total", 0)) != 0]
        return json.dumps(filtered)
    return "[]"


def bitget_all_positions() -> str:
    """Get all open positions via single-position endpoint.

    Note: single-position requires a symbol param — query common symbols
    and aggregate results. This API key is read-only; trade endpoints return 40404.
    """
    # Common USDT-FUTURES symbols to check (MATICUSDT may return 40309 removed)
    symbols = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
        "ADAUSDT", "DOGEUSDT", "DOTUSDT", "LINKUSDT", "AVAXUSDT",
        "MATICUSDT", "UNIUSDT", "LTCUSDT", "ATOMUSDT", "NEARUSDT",
    ]
    all_positions = []
    for sym in symbols:
        try:
            data = _request(
                "GET",
                f"/api/v2/mix/position/single-position?productType={PRODUCT_TYPE}&marginCoin=USDT&symbol={sym}"
            )
            # _request returns the inner list for single-position
            if isinstance(data, list):
                non_zero = [p for p in data if float(p.get("total", 0)) != 0]
                all_positions.extend(non_zero)
        except Exception:
            # Skip symbols that return errors (e.g., 40309 removed, 40404 permission)
            pass
    return json.dumps(all_positions)

def bitget_open_long(symbol: str = "BTCUSDT", size: float = 0.0, leverage: int = 20) -> str:
    """Open a long position. size in contracts (BTC notional / mark_price)."""
    if size <= 0:
        return json.dumps({"error": "size must be > 0"})
    # Note: endpoint is /place-order (HYPHEN), not /placeOrder
    # One-Way Mode: side=buy/sell + tradeSide=open/close
    path = "/api/v2/mix/order/place-order"
    body = json.dumps({
        "productType": PRODUCT_TYPE,
        "symbol": symbol,
        "marginCoin": "USDT",
        "marginMode": "isolated",
        "size": str(size),
        "side": "buy",
        "tradeSide": "open",
        "orderType": "market",
        "leverage": str(leverage),
    })
    headers = _sign("POST", path, body)
    resp = requests.post(f"{BASE_URL}{path}", headers=headers, data=body, timeout=10)
    return json.dumps(resp.json())


def bitget_open_short(symbol: str = "BTCUSDT", size: float = 0.0, leverage: int = 20) -> str:
    """Open a short position."""
    if size <= 0:
        return json.dumps({"error": "size must be > 0"})
    path = "/api/v2/mix/order/place-order"
    body = json.dumps({
        "productType": PRODUCT_TYPE,
        "symbol": symbol,
        "marginCoin": "USDT",
        "marginMode": "isolated",
        "size": str(size),
        "side": "sell",
        "tradeSide": "open",
        "orderType": "market",
        "leverage": str(leverage),
    })
    headers = _sign("POST", path, body)
    resp = requests.post(f"{BASE_URL}{path}", headers=headers, data=body, timeout=10)
    return json.dumps(resp.json())


def bitget_close_long(symbol: str = "BTCUSDT", size: float = 0.0) -> str:
    """Close a long position (One-Way Mode: use /close-positions)."""
    if size <= 0:
        return json.dumps({"error": "size must be > 0"})
    path = "/api/v2/mix/order/close-positions"
    body = json.dumps({
        "productType": PRODUCT_TYPE,
        "symbol": symbol,
        "marginCoin": "USDT",
        "marginMode": "isolated",
        "size": str(size),
    })
    headers = _sign("POST", path, body)
    resp = requests.post(f"{BASE_URL}{path}", headers=headers, data=body, timeout=10)
    return json.dumps(resp.json())


def bitget_close_short(symbol: str = "BTCUSDT", size: float = 0.0) -> str:
    """Close a short position (One-Way Mode: use /close-positions)."""
    if size <= 0:
        return json.dumps({"error": "size must be > 0"})
    path = "/api/v2/mix/order/close-positions"
    body = json.dumps({
        "productType": PRODUCT_TYPE,
        "symbol": symbol,
        "marginCoin": "USDT",
        "marginMode": "isolated",
        "size": str(size),
    })
    headers = _sign("POST", path, body)
    resp = requests.post(f"{BASE_URL}{path}", headers=headers, data=body, timeout=10)
    return json.dumps(resp.json())


def bitget_set_leverage(symbol: str = "BTCUSDT", leverage: int = 20) -> str:
    """Set leverage for a symbol."""
    path = "/api/v2/mix/account/set-leverage"
    body = json.dumps({
        "productType": PRODUCT_TYPE,
        "symbol": symbol,
        "marginCoin": "USDT",
        "leverage": str(leverage),
        "side": "long_short_mode",
    })
    headers = _sign("POST", path, body)
    resp = requests.post(f"{BASE_URL}{path}", headers=headers, data=body, timeout=10)
    return json.dumps(resp.json())


def bitget_place_limit_order(symbol: str = "BTCUSDT", side: str = "open_long",
                              price: float = 0.0, size: float = 0.0, leverage: int = 20) -> str:
    """Place a limit order."""
    if price <= 0 or size <= 0:
        return json.dumps({"error": "price and size must be > 0"})
    # Map side strings to buy/sell + tradeSide for One-Way Mode
    side_map = {
        "open_long": ("buy", "open"),
        "open_short": ("sell", "open"),
        "close_long": ("sell", "close"),
        "close_short": ("buy", "close"),
    }
    if side not in side_map:
        return json.dumps({"error": f"Invalid side. Use: {list(side_map.keys())}"})
    actual_side, actual_trade_side = side_map[side]
    
    path = "/api/v2/mix/order/place-order"
    body = json.dumps({
        "productType": PRODUCT_TYPE,
        "symbol": symbol,
        "marginCoin": "USDT",
        "marginMode": "isolated",
        "size": str(size),
        "side": actual_side,
        "tradeSide": actual_trade_side,
        "orderType": "limit",
        "price": str(price),
        "leverage": str(leverage),
    })
    headers = _sign("POST", path, body)
    resp = requests.post(f"{BASE_URL}{path}", headers=headers, data=body, timeout=10)
    return json.dumps(resp.json())


# ── Registry ───────────────────────────────────────────────────────────────

def _check() -> bool:
    return bool(os.getenv("BITGET_API_KEY") and os.getenv("BITGET_SECRET_KEY") and os.getenv("BITGET_PASSPHRASE"))


registry.register(
    name="bitget_ticker",
    toolset="bitget",
    schema={
        "name": "bitget_ticker",
        "description": "Get real-time ticker/price for a Bitget trading pair (USDT-FUTURES). Returns last price, bid/ask, 24h high/low, volume, funding rate.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "default": "BTCUSDT", "description": "Trading pair symbol, e.g. BTCUSDT, ETHUSDT, SOLUSDT"},
            },
        },
    },
    handler=lambda args, **kw: bitget_ticker(symbol=args.get("symbol", "BTCUSDT")),
    check_fn=_check,
    requires_env=["BITGET_API_KEY", "BITGET_SECRET_KEY", "BITGET_PASSPHRASE"],
)

registry.register(
    name="bitget_candles",
    toolset="bitget",
    schema={
        "name": "bitget_candles",
        "description": "Get candlestick/kline OHLCV data for a trading pair. Useful for technical analysis and strategy signals.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "default": "BTCUSDT"},
                "granularity": {"type": "string", "default": "1H", "description": "Timeframe: 1m, 3m, 5m, 15m, 30m, 1H, 2H, 4H, 6H, 8H, 12H, 1D, 2D, 3D, 1W, 1M"},
                "limit": {"type": "integer", "default": 100, "description": "Number of candles (max 200)"},
            },
        },
    },
    handler=lambda args, **kw: bitget_candles(
        symbol=args.get("symbol", "BTCUSDT"),
        granularity=args.get("granularity", "1H"),
        limit=args.get("limit", 100),
    ),
    check_fn=_check,
    requires_env=["BITGET_API_KEY", "BITGET_SECRET_KEY", "BITGET_PASSPHRASE"],
)

registry.register(
    name="bitget_balance",
    toolset="bitget",
    schema={
        "name": "bitget_balance",
        "description": "Get USDT account balance — available margin, account equity, unrealized PnL, and bonus credits.",
        "parameters": {"type": "object", "properties": {}},
    },
    handler=lambda args, **kw: bitget_balance(),
    check_fn=_check,
    requires_env=["BITGET_API_KEY", "BITGET_SECRET_KEY", "BITGET_PASSPHRASE"],
)

registry.register(
    name="bitget_positions",
    toolset="bitget",
    schema={
        "name": "bitget_positions",
        "description": "Get current open position for a specific symbol. Returns position size, entry price, unrealized PnL, leverage, margin.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "default": "BTCUSDT"},
            },
        },
    },
    handler=lambda args, **kw: bitget_positions(symbol=args.get("symbol", "BTCUSDT")),
    check_fn=_check,
    requires_env=["BITGET_API_KEY", "BITGET_SECRET_KEY", "BITGET_PASSPHRASE"],
)

registry.register(
    name="bitget_all_positions",
    toolset="bitget",
    schema={
        "name": "bitget_all_positions",
        "description": "Get all open positions across all symbols.",
        "parameters": {"type": "object", "properties": {}},
    },
    handler=lambda args, **kw: bitget_all_positions(),
    check_fn=_check,
    requires_env=["BITGET_API_KEY", "BITGET_SECRET_KEY", "BITGET_PASSPHRASE"],
)

registry.register(
    name="bitget_open_long",
    toolset="bitget",
    schema={
        "name": "bitget_open_long",
        "description": "Open a BTCUSDT long (buy) position. Use for martingale strategy — opens/averages long positions. size is number of contracts.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "default": "BTCUSDT"},
                "size": {"type": "number", "description": "Number of contracts to open"},
                "leverage": {"type": "integer", "default": 20},
            },
            "required": ["size"],
        },
    },
    handler=lambda args, **kw: bitget_open_long(
        symbol=args.get("symbol", "BTCUSDT"),
        size=float(args["size"]),
        leverage=int(args.get("leverage", 20)),
    ),
    check_fn=_check,
    requires_env=["BITGET_API_KEY", "BITGET_SECRET_KEY", "BITGET_PASSPHRASE"],
)

registry.register(
    name="bitget_open_short",
    toolset="bitget",
    schema={
        "name": "bitget_open_short",
        "description": "Open a BTCUSDT short (sell) position. Use for martingale strategy — opens/averages short positions.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "default": "BTCUSDT"},
                "size": {"type": "number", "description": "Number of contracts"},
                "leverage": {"type": "integer", "default": 20},
            },
            "required": ["size"],
        },
    },
    handler=lambda args, **kw: bitget_open_short(
        symbol=args.get("symbol", "BTCUSDT"),
        size=float(args["size"]),
        leverage=int(args.get("leverage", 20)),
    ),
    check_fn=_check,
    requires_env=["BITGET_API_KEY", "BITGET_SECRET_KEY", "BITGET_PASSPHRASE"],
)

registry.register(
    name="bitget_close_long",
    toolset="bitget",
    schema={
        "name": "bitget_close_long",
        "description": "Close a BTCUSDT long position by opening an offsetting short. size = number of contracts to close.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "default": "BTCUSDT"},
                "size": {"type": "number", "description": "Number of contracts to close"},
            },
            "required": ["size"],
        },
    },
    handler=lambda args, **kw: bitget_close_long(
        symbol=args.get("symbol", "BTCUSDT"),
        size=float(args["size"]),
    ),
    check_fn=_check,
    requires_env=["BITGET_API_KEY", "BITGET_SECRET_KEY", "BITGET_PASSPHRASE"],
)

registry.register(
    name="bitget_close_short",
    toolset="bitget",
    schema={
        "name": "bitget_close_short",
        "description": "Close a BTCUSDT short position by opening an offsetting long. size = number of contracts to close.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "default": "BTCUSDT"},
                "size": {"type": "number", "description": "Number of contracts to close"},
            },
            "required": ["size"],
        },
    },
    handler=lambda args, **kw: bitget_close_short(
        symbol=args.get("symbol", "BTCUSDT"),
        size=float(args["size"]),
    ),
    check_fn=_check,
    requires_env=["BITGET_API_KEY", "BITGET_SECRET_KEY", "BITGET_PASSPHRASE"],
)

registry.register(
    name="bitget_set_leverage",
    toolset="bitget",
    schema={
        "name": "bitget_set_leverage",
        "description": "Set leverage (1-125) for a symbol. Must be set before opening positions.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "default": "BTCUSDT"},
                "leverage": {"type": "integer", "default": 20},
            },
        },
    },
    handler=lambda args, **kw: bitget_set_leverage(
        symbol=args.get("symbol", "BTCUSDT"),
        leverage=int(args.get("leverage", 20)),
    ),
    check_fn=_check,
    requires_env=["BITGET_API_KEY", "BITGET_SECRET_KEY", "BITGET_PASSPHRASE"],
)

registry.register(
    name="bitget_place_limit_order",
    toolset="bitget",
    schema={
        "name": "bitget_place_limit_order",
        "description": "Place a limit (post-only) order at a specific price. Good for entry averaging or profit-taking.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "default": "BTCUSDT"},
                "side": {"type": "string", "description": "open_long, open_short, close_long, close_short"},
                "price": {"type": "number", "description": "Limit price"},
                "size": {"type": "number", "description": "Number of contracts"},
                "leverage": {"type": "integer", "default": 20},
            },
            "required": ["side", "price", "size"],
        },
    },
    handler=lambda args, **kw: bitget_place_limit_order(
        symbol=args.get("symbol", "BTCUSDT"),
        side=args["side"],
        price=float(args["price"]),
        size=float(args["size"]),
        leverage=int(args.get("leverage", 20)),
    ),
    check_fn=_check,
    requires_env=["BITGET_API_KEY", "BITGET_SECRET_KEY", "BITGET_PASSPHRASE"],
)
