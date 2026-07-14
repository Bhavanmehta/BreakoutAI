"""Single flat config for the Iron Condor system. Edit values here, not in code."""
from __future__ import annotations
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Kolkata")

# Which structure this process trades. Set per-process via the STRATEGY env var
# so a condor book and a butterfly book can run side by side, each with its own
# runtime/<strategy>/ dir (state.json, events.jsonl, orders.json). The shared
# scrip-master CSV stays at runtime/ (see instruments.py) -- it's strategy-agnostic.
STRATEGY = os.environ.get("STRATEGY", "condor").strip().lower()
if STRATEGY not in ("condor", "butterfly"):
    raise SystemExit(f"STRATEGY must be 'condor' or 'butterfly', got {STRATEGY!r}")

RISK_FREE_RATE = 0.069  # ponytail: static 91-day T-bill rate; upgrade path = RBI scrape when 5bps matters
LOT_SIZE = 65  # config knob - NSE revises lot size; VERIFY before live
CAPITAL = 100_000
BUFFER = 10_000
MAX_LOTS = 2
WING_WIDTH = 200
TARGET_SHORT_DELTA = 0.20
MIN_CREDIT_PTS = 30
DELTA_BAND = 0.15
BREACH_PERSIST_S = 180
MAX_ROLLS_PER_SIDE = 2
DAILY_LOSS_PCT = 0.03
OR_START = "09:15"
OR_END = "10:15"
EOD_FLATTEN = "15:12"

# Late (post-OR) entry: if the process starts after OR_END (or the range never
# established), allow a delta-anchored condor to be opened any time up to
# EOD_FLATTEN instead of skipping the day. Strikes are anchored on live spot by
# TARGET_SHORT_DELTA (no OR-outside constraint). The OR-width trend filter can't
# run, so it's replaced by a volatility-sanity guard: skip while the day has
# moved more than LATE_ENTRY_MAX_MOVE_PCT vs prev close (proxy for "trending
# hard"); the watcher retries, so entry happens once the move settles.
LATE_ENTRY_ENABLED = True
LATE_ENTRY_MAX_MOVE_PCT = 1.0
MODE = "paper"  # "paper" | "live" -- DO NOT set "live" until lot size + margin verified against your account

MARGIN_PER_LOT_PAPER = 45_000  # fake margin, hedge-benefit assumed baked in

# ----- Short iron butterfly (STRATEGY="butterfly") -----
# Both shorts sit ATM, so the position is delta-neutral at entry but gamma-heavy:
# net delta leaves the condor's tight DELTA_BAND on almost any move. Give the fly
# a wider band so it doesn't thrash, and DISABLE the condor roll workflow (closing
# the "untested vertical" is meaningless when both shorts share the ATM strike) --
# on a persistent breach the fly flattens instead. Hedge-wall, daily-loss and EOD
# flatten still apply unchanged.
BUTTERFLY_DELTA_BAND = 0.35
BUTTERFLY_ROLL_ENABLED = False

__all__ = [n for n in dir() if n.isupper()]

if __name__ == "__main__":
    for name in __all__:
        print(f"{name} = {globals()[name]!r}")
