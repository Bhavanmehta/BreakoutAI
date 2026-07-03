"""
Central settings for the BreakoutAI data pipeline.

Everything tunable lives here so the rest of the code stays clean and you can
adjust behaviour without hunting through modules.
"""
from pathlib import Path

# --- Paths -------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent
REPO_DIR = BACKEND_DIR.parent
DATA_DIR = REPO_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DUCKDB_PATH = DATA_DIR / "market_research.duckdb"   # local research DB (git-ignored)
BREAKOUTS_JSON = DATA_DIR / "breakouts.json"         # output the website reads (committed)

# --- Universe ------------------------------------------------------------
# Which symbols get scanned. By default this is discovered dynamically every run
# from NSE's own daily bhavcopy (every listed equity's turnover for one trading
# day, via jugaad-data) — see universe.py — rather than hand-typed. Only the top
# UNIVERSE_SIZE names by turnover are kept: a liquidity filter, since thin/illiquid
# names produce noisy, effectively untradeable "breakouts". Discovery costs exactly
# one lightweight NSE request; actual price history still comes from get_prices()
# (yfinance by default), so this does NOT mean 300x more load on NSE's live API.
USE_DYNAMIC_UNIVERSE = True
UNIVERSE_SIZE = None             # top-N NSE equities by turnover; None = whole market (~2000)
UNIVERSE_LOOKBACK_DAYS = 10      # how far back to search for the latest trading day's bhavcopy
# Illiquid floor: skip names whose latest-day turnover is below this (in ₹). Default 0
# = truly the whole market; the data-availability + MIN_HISTORY_BARS gates already drop
# the genuinely-dead names. Raise this (e.g. 1_00_00_000 = ₹1cr/day) if the illiquid
# tail proves too noisy — "breakouts" on a stock trading ₹0.5cr/day are barely tradeable.
MIN_TURNOVER = 0

# Offline fallback if bhavcopy discovery fails (network issue, NSE blocking, etc.) —
# the pipeline should never hard-fail just because universe discovery had a bad day.
# Also the universe when USE_DYNAMIC_UNIVERSE = False.
# symbol -> display name. Symbols are the NSE trading symbols.
# yfinance uses "<SYMBOL>.NS"; jugaad-data uses "<SYMBOL>".
FALLBACK_WATCHLIST = {
    "ETERNAL":    {"name": "Eternal Ltd.",                   "sector": "Consumer Services · Internet Retail"},
    "TCS":        {"name": "Tata Consultancy Services Ltd.", "sector": "Technology Services · IT Consulting"},
    "RELIANCE":   {"name": "Reliance Industries Ltd.",       "sector": "Energy · Oil & Gas Refining"},
    "INFY":       {"name": "Infosys Ltd.",                   "sector": "Technology Services · IT Consulting"},
    "MOIL":       {"name": "MOIL Ltd.",                      "sector": "Materials · Mining"},
    "TATASTEEL":  {"name": "Tata Steel Ltd.",                "sector": "Materials · Steel"},
    "HDFCBANK":   {"name": "HDFC Bank Ltd.",                 "sector": "Financials · Private Bank"},
    "ICICIBANK":  {"name": "ICICI Bank Ltd.",                "sector": "Financials · Private Bank"},
    "BHARTIARTL": {"name": "Bharti Airtel Ltd.",             "sector": "Communications · Telecom"},
    "SUNPHARMA":  {"name": "Sun Pharmaceutical Ind. Ltd.",   "sector": "Health Care · Pharma"},
    "TITAN":      {"name": "Titan Company Ltd.",             "sector": "Consumer Discretionary · Jewellery"},
    "LT":         {"name": "Larsen & Toubro Ltd.",          "sector": "Industrials · Construction"},
}

# --- Data fetch --------------------------------------------------------------
# How many years of daily history to pull for each stock. Needs to comfortably
# exceed the 200-day EMA window and give a few years of breakout history.
HISTORY_YEARS = 3

# Primary price source for the daily scan.
#   "yfinance" -> already split/bonus-adjusted, works from anywhere (incl. CI). Best default.
#   "jugaad"   -> raw NSE data + our own split/bonus adjustment (the full-market path).
PRICE_SOURCE = "yfinance"

# --- Pattern / breakout thresholds ------------------------------------------
# A breakout = close above the prior-N-day high, on a volume surge, WHILE the stock
# is in an uptrend and near its 52-week high. The trend + 52w filters are what
# separate real breakouts from "false breakouts" (bounces in a downtrend).
# Grounded in Minervini's Trend Template, Weinstein Stage 2, and Turtle/Donchian.
LOOKBACK_HIGH = 50        # close must exceed the highest high of the prior N days
VOL_AVG_WINDOW = 20       # window for average volume
VOL_SURGE_MULT = 1.5      # breakout day volume must exceed this multiple of average
ATR_SHORT = 10            # recent volatility window
ATR_LONG = 30             # baseline volatility window (ratio < 1 => "coiling")
RESISTANCE_TOUCH_PCT = 2.0  # a day counts as "touching" resistance if within this % of it
FORWARD_WINDOWS = [5, 10, 20]  # trading days ahead used to score historical breakouts

# Trend filter (Stage 2). Only count breakouts when the stock is actually trending up.
REQUIRE_UPTREND = True
TREND_EMA_LONG = 200          # must be above this EMA, and it must be rising
TREND_EMA_MID = 50            # must also be above this EMA
EMA200_SLOPE_LOOKBACK = 21    # bars used to confirm the long EMA is rising (~1 month)
MAX_DIST_FROM_52W_HIGH = 25.0 # breakout must be within this % of the 52-week high (Minervini)

# Minimum raw trading-day bars before a stock gets a summary card at all. `ema200`
# (an EWM) never actually returns NaN for short histories, so a naive dropna doesn't
# catch newly-listed/demerged names -- widening the universe surfaces these (e.g.
# a fresh spin-off with 15 days of history). Needs enough for the 200-EMA "rising"
# check (TREND_EMA_LONG + its slope lookback) and a genuine 52-week high.
MIN_HISTORY_BARS = max(TREND_EMA_LONG + EMA200_SLOPE_LOOKBACK, 252)

# Follow-through: how we judge whether a past breakout "worked" — did price hit +1R
# (using the same stop shown in the entry guidance) before -1R, within WINDOW trading
# days. R = entry - stop scales per-stock/event automatically, unlike a fixed % target,
# which grades low-vol large-caps as failures and high-beta names as successes regardless
# of whether the setup itself was any good.
FOLLOWTHROUGH_WINDOW = 10
STOP_LOSS_FRACTION = 0.94   # stop = resistance * this (~6% below); defines 1R = entry - stop

# --- Indicator windows -------------------------------------------------------
# 8 & 21 are the responsive Fibonacci "momentum/trend" EMAs (catch moves early);
# 50 & 200 are the structural/institutional anchors (macro trend + breakout filter).
# Order matters for display. 50 and 200 must stay in the list (used by the
# sentiment rule and the min-history check).
EMA_WINDOWS = [8, 21, 50, 200]
EMA_LABELS = {8: "Momentum", 21: "Short-term trend", 50: "Structural", 200: "Macro"}
ADX_PERIOD = 14
