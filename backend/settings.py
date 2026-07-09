"""
Central settings for the BreakoutAI data pipeline.

Everything tunable lives here so the rest of the code stays clean and you can
adjust behaviour without hunting through modules.

MARKET SWITCH: this module reads BREAKOUTAI_MARKET from the environment ONCE
at import time and branches every per-market constant below accordingly. It is
a frozen-at-import singleton -- correct for the one-process-per-invocation model
every script here uses (`BREAKOUTAI_MARKET=US python run_scan.py`), but it means
a single long-lived process can never serve both markets without an explicit
`importlib.reload(settings)`. Don't build that; just don't be surprised by it.
"""
import os
from pathlib import Path

MARKET = os.environ.get("BREAKOUTAI_MARKET", "IN").strip().upper()
if MARKET not in ("IN", "US"):
    raise ValueError(f"Unknown BREAKOUTAI_MARKET={MARKET!r} -- expected 'IN' or 'US'")

# --- Paths -------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent
REPO_DIR = BACKEND_DIR.parent
DATA_DIR = REPO_DIR / "data" / "us" if MARKET == "US" else REPO_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DUCKDB_PATH = DATA_DIR / "market_research.duckdb"   # local research DB (git-ignored)
BREAKOUTS_JSON = DATA_DIR / "breakouts.json"         # output the website reads (committed)

# Ticker suffix yfinance needs: NSE tickers are "<SYMBOL>.NS"; US tickers are bare.
TICKER_SUFFIX = "" if MARKET == "US" else ".NS"
# Relative-strength benchmark (methods.py's Method E) + its display label.
RS_BENCHMARK = "^GSPC" if MARKET == "US" else "^NSEI"          # S&P 500 vs Nifty 50
RS_BENCHMARK_LABEL = "S&P 500" if MARKET == "US" else "Nifty"
# Market Mood Index inputs (market_mood.py, run inside run_scan.py).
VIX_TICKER = "^VIX" if MARKET == "US" else "^INDIAVIX"
HAS_FII_DII_FLOW = MARKET != "US"   # no free public US equivalent of NSE's daily FII/DII feed
# India VIX and CBOE VIX are NOT on the same historical scale -- reusing India's
# calm/panic calibration for CBOE VIX would misjudge US regimes.
MOOD_VIX_CALM = 12.0 if MARKET == "US" else 10.0
MOOD_VIX_PANIC = 35.0 if MARKET == "US" else 30.0
# Entry-guidance display currency (find_breakouts.py's plain-English trigger/stop text).
CURRENCY_SYMBOL = "$" if MARKET == "US" else "₹"
# --- Conviction-score calibration (score.py; thresholds also drive the reliability
# caution text in find_breakouts._reliability_note). Backtested PER MARKET, because the
# two markets' measured Method-A follow-through base rates are far apart: India 38.8%
# (17k+ events) vs US 26.7% (20,814 events, whole-market replay 2026-07-05) -- US
# stocks resolve the fixed ~6% stop/target band within 10 days far less often. Using
# India's 0.39 prior on US stocks systematically oversells every thin-history name.
# US weights were chosen on a 60% train split and held up out-of-sample (test-set
# tertile stratification 14.4% -> 39.4%, p<1e-96; top-decile 43.4%): base depth is the
# strongest validated US feature, trailing reliability keeps a smaller but real role,
# and the method-confirmation term is DROPPED for US (D co-fire measured -12.2pt
# harmful, p=0.002; E2 co-fire lift is subsumed by depth+reliability). India's numbers
# are untouched -- identical to the values shipped 2026-07-04.
if MARKET == "US":
    SCORE_BASE_RATE = 0.27
    SCORE_W_REL, SCORE_W_DEPTH, SCORE_W_METHOD = 0.30, 0.70, 0.00
    SCORE_Q_RANGE = (0.04, 0.85)   # p01/p99 of the blend over 20,814 replayed US events
else:
    SCORE_BASE_RATE = 0.39
    SCORE_W_REL, SCORE_W_DEPTH, SCORE_W_METHOD = 0.60, 0.25, 0.15
    SCORE_Q_RANGE = (0.18, 0.78)
# Reliability-note bands sit +-6pts around the market base rate. For India these land
# on 0.33/0.45 -- exactly the constants that were previously hardcoded, so IN output
# is bit-identical; for US they land on 0.21/0.33.
RELIABILITY_CAUTION_BELOW = round(SCORE_BASE_RATE - 0.06, 2)
RELIABILITY_GOOD_AT = round(SCORE_BASE_RATE + 0.06, 2)

