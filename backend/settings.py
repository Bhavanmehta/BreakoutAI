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

# --- Watchlist ---------------------------------------------------------------
# symbol -> display name. Symbols are the NSE trading symbols.
# yfinance uses "<SYMBOL>.NS"; jugaad-data uses "<SYMBOL>".
WATCHLIST = {
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

# Follow-through: how we judge whether a past breakout "worked", in plain terms —
# did it gain at least TARGET% within WINDOW trading days.
FOLLOWTHROUGH_TARGET_PCT = 5.0
FOLLOWTHROUGH_WINDOW = 10

# --- Indicator windows -------------------------------------------------------
# 8 & 21 are the responsive Fibonacci "momentum/trend" EMAs (catch moves early);
# 50 & 200 are the structural/institutional anchors (macro trend + breakout filter).
# Order matters for display. 50 and 200 must stay in the list (used by the
# sentiment rule and the min-history check).
EMA_WINDOWS = [8, 21, 50, 200]
EMA_LABELS = {8: "Momentum", 21: "Short-term trend", 50: "Structural", 200: "Macro"}
ADX_PERIOD = 14
