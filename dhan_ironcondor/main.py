"""Trader process. Single asyncio event loop. WS ticks mutate the one shared
MarketState. Hedge-wall check runs inline in the tick callback. Everything
else runs on its own timer:
  - greeks sampler       every 30s
  - risk manager loop    every 5 min
  - telemetry flush      every 1 min

Two integration points are left as explicit TODOs because they depend on
account-specific data the spec doesn't provide: the Dhan instrument/security
-id map (needed to build the WS subscription list and to place live orders)
and a previous-close fetch. Paper mode with a populated instrument list works
end to end; wire those two before flipping MODE to "live".
"""
from __future__ import annotations
import asyncio
import json
import os
import time
from datetime import time as dtime
from pathlib import Path

from config import (MODE, STRATEGY, OR_START, OR_END, EOD_FLATTEN, CAPITAL, MAX_LOTS, LOT_SIZE,
                    WING_WIDTH, TARGET_SHORT_DELTA, MIN_CREDIT_PTS, DELTA_BAND,
                    BREACH_PERSIST_S, MAX_ROLLS_PER_SIDE, DAILY_LOSS_PCT,
                    RISK_FREE_RATE, MARGIN_PER_LOT_PAPER,
                    LATE_ENTRY_ENABLED, LATE_ENTRY_MAX_MOVE_PCT,
                    BUTTERFLY_DELTA_BAND, BUTTERFLY_ROLL_ENABLED)
from feed import MarketState, DhanFeed
from execution import PaperBroker, DhanBroker
from risk import RiskManager, time_to_expiry_years
from strategy import Leg, select_condor, select_butterfly, entry_filters, late_entry_ok
from instruments import download_master, load_universe, fetch_prev_close

# Structure this process trades, resolved once from config.STRATEGY. Each strategy
# gets its own selector, delta band and roll policy, plus an isolated runtime dir
# so a condor book and a butterfly book can run as two processes without clobbering
# each other's state.json / events.jsonl / orders.json.
if STRATEGY == "butterfly":
    SELECT_STRUCTURE = select_butterfly
    ACTIVE_DELTA_BAND = BUTTERFLY_DELTA_BAND
    ROLL_ENABLED = BUTTERFLY_ROLL_ENABLED
else:
    SELECT_STRUCTURE = select_condor
    ACTIVE_DELTA_BAND = DELTA_BAND
    ROLL_ENABLED = True

RUNTIME_DIR = Path(__file__).parent / "runtime" / STRATEGY
STATE_FILE = RUNTIME_DIR / "state.json"
EVENTS_FILE = RUNTIME_DIR / "events.jsonl"


def _atomic_write_json(path: Path, data) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    os.replace(tmp, path)


