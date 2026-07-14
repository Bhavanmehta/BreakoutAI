# Dhan Iron Condor — Session Handoff

Project: `c:\Users\bhava\OneDrive\Documents\GitHub\BreakoutAI\dhan_ironcondor`
Platform: Windows 11, PowerShell primary. Trader = `python main.py`; dashboard = `streamlit run dashboard.py`.

## What this system is
Automated, hedged intraday Iron Condor (NSE/NIFTY options) trader via Dhan.
- `main.py` — async trader loop; owns `runtime/state.json` + `runtime/events.jsonl` writes. Entrypoint: `asyncio.run(Trader().run())`. `MODE` from `config.py` ("paper"/"live"); live needs `DHAN_CLIENT_ID` + `DHAN_ACCESS_TOKEN` env vars.
- `risk.py` — `RiskManager`: condor entry, delta-band breach → roll, hedge-wall, EOD flatten. `to_dict()` builds the dashboard snapshot.
- `dashboard.py` — read-only Streamlit UI; reads `runtime/state.json` (full snapshot) + `runtime/events.jsonl`. Auto-refresh via sidebar.
- `config.py` — strategy constants (WING_WIDTH, TARGET_SHORT_DELTA, MIN_CREDIT_PTS, DELTA_BAND, BREACH_PERSIST_S, MAX_ROLLS_PER_SIDE, DAILY_LOSS_PCT, LOT_SIZE, MAX_LOTS, RISK_FREE_RATE, MARGIN_PER_LOT_PAPER, etc.).

## Work completed this session

### 1. Stopped greeks_sample event flood  ✅ DONE
- `risk.py:205` (`sample_greeks`) — deliberately NO LONGER emits a periodic `greeks_sample` event every 30s. Live net delta already rides in the state.json snapshot. Only genuine state transitions (`breach_started` / `breach_cleared`) are logged now.

### 2. Delta-at-entry capture + snapshot  ✅ DONE (code)
- `risk.py:63` `entry_net_delta` field; captured at entry (`risk.py:179`).
- `risk.py:419-429` `to_dict()` now writes a `"delta"` block:
  - `entry_net_delta`, `current_net_delta` (per-unit, normalized by lots×LOT_SIZE)
  - `entry_delta_rs`, `current_delta_rs` (₹ P&L change per 1-pt NIFTY move = delta × qty)
  - `current_*` are `None` until a condor is live.

### 3. Delta drift dashboard panel  ✅ DONE (code)
- `dashboard.py` — new "📐 Delta drift (entry vs live)" section (after Position P&L, before Condor open). 3 metrics: Delta at entry / Delta now (drift as delta-indicator) / Drift since entry, each with ₹ exposure in help tooltip. Caption explains normalization + `±DELTA_BAND` roll trigger. Graceful fallback when no live delta.
- Verified: `DELTA_BAND` is exposed in `main.py` diagnostics `"config"` block, so caption shows the real value.

### 4. Diagnosed "delta drift empty + 199 rows" (NOT code bugs)
Root cause: **stale runtime files + trader process not running.**
- `state.json` was frozen 5+ min (old format, no `delta` key) → written by pre-edit process.
- `events.jsonl` had 248/251 lines = historical `greeks_sample` (from before the fix). "199" = pandas row index of last-200 window.
- PowerShell process list showed **no `python main.py`** running — only http.servers + streamlit. The restarted "python" never stayed alive.
- **Action taken:** backed up + cleared stale runtime files:
  - Moved `runtime/state.json` and `runtime/events.jsonl` → `runtime/_stale_backup/*.20260713_013251.bak`
  - `runtime/` now has no state/events files; dashboard shows empty until fresh trader writes them.

## PENDING / TODO

1. **Convert event + dashboard timestamps to IST**  — IN PROGRESS (not confirmed complete).
   - Events store epoch `ts`; dashboard renders them. Verify all timestamp displays use IST (`market.now_ist()` / TZ) consistently, incl. events table `ts` column.

2. **Live verification of delta panel + events cleanup**  — pending USER action:
   - Start trader in its own terminal, leave open:
     ```powershell
     cd C:\Users\bhava\OneDrive\Documents\GitHub\BreakoutAI\dhan_ironcondor
     python main.py    # set $env:DHAN_CLIENT_ID / $env:DHAN_ACCESS_TOKEN first if MODE=live
     ```
   - Confirm it stays alive (state.json < ~10s old):
     ```bash
     python -c "import os,time; print(int(time.time()-os.path.getmtime('runtime/state.json')),'s old')"
     ```
   - If trader crashes on startup, capture the terminal traceback and debug.
   - Once fresh: confirm Delta drift panel populates + Recent events shows only meaningful events.

3. (Optional) Delete `runtime/_stale_backup/` once confirmed no longer needed.

## Gotchas
- Only `main.py` writes runtime files; dashboard is read-only. A stale/empty dashboard almost always = trader not running, not a UI bug.
- Trader must run in a persistent terminal (closing the terminal kills it → files go stale).
- `RUNTIME_DIR = Path(__file__).parent / "runtime"` in both main.py and dashboard.py (paths agree).
