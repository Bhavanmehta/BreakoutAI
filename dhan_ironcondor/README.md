# dhan_ironcondor

Automated hedged intraday Iron Condor on NIFTY via Dhan API v2.

## Setup

```powershell
cd c:\Users\bhava\OneDrive\Documents\GitHub\BreakoutAI\dhan_ironcondor
pip install -r requirements.txt
```

For live trading only (paper mode needs neither of these):

```powershell
$env:DHAN_CLIENT_ID = "your-client-id"
$env:DHAN_ACCESS_TOKEN = "your-access-token"
```

## Run

```powershell
# paper trader (single asyncio event loop; writes runtime/state.json + runtime/events.jsonl)
python main.py

# dashboard (separate process, read-only, polls runtime/ files)
streamlit run dashboard.py

# checks (no keys, no network)
python black76.py
python replay_smoke.py
```

## Config knobs (config.py)

| Knob | Meaning |
|---|---|
| `RISK_FREE_RATE` | Static rate used in Black-76 pricing/greeks |
| `LOT_SIZE` | **NSE-revisable.** Contracts per lot — verify before live |
| `CAPITAL` | Account capital used for daily-loss-limit sizing |
| `BUFFER` | Capital reserved, not deployed to margin |
| `MAX_LOTS` | Position size cap |
| `WING_WIDTH` | Long-leg distance (points) from short strike |
| `TARGET_SHORT_DELTA` | Target |delta| when selecting short strikes |
| `MIN_CREDIT_PTS` | Minimum acceptable net credit to enter |
| `DELTA_BAND` | Net portfolio delta band before a breach is flagged |
| `BREACH_PERSIST_S` | Seconds a delta breach must persist before rolling |
| `MAX_ROLLS_PER_SIDE` | Max rolls per side before forced flatten |
| `DAILY_LOSS_PCT` | Drawdown vs. day-start equity that forces a flatten |
| `OR_START` / `OR_END` | Opening-range window (IST) |
| `EOD_FLATTEN` | Time-of-day (IST) forced flatten |
| `MODE` | `"paper"` or `"live"` |
| `MARGIN_PER_LOT_PAPER` | Paper-mode margin assumption per lot |

## ⚠️ IMPORTANT

**Keep `MODE = "paper"` in `config.py` until you have personally verified:**
1. `LOT_SIZE` (currently `65`) against the current NSE-published lot size — NSE revises this periodically.
2. `MARGIN_PER_LOT_PAPER` and all margin assumptions against your actual live broker account.

Do not flip `MODE` to `"live"` until both are confirmed.
