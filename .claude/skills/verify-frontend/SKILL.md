---
name: verify-frontend
description: Verification bar for any change to combined_breakout_scanner_platform.html — serve locally (never file://), drive the real DOM with Python Playwright, check both India and US when MARKET-branched code is touched, zero console errors. Use before declaring any frontend change done.
---

# Verify a frontend change

The frontend is one 2,600-line vanilla-JS file with no build step and no tests —
the only safety net is actually driving it. "It looks right in the code" has
missed real bugs here (phantom price line, invisible dynamic support, ₹-crore
labels on US stocks). The bar for "done":

## Setup

- Serve from the repo root: `python -m http.server 8000` →
  `http://localhost:8000/combined_breakout_scanner_platform.html`.
  `file://` silently breaks — the page fetches `data/breakouts.json`.
- Drive it with the **Python** `playwright` package (installed). There is no
  Node/chromium-cli in this environment.
- If the change depends on a new JSON field, regenerate first
  (`cd backend; python run_scan.py`) — the frontend reads only
  `data/breakouts.json` / `data/us/breakouts.json`.

## The checklist

1. **Exercise the actual change in the DOM** — click/type/select to the state
   the change affects and assert on element text/attributes, not just a
   screenshot. (E.g. for a new badge: search a symbol that should have it AND
   one that shouldn't.)
2. **Console errors must be zero** — capture them for the whole session, not
   just page load.
3. **Both markets when MARKET-branched code is touched** (`fmtMarketCap`,
   `FUND_FIELDS`, `switchMarket()`-refreshed tooltips, verdict copy, anything
   reading `MARKET`). A change verified only on India has already shipped a US
   currency bug once.
4. **One rich stock + one sparse stock** — much of the page degrades gracefully
   on missing data (`holdings: null`, no analog, no fundamentals); verify the
   change doesn't break the sparse path.
5. **Screenshot at the end** for the user, and open it in their browser if the
   change is visual (per the open-browser-after-changes memory).

## Known page invariants (don't rediscover these)

- Filtering: `applyFilters()` → `currentVisible()` → `renderWatchlist()`; the
  detail pane only changes if the current stock fell out of the filtered list
  (deliberate — typing in search must not yank the selection mid-keystroke).
- Tooltips are ONE body-level `#floatTip` portal positioned by `initTooltips()`
  — never per-card tooltips (cards are `overflow-hidden` and will clip them).
- Sort/filter/radar code keys off `readiness.score` / `readiness.watch` /
  `readiness.signal` enums, not label text — new signals should follow that.
- TradingView chart uses `BSE:` symbols (NSE is walled in the free widget).