# --- US high-conviction setup tiers (find_breakouts.build_summary; validated
# 2026-07-06 on a train/test split of the whole-market 3y replay — see
# IMPLEMENT_US_HIGH_CONVICTION.md for the numbers each threshold carries).
# NOT validated on India data — do not enable for IN without rerunning the backtest.
HC_ENABLED = MARKET == "US"
HC_ATR_MIN_PCT = 4.5             # 10-day ATR must be >= this % of price ("enough energy")
HC_EXT_MAX_PCT = 3.0             # tier-1 only: close <= this % above the 50d resistance
HC_COFIRE_BARS = 5               # tier-1 only: Method-A breakout within the last N bars (incl today)
HC_MIN_AVG_VOL_SHARES = 100_000  # 20-day avg volume floor (user-chosen; keeps small caps)
HC_MIN_PRICE = 1.0
# Social buzz (fetch_social.py): subreddits + pytrends geo/locale/timezone.
SOCIAL_SUBREDDITS_US = ["wallstreetbets", "stocks", "investing", "StockMarket"]
TRENDS_GEO = "US" if MARKET == "US" else "IN"
TRENDS_HL = "en-US" if MARKET == "US" else "en-IN"
TRENDS_TZ = 300 if MARKET == "US" else 330   # US Eastern vs IST, minutes offset pytrends expects

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

FALLBACK_WATCHLIST_US = {
    "AAPL":  {"name": "Apple Inc.",            "sector": "Technology · Consumer Electronics"},
    "MSFT":  {"name": "Microsoft Corp.",       "sector": "Technology · Software"},
    "NVDA":  {"name": "NVIDIA Corp.",          "sector": "Technology · Semiconductors"},
    "AMZN":  {"name": "Amazon.com Inc.",       "sector": "Consumer Cyclical · Internet Retail"},
    "GOOGL": {"name": "Alphabet Inc.",         "sector": "Communication Services · Internet Content"},
    "META":  {"name": "Meta Platforms Inc.",   "sector": "Communication Services · Internet Content"},
    "TSLA":  {"name": "Tesla Inc.",            "sector": "Consumer Cyclical · Auto Manufacturers"},
    "JPM":   {"name": "JPMorgan Chase & Co.",  "sector": "Financial Services · Banks"},
    "XOM":   {"name": "Exxon Mobil Corp.",     "sector": "Energy · Oil & Gas"},
    "UNH":   {"name": "UnitedHealth Group Inc.", "sector": "Healthcare · Managed Care"},
    "V":     {"name": "Visa Inc.",             "sector": "Financial Services · Payments"},
    "WMT":   {"name": "Walmart Inc.",          "sector": "Consumer Defensive · Discount Stores"},
}
if MARKET == "US":
    FALLBACK_WATCHLIST = FALLBACK_WATCHLIST_US

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

# --- Advisory gates (signals.build_rationale) --------------------------------
# Thresholds for the pass/fail "hard gates" shown in the detail pane's rationale
# layer (Sprint 2, competitor-idea #4). ADVISORY ONLY this pass: they annotate a
# setup, they do NOT cap the tier or change the sort order / conviction (which stay
# governed solely by score.py). Market-aware so IN/US read in native units.
GATE_EARNINGS_VETO_DAYS = 3          # earnings within this many days = a "window" veto flag
GATE_VOL_CONFIRM_MULT = VOL_SURGE_MULT  # breakout-bar volume must clear this ×avg to confirm
# Liquidity floor as average daily TURNOVER (price × 20d avg volume), in native
# currency. US reuses the HC share-count floor (HC_MIN_AVG_VOL_SHARES) scaled to a
# rough dollar turnover; IN uses a ₹1cr/day tradeability floor (cf. MIN_TURNOVER).
GATE_MIN_AVG_TURNOVER = 5_000_000 if MARKET == "US" else 1_00_00_000

# --- Displayed support/resistance zones (levels.py) --------------------------
# These drive the horizontal lines drawn on the annotated chart and the
# "Key Levels" card. Unlike the rolling LOOKBACK_HIGH high/low (which fires on a
# single touch and is only an internal input to is_breakout), these are the
# trader-style zones: swing pivots that price has reversed at repeatedly. Method
# follows the common "3-point rule" (a valid level is touched multiple times),
# weighted by the volume on those touches.
SR_LOOKBACK = 180          # bars of history scanned for pivots (~9 months of daily)
SR_PIVOT_K = 5             # a swing pivot is the local extreme within +/- this many bars
SR_CLUSTER_TOL_PCT = 1.75  # pivots within this % of each other merge into one zone
SR_MIN_TOUCHES = 2         # a zone must be touched at least this many times to be shown
SR_STRONG_TOUCHES = 3      # the article's "3-point rule" — zones at/above this are "confirmed"
SR_MAX_DISTANCE_PCT = 20   # ignore horizontal zones farther than this % from price (a level
                           # the stock hasn't been near in months isn't an actionable line).
                           # For a stock that's run away from all structure, the rising EMA is
                           # reported as *dynamic* support instead (see levels.resolve_display_levels).

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