def on_event(event: dict) -> None:
    """Append-only event log. Not the full-snapshot atomic path (that's
    state.json) -- a single appended line can't corrupt prior lines."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with EVENTS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, default=str) + "\n")


def _parse_hhmm(s: str) -> dtime:
    h, m = (int(x) for x in s.split(":"))
    return dtime(h, m)


def _in_or_window(now) -> bool:
    return _parse_hhmm(OR_START) <= now.time() < _parse_hhmm(OR_END)


class Trader:
    def __init__(self) -> None:
        self.state = MarketState()
        client_id = os.environ.get("DHAN_CLIENT_ID", "")
        access_token = os.environ.get("DHAN_ACCESS_TOKEN", "")

        if MODE == "live":
            if not client_id or not access_token:
                raise RuntimeError("MODE=live requires DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN env vars")
            self.broker = DhanBroker(client_id, access_token, self._security_id_lookup)
        else:
            self.broker = PaperBroker(lambda: self.state.chain)

        # Build the tradeable universe from the scrip master (FUT underlying +
        # option chain for the current weekly expiry, windowed around ATM).
        self._universe = self._build_universe()
        self.state.underlying_secid = self._universe.underlying_security_id
        self.state.secid_to_option = self._universe.secid_to_option
        self.state.expiry = self._universe.expiry
        self.state.prev_close = self._load_prev_close()

        self.feed = DhanFeed(client_id, access_token,
                             instruments=self._universe.subscription_list())
        self.risk = RiskManager(market=self.state, broker=self.broker, on_event=on_event,
                                lots=MAX_LOTS, select_structure=SELECT_STRUCTURE,
                                delta_band=ACTIVE_DELTA_BAND, roll_enabled=ROLL_ENABLED)
        self._entry_attempted = False

    def _build_universe(self):
        download_master()
        # Center the subscribed strike window on last known price. prev_close is
        # the cheapest hint available before the socket delivers a live FUT tick.
        hint = self._env_prev_close()
        uni = load_universe(spot_hint=hint, strike_window=40)
        if uni.lot_size != LOT_SIZE:
            print(f"WARNING: scrip master lot size {uni.lot_size} != config.LOT_SIZE "
                  f"{LOT_SIZE}. Paper qty/margin use config.LOT_SIZE -- reconcile config.py.")
        print(f"universe: FUT secid={uni.underlying_security_id} expiry={uni.expiry} "
              f"lot={uni.lot_size} strikes={len(uni.options)} "
              f"(subscribing {len(uni.subscription_list())} instruments)")
        return uni

    @staticmethod
    def _env_prev_close():
        raw = os.environ.get("NIFTY_FUT_PREV_CLOSE", "").strip()
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    def _load_prev_close(self):
        """Previous close of the FUT (the gap filter compares it to today's
        open). Fetched automatically via Dhan's REST quote API using the same
        creds the feed uses. A manual NIFTY_FUT_PREV_CLOSE env var overrides the
        fetch (escape hatch if the API path needs a tweak). If neither yields a
        value, entry is SKIPPED rather than trading on a guessed close."""
        override = self._env_prev_close()
        if override is not None:
            print(f"prev_close: {override} (from NIFTY_FUT_PREV_CLOSE override)")
            return override

        client_id = os.environ.get("DHAN_CLIENT_ID", "")
        access_token = os.environ.get("DHAN_ACCESS_TOKEN", "")
        if client_id and access_token:
            try:
                pc = fetch_prev_close(client_id, access_token,
                                      self._universe.underlying_security_id,
                                      self._universe.underlying_segment_key)
                if pc:
                    print(f"prev_close: {pc} (auto-fetched from Dhan quote API)")
                    return pc
                print("WARNING: Dhan quote API returned no close for the FUT.")
            except Exception as e:
                print(f"WARNING: prev_close auto-fetch failed ({e!r}).")

        print("WARNING: no prev_close -> gap filter can't run -> entry will be "
              "SKIPPED today. Set NIFTY_FUT_PREV_CLOSE=<FUT prior settle> to override.")
        return None

    def _security_id_lookup(self, leg: Leg) -> str:
        sid = self._universe.option_to_secid.get((leg.strike, leg.opt_type))
        if sid is None:
            raise KeyError(f"no security_id for leg strike={leg.strike} {leg.opt_type} "
                           f"(expiry {self._universe.expiry}) -- outside subscribed window?")
        return sid

    # ---------- tick callback: hedge-wall check happens HERE, inline ----------

    def on_tick(self, state: MarketState) -> None:
        now = state.now_ist()
        if _in_or_window(now):
            if state.open_price is None:
                state.open_price = state.spot
            state.or_high = state.spot if state.or_high is None else max(state.or_high, state.spot)
            state.or_low = state.spot if state.or_low is None else min(state.or_low, state.spot)
        elif state.or_high is not None and state.or_low is not None and not state.or_established:
            state.or_established = True
        self.risk.on_tick_hedge_check()  # one float comparison, no timer

    # ---------- timers ----------

    async def greeks_sampler(self) -> None:
        while True:
            await asyncio.sleep(30)
            self.risk.sample_greeks()

    async def risk_loop(self) -> None:
        while True:
            await asyncio.sleep(300)
            self.risk.risk_loop(current_equity=self._estimate_equity())

    def _flush_state(self) -> None:
        snapshot = self.risk.to_dict()
        snapshot["flushed_at"] = time.time()
        snapshot["mode"] = MODE
        snapshot["diagnostics"] = self._build_diagnostics()
        _atomic_write_json(STATE_FILE, snapshot)

    async def telemetry_flush(self) -> None:
        # Flush once immediately, then every 60s. Without the upfront write the
        # dashboard sat on a "No state.json yet -- is main.py running?" warning
        # for the first minute of every launch even though the book was healthy;
        # state.json only appeared on the first post-sleep flush.
        while True:
            self._flush_state()
            await asyncio.sleep(60)

    async def entry_watcher(self) -> None:
        """Polls for OR-window close rather than sleeping until 10:15 so a late
        process start still behaves. Once the OR window is past there are three
        outcomes: (1) range established -> one-shot OR-breakout entry; (2) range
        missed but LATE_ENTRY_ENABLED -> retry a delta-anchored entry every poll
        until it fills or EOD; (3) range missed and late entry disabled -> skip
        the day cleanly."""
        while True:
            await asyncio.sleep(5)
            if self._entry_attempted:
                continue
            now = self.state.now_ist()
            if self.state.or_established:
                # OR breakout: one-shot, resolves the day either way.
                self._entry_attempted = True
                self.risk.try_enter()
            elif now.time() >= _parse_hhmm(OR_END):
                # Late start: the opening range can never establish now.
                if not LATE_ENTRY_ENABLED:
                    self._entry_attempted = True
                    self.risk.skip_day(f"opening range missed (started after "
                                       f"{OR_END}); late entry disabled")
                    print(f"Opening range missed (started after {OR_END}); "
                          f"skipping entry for today.")
                elif now.time() >= _parse_hhmm(EOD_FLATTEN):
                    # Ran out the session without a qualifying late entry.
                    self._entry_attempted = True
                    self.risk.skip_day(f"opening range missed and no qualifying "
                                       f"late entry before {EOD_FLATTEN}")
                    print(f"Opening range missed and no late entry before "
                          f"{EOD_FLATTEN}; skipping entry for today.")
                else:
                    # Delta-anchored late entry is retryable: only latch the
                    # attempt once it actually resolves the day (fill or halt).
                    if self.risk.try_enter_late():
                        self._entry_attempted = True

    def _estimate_equity(self) -> float:
        # TODO(live): wire real mark-to-market from open leg LTPs + realized_pnl.
        return CAPITAL + self.risk.realized_pnl

    def _build_diagnostics(self) -> dict:
        """Read-only view of what the entry logic currently sees, for the
        dashboard: live status of each entry filter and the condor we WOULD sell
        right now. Wrapped so any error here never blocks the state flush."""
        try:
            m = self.state
            now = m.now_ist()
            spot = m.spot or 0.0
            open_px, prev = m.open_price, m.prev_close
            oh, ol = m.or_high, m.or_low

            # Live status of the three entry filters (same thresholds as
            # strategy.entry_filters). None value => input not available yet.
            gap = (abs(open_px - prev) / prev * 100.0) if (open_px is not None and prev) else None
            orw = ((oh - ol) / open_px * 100.0) if (oh is not None and ol is not None and open_px) else None
            drift = (abs(spot - open_px) / open_px * 100.0) if open_px else None
            filters = [
                {"name": "Gap: open vs prev close", "value_pct": gap, "limit_pct": 0.5,
                 "pass": gap is not None and gap <= 0.5},
                {"name": "Opening-range width", "value_pct": orw, "limit_pct": 0.75,
                 "pass": orw is not None and orw <= 0.75},
                {"name": "Drift: spot vs open", "value_pct": drift, "limit_pct": 0.5,
                 "pass": drift is not None and drift <= 0.5},
            ]
            # Where are we relative to the opening-range window? A late process
            # start (after OR_END with no ticks captured) can never establish the
            # range, so it must be reported honestly as MISSED rather than PENDING.
            if bool(m.or_established):
                win_state = "closed"
            elif _in_or_window(now):
                win_state = "forming"
            elif now.time() < _parse_hhmm(OR_START):
                win_state = "before"
            else:
                win_state = "missed"

            if win_state == "closed" and open_px is not None and prev is not None:
                ok, reason = entry_filters(open_px, prev, spot, oh, ol)
                verdict = {"status": "PASS" if ok else "FAIL", "reason": reason}
            elif win_state == "missed":
                if LATE_ENTRY_ENABLED:
                    # No opening range to lean on, so the OR filters don't apply;
                    # the only gate is the volatility-sanity check on spot.
                    lok, lreason = (late_entry_ok(spot, prev, LATE_ENTRY_MAX_MOVE_PCT)
                                    if (spot > 0 and prev) else (False, "no spot/prev close yet"))
                    verdict = {"status": "PASS" if lok else "PENDING",
                               "reason": (f"opening range MISSED -- delta-anchored late "
                                          f"entry active. {lreason}")}
                else:
                    verdict = {"status": "SKIP",
                               "reason": (f"opening range MISSED -- bot was not running during "
                                          f"{OR_START}-{OR_END}, so no range was captured; no entry "
                                          f"today. Start before {OR_START} to trade this session.")}
            elif win_state == "before":
                verdict = {"status": "PENDING",
                           "reason": f"before opening-range window (starts {OR_START})"}
            else:  # forming
                verdict = {"status": "PENDING",
                           "reason": f"opening range still forming; evaluated after {OR_END}"}

            # The condor the selector would pick right now. Before the OR closes
            # this is provisional (centered on live spot); after, it's the real
            # candidate (scanned outside OR high/low).
            T = time_to_expiry_years(now)
            if m.or_established:
                basis = "OR levels"
            elif win_state == "missed":
                basis = "live spot (delta-anchored late entry)"
            else:
                basis = "live spot (provisional -- OR not closed)"
            cond = {"available": False, "reason": "no spot/chain yet", "basis": basis}
            if spot > 0 and getattr(m, "chain", None) and (m.chain.calls or m.chain.puts):
                use_oh = oh if m.or_established else spot
                use_ol = ol if m.or_established else spot
                spec, why = SELECT_STRUCTURE(m.chain, use_oh, use_ol, spot, T, RISK_FREE_RATE)
                if spec:
                    cond = {"available": True, "basis": basis,
                            "short_call_k": spec.short_call_k, "long_call_k": spec.long_call_k,
                            "short_put_k": spec.short_put_k, "long_put_k": spec.long_put_k,
                            "net_credit": spec.net_credit, "min_credit_req": MIN_CREDIT_PTS,
                            "short_call_delta": spec.short_call_delta,
                            "short_put_delta": spec.short_put_delta}
                else:
                    cond = {"available": False, "reason": why, "basis": basis}

            # Dashboard-facing state. A missed range while late entry is enabled is
            # not a dead session -- report it as "late" so the UI shows the active
            # delta-anchored path rather than a terminal "MISSED".
            disp_state = ("late" if (win_state == "missed" and LATE_ENTRY_ENABLED)
                          else win_state)
            return {
                "now_ist": now.strftime("%H:%M:%S"),
                "or_window": {"start": OR_START, "end": OR_END,
                              "in_window": _in_or_window(now), "established": bool(m.or_established),
                              "state": disp_state},
                "eod_flatten": EOD_FLATTEN,
                "entry_attempted": self._entry_attempted,
                "levels": {"spot_fut": spot, "prev_close": prev, "open": open_px,
                           "or_high": oh, "or_low": ol},
                "filters": filters,
                "verdict": verdict,
                "prospective_condor": cond,
                "expiry": str(getattr(m, "expiry", "")),
                "days_to_expiry": round(T * 365, 2),
                "config": {
                    "STRATEGY": STRATEGY, "ROLL_ENABLED": ROLL_ENABLED,
                    "WING_WIDTH": WING_WIDTH, "TARGET_SHORT_DELTA": TARGET_SHORT_DELTA,
                    "MIN_CREDIT_PTS": MIN_CREDIT_PTS, "DELTA_BAND": ACTIVE_DELTA_BAND,
                    "BREACH_PERSIST_S": BREACH_PERSIST_S, "MAX_ROLLS_PER_SIDE": MAX_ROLLS_PER_SIDE,
                    "DAILY_LOSS_PCT": DAILY_LOSS_PCT, "MAX_LOTS": MAX_LOTS, "LOT_SIZE": LOT_SIZE,
                    "MARGIN_PER_LOT_PAPER": MARGIN_PER_LOT_PAPER, "RISK_FREE_RATE": RISK_FREE_RATE,
                },
            }
        except Exception as e:  # diagnostics must never break the state flush
            return {"error": repr(e)}

    async def run(self) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        await asyncio.gather(
            self.feed.start(self.state, self.on_tick),
            self.greeks_sampler(),
            self.risk_loop(),
            self.telemetry_flush(),
            self.entry_watcher(),
        )


if __name__ == "__main__":
    asyncio.run(Trader().run())
