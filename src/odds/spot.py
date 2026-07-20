"""Free crypto spot data — Coinbase Exchange public API (no key, US-friendly)."""
import math
import statistics
import time

import requests

BASE = "https://api.exchange.coinbase.com"
PRODUCTS = {"btc": "BTC-USD", "eth": "ETH-USD", "sol": "SOL-USD", "xrp": "XRP-USD"}
_session = requests.Session()
_session.headers["User-Agent"] = "contest-edge/0.1"


def spot_price(symbol: str) -> float:
    product = PRODUCTS[symbol]
    r = _session.get(f"{BASE}/products/{product}/ticker", timeout=15)
    r.raise_for_status()
    return float(r.json()["price"])


def open_price_at(symbol: str, ts: int) -> float:
    """Open of the 1-minute candle starting at unix ts."""
    product = PRODUCTS[symbol]
    iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts))
    end_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts + 120))
    r = _session.get(f"{BASE}/products/{product}/candles",
                     params={"granularity": 60, "start": iso, "end": end_iso},
                     timeout=15)
    r.raise_for_status()
    candles = r.json()  # [time, low, high, open, close, volume], newest first
    if not candles:
        raise RuntimeError(f"no candle at {iso} for {product}")
    return float(sorted(candles, key=lambda c: c[0])[0][3])


def minute_sigma(symbol: str, minutes: int = 60) -> float:
    """Std dev of 1-minute log returns over the recent window."""
    product = PRODUCTS[symbol]
    now = int(time.time())
    iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - minutes * 60))
    end_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now))
    r = _session.get(f"{BASE}/products/{product}/candles",
                     params={"granularity": 60, "start": iso, "end": end_iso},
                     timeout=15)
    r.raise_for_status()
    closes = [float(c[4]) for c in sorted(r.json(), key=lambda c: c[0])]
    if len(closes) < 10:
        raise RuntimeError("not enough candles for sigma")
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    return statistics.pstdev(rets)