# --- Performance page (build_performance.py -> data/performance.json) --------
# The feed behind performance.html: a live, forward-only ledger of every
# suggestion the site actually published (from the conviction era onward --
# nothing backfilled), each tracked for the next PERF_TRACK_BARS trading days
# and graded by the same +1R-before-stop rule. See build_performance.py.
PERF_JSON = DATA_DIR / "performance.json"
PERF_TRACK_BARS = FOLLOWTHROUGH_WINDOW   # "~2 weeks": the grade window IS the display window

# --- Alternative breakout-detection methods (research/comparison only — see
# backend/methods.py and analyze_reliability.py; NOT part of run_scan.py / the served
# site). Each is a genuinely different trigger definition from Method A above, graded
# against the SAME followthrough/r_multiple outcome rule (already computed in
# add_indicators) so the comparison is fair: which trigger condition better predicts
# hitting +1R before the stop, not whose own preferred stop is best.
# B — true multi-leg VCP: a sequence of progressively smaller pivot-high-to-trough
# contractions, each on declining volume, then a break above the final pivot high.
VCP_PIVOT_K = 5              # bars each side for a swing pivot (see patterns.find_pivots)
VCP_MIN_LEGS = 2              # need at least this many contracting legs to qualify
VCP_MAX_LOOKBACK_LEGS = 4     # only consider the most recent N legs into a pivot
VCP_BREAKOUT_SEARCH_DAYS = 20 # how many days after the final pivot high to look for the break
VCP_VOL_CONFIRM_MULT = 1.3    # breakout day volume must exceed this multiple of the pre-base average

# C — volatility-squeeze breakout: Bollinger Band width compresses to a multi-month low,
# then expands with a directional close (a la the TTM Squeeze). Orthogonal to A/B: this
# triggers on the volatility regime expanding, not on a specific price level.
SQUEEZE_BB_WINDOW = 20
SQUEEZE_RANGE_LOOKBACK = 120   # trailing days used to judge "near the low end of its own range"
SQUEEZE_POSITION_MAX = 0.15    # band width must sit in the bottom 15% of that trailing range
SQUEEZE_CONFIRM_DAYS = 3       # the squeeze must have been active within this many days of the break
SQUEEZE_VOL_CONFIRM_MULT = 1.3

# D — trend-inception / momentum: +DI crosses above -DI while ADX is rising through a
# threshold and the EMA stack is aligned. Catches the START of a trend, no price level
# (resistance) involved at all.
DI_ADX_THRESHOLD = 20
DI_ADX_RISING_LOOKBACK = 10
# D2 — same DI-cross-up "inception" idea, but loosened: a lower ADX bar and the
# broader `uptrend` filter (already computed in add_indicators) instead of requiring
# the full 4-EMA stack in perfect order. Tests whether D's edge survives with a
# bigger sample or was a strict-filter fluke.
DI_ADX_THRESHOLD_LOOSE = 15

# E — relative-strength breakout: stock-price ÷ benchmark ratio line makes a new N-day
# high, independent of the stock's own absolute chart (classic IBD-style "RS line"
# signal). No longer research-only: the uptrend-gated variant (E2) backs a production
# readiness tier in find_breakouts.build_summary(), via run_scan.py — see methods.py's
# docstring. RS_BENCHMARK itself is set in the market branch at the top of this file
# (^NSEI/^GSPC) — not redefined here, so it isn't silently overwritten back to India's.
RS_LOOKBACK = 50

# F — episodic pivot: a massive gap up on extreme volume (the technical proxy for a
# fundamental-catalyst move, e.g. an earnings surprise). NOTE: this only tests the gap +
# volume shock itself — confirming it against an actual earnings/catalyst calendar needs
# a new data source (no earnings-date feed exists in this pipeline yet), so treat this as
# stage 1 of the method, not the full "confirmed by a known catalyst" definition.
EP_MIN_GAP_PCT = 5.0           # minimum opening gap (%) to count as an episodic pivot
EP_MIN_VOL_MULT = 5.0          # volume must be >= this multiple of the 50-day average (user's 5x-10x floor)
EP_VOL_AVG_WINDOW = 50

