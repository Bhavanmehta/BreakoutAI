# Data-Source Roadmap — new feeds to improve breakout *quality*

Candidate data sources (from the Fincept connector map) that plausibly improve
**follow-through prediction** — the only thing `score.py` is allowed to rank on.

## Ground rule (do not skip)

Every source below enters the pipeline the same disciplined way ADX/volume/patterns
were tested and *rejected*:

1. **Decorative first** — add as a `confirming`/`risk` gate token in `signals.py`
   (advisory only, never caps tier or reorders).
2. **Test** — wire a hypothesis into `analyze_reliability.py`; check it actually
   stratifies whole-market follow-through (p<0.01, holds out-of-sample).
3. **Promote only if it survives** — only then does it earn a weight in `score.py`.
   Expect some of these (esp. sentiment-ish ones) to fail the test like ADX did.

Two independent constraints per source:
- **Library license** — can we use the wrapper.
- **Data terms** — can we cache/redistribute into `breakouts.json`. For restricted
  vendors (Finnhub/FMP/Polygon) store **derived signals** (`delivery_confirm: true`),
  never the vendor's raw numbers. Public sources (FINRA/SEC/NSE) are safe to cache raw.

Already ingested — do NOT re-add: OHLC + squeeze/VCP/EMA · fundamentals (P-E/growth/
ROE/D-E) · news + VADER · social (Reddit, Google Trends) · quarterly FII/DII/promoter
(NSE SHP-XBRL, India only) · options *flow* (Tradier/Polygon) · EPS est-vs-actual ·
India-VIX mood.

---

## Tier 1 — high ROI, cheap, per-market, backtest-ready

### 1. NSE delivery-% (India)  — accumulation vs. intraday churn  ← DO FIRST
- **Why (follow-through):** high delivery-% on a breakout day = real positioning, not
  day-trade froth. Best *free* India realness filter, and it's daily.
- **Coverage:** India strong. US: n/a (concept doesn't exist).
- **Library / license:** `jugaad-data` (already a dependency) — `sec_bhavdata_full`
  carries `DELIV_PER`; fallback `nsepython` (MIT).
- **Data terms:** free NSE, daily, cacheable raw.
- **Plugs in:** compute in the daily price scan alongside volume; gate `delivery_confirm`.
- **Effort:** Very low — same source we already hit.

### 2. Short interest + days-to-cover / borrow (US) — squeeze fuel
- **Why:** high SI% + breakout = classic continuation accelerant; the US mirror of #1.
- **Coverage:** US strong (FINRA bi-monthly, public domain). India: n/a (SEBI doesn't publish).
- **Library / license:** `finnhub-python` (Apache-2.0) `stock/short-interest`; or FINRA
  files direct via `pandas.read_csv` (no lib, public domain). Real-time = Ortex/S3 (paid, no free lib).
- **Data terms:** FINRA free, ~T+8 lag, cacheable. Finnhub free 60/min (SI may need paid tier).
- **Plugs in:** `data/short_interest.json`, merged by `run_scan` like holdings; gate `squeeze_fuel`.
- **Effort:** Low — mirror `fetch_holdings.py` cadence (bi-monthly).

### 3. Estimate-revision trajectory + analyst upgrade/downgrade flow — PEAD momentum
- **Why:** rising forward-EPS revisions + upgrade clusters are the best-documented driver
  of *sustained* post-breakout continuation. We have the *surprise*, not the *trend of estimates*.
- **Coverage:** US strong; India partial (large caps only — matches earnings.py's ~40–50% coverage note).
- **Library / license:** start free with yfinance (`get_upgrades_downgrades`,
  `recommendations_summary`); scale via `finnhub-python` (Apache-2.0) or `fmpsdk` (MIT).
- **Data terms:** yfinance free; Finnhub/FMP free tiers rate-limited (FMP 250/day).
- **Plugs in:** `data/estimates.json`, merged like earnings; gate `estimate_up` / risk `estimate_cut`.
- **Effort:** Low-med — begin with free yfinance fields we're already authed for.

---

## Tier 2 — good, moderate effort

### 4. Insider transactions — Form 4 (US) / SEBI SAST (IN) — timely smart-money accumulation
- **Why:** cluster insider *buys* precede/sustain Stage-2 breakouts; a *timely* (not
  quarterly-lagged) extension of the holdings thesis.
- **Coverage:** US strong (SEC Form 4, ~2-day lag); India via NSE insider/SAST + bulk/block deals (weaker).
- **Library / license:** `sec-edgar-downloader` (MIT) or `edgartools` (permissive — CONFIRM on PyPI)
  for US; `nsepython`/direct NSE for India.
- **Data terms:** SEC public domain, requires descriptive `User-Agent`, ~10 req/s fair-access; cacheable.
- **Plugs in:** `data/insider.json`; gate `insider_buy` / risk `insider_sell`.
- **Effort:** Medium — Form 4 XML parsing.

### 5. 13F institutional ownership *change* (US) — market parity with India holdings
- **Why:** `holdings.py` gives India accumulation but the US side is blind to institutional
  flow. 13F QoQ change is the closest US mirror; same "confirmed coil" logic.
- **Coverage:** US only (SEC 13F, 45-day lag — slow, like our NSE quarterly data).
- **Library / license:** `edgartools` / `sec-edgar-downloader` (see #4); or Finnhub `institutional-ownership`.
- **Data terms:** SEC public domain; 45-day lag fits the quarterly-merge pattern.
- **Effort:** Medium.

### 6. Per-stock IV-rank / IV-percentile / skew (US) — options-implied confirmation
- **Why:** we have options *flow* but not *implied* structure. Low IV-rank at breakout =
  cheap fuel / room to run; skew flags directional positioning. Enhancement, not a new vendor.
- **Coverage:** US (reuse existing Tradier/Polygon plumbing). India options thin.
- **Library / license:** reuse `tradier_providers` / Polygon (already in repo).
- **Effort:** Low-med — compute IV-rank from data we can already pull.

---

## Tier 3 — test-then-decide (bias: expect these to fail, given the rejection record)

### 7. StockTwits / X retail momentum
- Redundant with Reddit + Trends + news sentiment; sentiment is exactly what failed before.
- `pystocktwits`-style (MIT) / X API (paid). Cheap decorative add only.

### 8. FRED credit spreads / rate-of-change
- `fredapi` (MIT), free key. ⚠️ Index regime (SPX-vs-200dma) was already TESTED and
  REJECTED (-0.5pt, p=0.52). Credit-spread ROC is a different variable but bias toward
  expecting the same result. Lowest priority.

---

## Sequencing

1. **#1 NSE delivery-%** — daily, free, existing dependency, covers India, cleanest
   "is this move real?" test.
2. **#2 US short interest** — its US counterpart → symmetric IN/US realness gate for ~zero cost.
3. **#3 estimate revisions** — highest alpha upside; start with free yfinance fields before paying.

Then Tier 2 as capacity allows. Tier 3 only if bored / a Tier-1/2 idea validates and suggests it.

## Open flags
- Confirm `edgartools` and `finnhub-python` licenses on PyPI before bundling.
- For cached/published `breakouts.json`, data ToS binds, not the wrapper license — store
  derived booleans for restricted vendors, raw only for FINRA/SEC/NSE.
