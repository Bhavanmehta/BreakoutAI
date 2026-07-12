"""Vercel Python serverless function: GET /api/index_ohlc?symbol=^NSEI&market=IN|US

Feeds the market-index detail popup's chart (see combined_breakout_scanner_platform.html's
openIndexDetail()/renderIndexChart()) with enough daily history to draw candles + the same
EMA8/21/50/200 + RSI(14) overlays the per-stock annotated chart shows. Indexes don't get a
precomputed daily OHLC file the way stocks do (export_ohlc.py only covers individual
symbols in ohlcv_features -- see its docstring), so this computes on demand instead.

Deliberately NOT backed by yfinance/pandas, same reasoning as api/quotes.py: keeps this
function's cold start light (Vercel bundles each api/*.py off the root requirements.txt).
Hits Yahoo's public v8 chart endpoint directly and computes EMA/RSI in pure Python below,
using the identical smoothing conventions as backend/find_breakouts.add_indicators (EMA
span=window/adjust=False; Wilder RSI alpha=1/period) so the numbers agree with the per-
stock charts.

Why this used to fail as a TradingView embed instead: NSE/BSE real-time data can't be
redistributed via TradingView's widget outside tradingview.com itself (a licensing
restriction on their end, not a bug here) -- indices always rendered "This symbol is only
available on TradingView" for India. This endpoint sidesteps that entirely.

Usage: GET /api/index_ohlc?symbol=^NSEI&market=IN
       GET /api/index_ohlc?symbol=^GSPC&market=US
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import requests

REQUEST_TIMEOUT = 8
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
# ~2y of raw daily bars gives the 200-day EMA/RSI real runway before we tail down to what's
# actually drawn -- same BARS reasoning as export_ohlc.py, just computed live instead of
# during the nightly scan.
RANGE = "2y"
BARS = 220
EMA_WINDOWS = (8, 21, 50, 200)
RSI_PERIOD = 14


def _ema(values: list[float], window: int) -> list[float | None]:
    """EMA with span=window, adjust=False -- matches pandas' .ewm(span=w, adjust=False)
    used in backend/find_breakouts.py so these numbers line up with the per-stock chart."""
    alpha = 2.0 / (window + 1)
    out, prev = [], None
    for v in values:
        prev = v if prev is None else (v - prev) * alpha + prev
        out.append(round(prev, 2))
    return out


def _rsi(closes: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    """Wilder RSI, same alpha=1/period convention as add_indicators. First value is None
    (no prior close to diff against)."""
    alpha = 1.0 / period
    avg_gain = avg_loss = None
    out: list[float | None] = [None]
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain, loss = max(delta, 0.0), max(-delta, 0.0)
        avg_gain = gain if avg_gain is None else (gain - avg_gain) * alpha + avg_gain
        avg_loss = loss if avg_loss is None else (loss - avg_loss) * alpha + avg_loss
        out.append(100.0 if avg_loss == 0 else round(100 - 100 / (1 + avg_gain / avg_loss), 1))
    return out


def _r(x):
    return None if x is None else round(float(x), 2)


def fetch_index_ohlc(symbol: str) -> dict | None:
    try:
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"interval": "1d", "range": RANGE}, headers=_HEADERS, timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        result = resp.json()["chart"]["result"][0]
        ts = result["timestamp"]
        q = result["indicators"]["quote"][0]
        opens, highs, lows, closes = q["open"], q["high"], q["low"], q["close"]
        vols = q.get("volume") or [None] * len(ts)
    except Exception:
        return None
    if not ts:
        return None

    # Drop any bar with a missing close (e.g. a still-forming intraday bar, or a Yahoo
    # data gap) before computing indicators -- a None mid-series would otherwise corrupt
    # the EMA/RSI running state and confuse the chart library.
    idx = [i for i in range(len(ts)) if closes[i] is not None]
    if not idx:
        return None
    dates = [datetime.fromtimestamp(ts[i], tz=timezone.utc).strftime("%Y-%m-%d") for i in idx]
    opens = [opens[i] for i in idx]
    highs = [highs[i] for i in idx]
    lows = [lows[i] for i in idx]
    closes = [closes[i] for i in idx]
    vols = [vols[i] for i in idx]

    ema = {w: _ema(closes, w) for w in EMA_WINDOWS}
    rsi = _rsi(closes)

    start = max(0, len(dates) - BARS)
    bars = [[dates[i], _r(opens[i]), _r(highs[i]), _r(lows[i]), _r(closes[i])]
            for i in range(start, len(dates))]
    volume = [int(v) if v is not None else None for v in vols[start:]]
    return {
        "symbol": symbol,
        "as_of": dates[-1],
        "bars": bars,
        "volume": volume,
        "ema8": ema[8][start:], "ema21": ema[21][start:],
        "ema50": ema[50][start:], "ema200": ema[200][start:],
        "rsi": rsi[start:],
    }


class handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, status: int, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        symbol = (qs.get("symbol") or [""])[0].strip().upper()
        if not symbol:
            self._send_json(400, {"error": "symbol query param is required"})
            return
        data = fetch_index_ohlc(symbol)
        if data is None:
            self._send_json(502, {"error": "could not fetch index history"})
            return
        self._send_json(200, data)