# G — comprehensive PRE-breakout composite (Minervini Trend Template + CAN SLIM RS +
# VCP volatility contraction + institutional-accumulation volume reads), requested
# 2026-07-06 to explore for the US market. Unlike A-F above, this is meant to fire
# BEFORE a breakout, not on/after one -- see methods.py's docstring on this method for
# the full write-up of what's included/excluded and why. Sector/market-breadth and
# fundamentals (the user's spec sections 7-8) are deliberately NOT part of the score --
# no point-in-time history for either is cached (only current snapshots), so including
# them would leak today's classification into a 3-year backtest. Their combined 10%
# weight is redistributed proportionally across the six components below that CAN be
# measured honestly from cached OHLCV history alone.
G_SMA_SHORT = 50
G_SMA_MID = 150
G_SMA_LONG = 200
G_SMA_LONG_RISING_LOOKBACK = 20     # ~1 trading month, same idea as EMA200_SLOPE_LOOKBACK
G_MAX_DIST_FROM_52W_HIGH = 5.0      # trend filter: within this % of the 52-week high
G_MIN_DIST_FROM_52W_LOW = 30.0      # trend filter: at least this % above the 52-week low
G_BASE_WINDOW = 50                  # bars examined for base/volume/price-action stats
G_RESISTANCE_TOUCH_PCT = 3.0        # a bar "tests" resistance within this % of it
G_MAX_EXTENSION_PCT = 7.0           # exclude: close already this % above resistance (chasing)
G_ATR_CEILING_PCT = 12.0            # exclude: 10-day ATR this % of price = too erratic to be a coil
G_MAX_6M_RUN_PCT = 100.0            # exclude: already up this % over the last 6 months (parabolic)
G_DISTRIBUTION_VOL_MULT = 1.5       # a down day needs >= this x avg volume to count as "distribution"
G_FIRE_THRESHOLD = 75.0             # composite score (0-100) needed, on top of the trend gate, to fire
# Weights (sum to 100) for the six testable components -- Trend/Base/Volatility/Volume/
# Price/RS/Readiness -- after excluding Sector(5)+Fundamentals(5) from the user's
# original 20/15/15/15/10/10/5/5/5 spec and renormalizing the remaining 90pts to 100.
G_W_TREND, G_W_BASE, G_W_VOLATILITY, G_W_VOLUME, G_W_PRICE, G_W_RS, G_W_READINESS = \
    22, 17, 17, 17, 11, 11, 5

# H — "Pressure Cooker" score: a composite of the specific behaviors discretionary
# momentum traders describe seeing right before the strongest breakouts (contracting
# weekly range, falling ATR, unusually dry volume, higher lows, repeated resistance
# tests without breaking down, closes in the upper half of the daily range, seller
# exhaustion). Gated on the existing `uptrend` column from the start -- the same lesson
# Method E2 already taught (see multi-method-breakout-comparison memory): gating on
# uptrend cost E2 ~0 accuracy vs raw E, so there's no reason to skip it here.
H_WINDOW = 20            # trailing bars most sub-signals look over (~1 trading month)
H_FIRE_THRESHOLD = 75.0

# --- News / sentiment (fetch_news.py -- NOT part of the daily price scan itself; news
# is separately budgeted since all three free providers cap daily requests) ----------
NEWS_JSON = DATA_DIR / "news.json"
# Marketaux free tier: ~100 req/day -- keep headroom for a --check smoke test and
# same-day retries. NewsData.io free tier: 200 credits/day, ~12h delayed -- headroom
# likewise. GNews free tier: 100 req/day, headroom likewise. Finnhub isn't used: its
# free tier is US-only for company news/sentiment (international needs paid Premium),
# so it can't cover NSE/BSE.
NEWS_MARKETAUX_DAILY_BUDGET = 90
NEWS_NEWSDATA_DAILY_BUDGET = 190
NEWS_GNEWS_DAILY_BUDGET = 90
# Google News RSS: no key, no published quota -- these are self-imposed so the daily
# scan stays polite and bounded rather than hammering every remaining stock in one go.
NEWS_RSS_DAILY_BUDGET = 300
NEWS_RSS_MIN_REQUEST_GAP_SEC = 1.0
# Thresholds for sentiment.py's locally-computed compound score (-1..1), applied
# uniformly to headline text regardless of which provider it came from -- see
# sentiment.py and fetch_news.py's docstring for why we stopped trusting Marketaux's
# own (fragile, low-coverage) entity-tagged sentiment.
NEWS_SENTIMENT_BULLISH = 0.15    # compound score >= this -> "Bullish"
NEWS_SENTIMENT_BEARISH = -0.15   # <= this -> "Bearish"; between the two -> "Neutral"
# Only keep genuinely recent news. Marketaux's free search otherwise happily returns
# years-old articles for thinly-covered small/mid-caps -- a 2024 headline next to a live
# breakout is worse than no headline. Enforced at the API query (published_after) so we
# never even cache stale items, and the results are sorted newest-first.
NEWS_MAX_AGE_DAYS = 45

