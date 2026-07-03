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
    "ETERNAL":   {"name": "Eternal Ltd.",                       "sector": "Consumer Services · Internet Retail"},
    "TCS":       {"name": "Tata Consultancy Services Ltd.",     "sector": "Technology Services · IT Consulting"},
    "RELIANCE":  {"name": "Reliance Industries Ltd.",           "sector": "Energy · Oil & Gas Refining"},
    "INFY":      {"name": "Infosys Ltd.",                       "sector": "Technology Services · IT Consulting"},
    "MOIL":      {"name": "MOIL Ltd.",                          "sector": "Materials · Mining"},
    "TATASTEEL": {"name": "Tata Steel Ltd.",                    "sector": "Materials · Steel"},
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
LOOKBACK_HIGH = 50        # a "breakout" = close above the highest high of the prior N days
VOL_AVG_WINDOW = 20       # window for average volume
VOL_SURGE_MULT = 1.5      # breakout day volume must exceed this multiple of average
ATR_SHORT = 10            # recent volatility window
ATR_LONG = 30             # baseline volatility window (ratio < 1 => "coiling")
RESISTANCE_TOUCH_PCT = 2.0  # a day counts as "touching" resistance if within this % of it
FORWARD_WINDOWS = [5, 10, 20]  # trading days ahead used to score historical breakouts

# --- Indicator windows -------------------------------------------------------
EMA_WINDOWS = [10, 20, 50, 200]
ADX_PERIOD = 14