# --- Social buzz (fetch_social.py -- NOT part of the daily price scan itself; also
# separately budgeted). Reddit: mention count + locally-computed sentiment (sentiment.py)
# over post titles/selftext, from a fixed set of India-trading subreddits. Google
# Trends (pytrends, unofficial -- no API key): a 0-100 search-interest score, a cheap
# "how much is this being searched" proxy independent of any single social platform.
SOCIAL_JSON = DATA_DIR / "social.json"
SOCIAL_SUBREDDITS = SOCIAL_SUBREDDITS_US if MARKET == "US" else \
    ["IndianStreetBets", "DalalStreetTalks", "IndiaInvestments", "StockMarketIndia"]
# ApeWisdom (apewisdom.io) -- free, keyless Reddit mention-count aggregator, US-only
# (WSB-centric subreddit coverage). Used instead of the Reddit OAuth path for the US
# market since it needs zero credentials; India still needs REDDIT_CLIENT_ID/SECRET
# (unresolved) since ApeWisdom's subreddit coverage doesn't include Indian markets.
APEWISDOM_FILTER = "wallstreetbets"
APEWISDOM_DAILY_BUDGET = 300
SOCIAL_MENTIONS_TIME_FILTER = "week"   # Reddit search `t` param: hour/day/week/month/year/all
SOCIAL_REDDIT_DAILY_BUDGET = 300       # generous: free OAuth script apps allow ~60 req/min
SOCIAL_BUZZ_LOW = 3     # < this many mentions in the window -> "Low" buzz
SOCIAL_BUZZ_HIGH = 15   # >= this many -> "High" buzz; in between -> "Medium"
TRENDS_DAILY_BUDGET = 150     # self-imposed; Google Trends has no published free quota,
                              # but the unofficial API soft-blocks aggressive callers
TRENDS_MIN_REQUEST_GAP_SEC = 1.5   # spacing between pytrends calls to stay under that radar

# --- Market Mood Index (market_mood.py) -- a single 0-100 market-wide fear/greed
# gauge, NOT a per-stock signal. Runs INSIDE run_scan.py (unlike holdings/sectors/
# fundamentals/news/social, which are separate scripts) because it's cheap -- one
# extra yfinance ticker (India VIX; Nifty itself is already fetched for Method E) plus
# one lightweight NSE API call -- and time-sensitive, so it needs to be fresh every day
# rather than slow-changing reference data. Four independent, equally-weighted
# components; any one that fails to fetch is dropped and the rest reweight
# proportionally (see market_mood.compute_market_mood).
FII_DII_HISTORY_JSON = DATA_DIR / "fii_dii_history.json"
FII_DII_HISTORY_DAYS = 90        # keep this many days on disk; only the trailing
                                  # MOOD_FII_ROLLING_DAYS are actually used
MOOD_TREND_SMA_WINDOW = 20       # benchmark close vs its N-day SMA ("trend strength")
MOOD_TREND_CLAMP_PCT = 10.0      # +/- this % distance from the SMA maps to the full 0-100 range
# MOOD_VIX_CALM / MOOD_VIX_PANIC are set in the market branch at the top of this file
# (India VIX and CBOE VIX aren't on the same historical scale) -- not redefined here.
MOOD_FII_ROLLING_DAYS = 21       # window today's net FII flow is z-scored against
MOOD_FII_CLAMP_Z = 2.0           # a z-score of +/- this many std-devs maps to the full 0-100 range

# --- Indicator windows -------------------------------------------------------
# 8 & 21 are the responsive Fibonacci "momentum/trend" EMAs (catch moves early);
# 50 & 200 are the structural/institutional anchors (macro trend + breakout filter).
# Order matters for display. 50 and 200 must stay in the list (used by the
# sentiment rule and the min-history check).
EMA_WINDOWS = [8, 21, 50, 200]
EMA_LABELS = {8: "Momentum", 21: "Short-term trend", 50: "Structural", 200: "Macro"}
ADX_PERIOD = 14
RSI_PERIOD = 14   # standard Wilder RSI window; feeds the annotated chart's RSI pane
