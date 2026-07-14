"""State machine + risk management. Orchestrates strategy.py (pure decisions)
and execution.py (order placement) against a live MarketState. No I/O of its
own except emitting events via an injected callback -- main.py owns
runtime/state.json and runtime/events.jsonl.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, time as dtime
from enum import Enum
from typing import Callable, Optional

from config import (
    TZ, RISK_FREE_RATE, LOT_SIZE, MAX_LOTS, DELTA_BAND, BREACH_PERSIST_S,
    MAX_ROLLS_PER_SIDE, DAILY_LOSS_PCT, CAPITAL, EOD_FLATTEN,
    LATE_ENTRY_MAX_MOVE_PCT, WING_WIDTH, MARGIN_PER_LOT_PAPER,
)
from strategy import (
    Leg, OptionChain, CondorSpec, entry_filters, late_entry_ok, select_condor,
    net_portfolio_delta, select_roll, breakevens,
)
from execution import Broker
from feed import MarketState
from black76 import time_to_expiry as _black76_time_to_expiry


class StrategyState(str, Enum):
    IDLE = "IDLE"                       # before OR window closes, nothing to do yet
    WAITING_FOR_FILTERS = "WAITING_FOR_FILTERS"  # OR established, waiting for 10:15 filter check
    ENTERING = "ENTERING"               # placing the 4 condor legs
    ACTIVE = "ACTIVE"                   # condor live, delta within band
    DELTA_BREACH = "DELTA_BREACH"       # delta outside band, breach timer running
    ROLLING = "ROLLING"                 # closing untested vertical + opening replacement
    ROLLBACK = "ROLLBACK"               # roll order rejected, restoring prior vertical
    HEDGE_BREACH = "HEDGE_BREACH"       # underlying traded through a long-wing strike
    FLATTENING = "FLATTENING"           # closing all legs
    HALTED = "HALTED"                   # terminal: flat, no further action today
    DONE = "DONE"                       # terminal: filters failed, never entered


def time_to_expiry_years(now: datetime) -> float:
    """Same-day (0DTE-style) weekly expiry assumption: 15:30 IST today.
    Delegates to black76.time_to_expiry so there's one implementation of
    this math in the whole system."""
    return _black76_time_to_expiry(now, now.date())


@dataclass
class RiskManager:
    market: MarketState
    broker: Broker
    on_event: Callable[[dict], None]
    lots: int = MAX_LOTS

    # Pluggable structure: which selector builds the 4-leg spec (select_condor by
    # default, select_butterfly for the ATM fly), the delta band that triggers a
    # breach, and whether a persistent breach rolls the untested vertical or just
    # flattens. main.py sets these from config.STRATEGY. Defaults preserve the
    # original condor behaviour so existing callers/tests are unaffected.
    select_structure: Callable = select_condor
    delta_band: float = DELTA_BAND
    roll_enabled: bool = True

    state: StrategyState = StrategyState.IDLE
    condor: Optional[CondorSpec] = None
    legs: list[Leg] = field(default_factory=list)
    roll_count: dict = field(default_factory=lambda: {"CALL": 0, "PUT": 0})
    delta_breach_since: Optional[float] = None  # monotonic seconds
    day_start_equity: float = CAPITAL
    realized_pnl: float = 0.0
    halted_reason: str = ""
    entry_net_delta: Optional[float] = None  # net delta captured at initial condor entry

    # ---------- helpers ----------

    def _emit(self, kind: str, **fields) -> None:
        self.on_event({"kind": kind, "ts": time.time(), "state": self.state.value, **fields})

    def _ivs(self) -> dict[tuple[float, str], float]:
        ivs = {}
        for k, leg in self.market.chain.calls.items():
            ivs[(k, "CALL")] = leg.iv
        for k, leg in self.market.chain.puts.items():
            ivs[(k, "PUT")] = leg.iv
        return ivs

    def _now(self) -> datetime:
        return self.market.now_ist()

    def _T(self) -> float:
        # Use the actually-selected option expiry when the instrument master has
        # wired one in; fall back to the same-day 0DTE assumption only if it
        # hasn't. Getting this wrong silently biases every IV and delta.
        expiry = getattr(self.market, "expiry", None)
        if expiry is not None:
            return _black76_time_to_expiry(self._now(), expiry)
        return time_to_expiry_years(self._now())

    def current_net_delta(self) -> float:
        return net_portfolio_delta(self.legs, self.market.spot, self._T(), RISK_FREE_RATE, self._ivs())

    # ---------- entry ----------

    def skip_day(self, reason: str) -> None:
        """Terminal 'no trade today' -- record why and go DONE."""
        self._emit("entry_skipped", reason=reason)
        self.state = StrategyState.DONE

    def try_enter(self) -> None:
        """OR-breakout entry: called once, right after 10:15 OR close, with
        WAITING_FOR_FILTERS active. One-shot -- goes DONE on any skip."""
        m = self.market
        if not m.or_established or m.open_price is None or m.prev_close is None:
            self.skip_day("opening range not established")
            return

        ok, reason = entry_filters(m.open_price, m.prev_close, m.spot, m.or_high, m.or_low)
        if not ok:
            self.skip_day(reason)
            return

        self.state = StrategyState.ENTERING
        spec, reason = self.select_structure(m.chain, m.or_high, m.or_low, m.spot, self._T(), RISK_FREE_RATE)
        if spec is None:
            self.skip_day(reason)
            return
        self._open_condor(spec)

    def try_enter_late(self) -> bool:
        """Delta-anchored entry for a late start (past OR_END): anchor strikes on
        live spot by TARGET_SHORT_DELTA (no OR-outside constraint), gated by a
        volatility-sanity guard instead of the OR filters. Retryable -- returns
        True once the day is resolved (a position opened or the broker halted),
        False on a soft skip so the watcher can retry before EOD. Soft skips keep
        the strategy in WAITING_FOR_FILTERS."""
        m = self.market
        if m.prev_close is None or not m.spot or m.chain is None:
            return False  # inputs not ready yet -- retry

        ok, reason = late_entry_ok(m.spot, m.prev_close, LATE_ENTRY_MAX_MOVE_PCT)
        if not ok:
            self._emit("late_entry_waiting", reason=reason)
            return False  # day trending too hard -- retry when it settles

        self.state = StrategyState.ENTERING
        # Anchor both bounds on spot => condor scans calls>=spot and puts<=spot by
        # delta; the butterfly selector ignores the bounds and anchors ATM on spot.
        spec, reason = self.select_structure(m.chain, m.spot, m.spot, m.spot, self._T(), RISK_FREE_RATE)
        if spec is None:
            self._emit("late_entry_waiting", reason=reason)
            self.state = StrategyState.WAITING_FOR_FILTERS
            return False  # no qualifying condor right now -- retry

        self._open_condor(spec)
        return True  # entered or halted -- stop retrying

    def _open_condor(self, spec: CondorSpec) -> None:
        """Place the four legs of a selected condor. Unwinds partials and HALTs
        on any rejection; sets ACTIVE and emits 'entered' on full fill. Shared by
        both the OR-breakout and late delta-anchored entry paths."""
        qty = self.lots * LOT_SIZE
        legs = [
            Leg(spec.short_call_k, "CALL", -qty),
            Leg(spec.long_call_k, "CALL", +qty),
            Leg(spec.short_put_k, "PUT", -qty),
            Leg(spec.long_put_k, "PUT", +qty),
        ]
        filled: list[Leg] = []
        for leg in legs:
            result = self.broker.place_leg(leg)
            if result.status != "FILLED":
                self._emit("order_rejected", leg=leg.__dict__, reason=result.reason)
                # unwind whatever filled so far, don't leave a naked partial condor
                for f in filled:
                    self.broker.close_leg(f)
                self.state = StrategyState.HALTED
                self.halted_reason = f"entry order rejected: {result.reason}"
                self._emit("halted", reason=self.halted_reason)
                return
            filled.append(replace(leg, entry_price=result.avg_price))

        self.condor = spec
        self.legs = filled
        be_low, be_high = breakevens(spec.short_call_k, spec.short_put_k, spec.net_credit)
        self.state = StrategyState.ACTIVE
        # Snapshot net delta at the moment of entry (should be ~neutral). This is
        # the baseline the dashboard compares live drift against; set once here and
        # deliberately NOT updated on rolls -- "the delta we entered at".
        self.entry_net_delta = self.current_net_delta()
        self._emit("entered", condor=spec.__dict__, breakevens=[be_low, be_high],
                   entry_net_delta=self.entry_net_delta)

    # ---------- inline hedge-wall check (called from the tick callback, NOT a timer) ----------

    def on_tick_hedge_check(self) -> None:
        if self.state not in (StrategyState.ACTIVE, StrategyState.DELTA_BREACH,
                               StrategyState.ROLLING, StrategyState.ROLLBACK):
            return
        if self.condor is None:
            return
        F = self.market.spot
        if F >= self.condor.long_call_k or F <= self.condor.long_put_k:
            side = "CALL" if F >= self.condor.long_call_k else "PUT"
            self.state = StrategyState.HEDGE_BREACH
            self._emit("hedge_wall_hit", side=side, spot=F,
                       wall=self.condor.long_call_k if side == "CALL" else self.condor.long_put_k)
            self.flatten(f"hedge wall breached on {side} side, spot={F}")

    # ---------- 30s greeks sampler ----------

    def sample_greeks(self) -> None:
        if self.state not in (StrategyState.ACTIVE, StrategyState.DELTA_BREACH):
            return
        net_delta = self.current_net_delta()
        # NOTE: deliberately do NOT emit a periodic "greeks_sample" event here.
        # The live net delta is already carried in the state.json snapshot (written
        # every flush) and shown live on the dashboard; logging one row every 30s
        # regardless of change just floods events.jsonl with near-empty records.
        # Only genuine state transitions below (breach start/clear) are logged.

        if abs(net_delta) <= self.delta_band:
            if self.state == StrategyState.DELTA_BREACH:
                self._emit("breach_cleared", net_delta=net_delta)
            self.delta_breach_since = None
            self.state = StrategyState.ACTIVE
            return

        if self.delta_breach_since is None:
            self.delta_breach_since = time.monotonic()
            self.state = StrategyState.DELTA_BREACH
            self._emit("breach_started", net_delta=net_delta)
            return

        elapsed = time.monotonic() - self.delta_breach_since
        self.state = StrategyState.DELTA_BREACH
        if elapsed >= BREACH_PERSIST_S:
            if self.roll_enabled:
                self._roll(net_delta)
            else:
                # No-roll structure (e.g. the ATM butterfly): closing the untested
                # vertical is meaningless when both shorts share a strike, so a
                # persistent breach exits the position instead of rolling it.
                self.flatten(
                    f"delta breach persisted {elapsed:.0f}s >= {BREACH_PERSIST_S}s "
                    f"(net delta {net_delta:.3f}); no-roll structure -> flatten"
                )

    # ---------- roll workflow ----------

    def _roll(self, residual_delta_pre: float) -> None:
        if self.condor is None:
            return
        # Sign convention (see strategy.py): short put delta is positive, so a
        # crash (put side tested) drives net delta positive; a rally (call
        # side tested) drives net delta negative.
        tested_side = "PUT" if residual_delta_pre > 0 else "CALL"
        untested_side = "PUT" if tested_side == "CALL" else "CALL"

        if self.roll_count[untested_side] >= MAX_ROLLS_PER_SIDE:
            self._emit("roll_skipped", reason=f"max rolls reached on {untested_side}")
            self.flatten(f"max rolls ({MAX_ROLLS_PER_SIDE}) reached on {untested_side} side")
            return

        self.state = StrategyState.ROLLING
        self._emit("roll_started", tested_side=tested_side, residual_delta=residual_delta_pre)

        old_short, old_long = self._legs_for_side(untested_side)
        close_results = [self.broker.close_leg(old_short), self.broker.close_leg(old_long)]
        if any(r.status != "FILLED" for r in close_results):
            self._emit("roll_close_rejected", side=untested_side)
            self.state = StrategyState.ROLLBACK
            self._rollback(untested_side, old_short, old_long)
            return
        # Untested vertical is now flat -- realize its P&L: (close - entry) * signed
        # qty is +ve for a long leg bought back cheaper... no, correctly: for a long
        # leg (qty>0) profit is (close-entry); for a short leg (qty<0) profit is
        # (entry-close), and (close-entry)*qty gives exactly that in both cases since
        # qty carries the sign.
        for old_leg, result in zip((old_short, old_long), close_results):
            self.realized_pnl += (result.avg_price - old_leg.entry_price) * old_leg.qty
        self.legs = [l for l in self.legs if l.strike not in (old_short.strike, old_long.strike)
                     or l.opt_type != untested_side]

        residual = net_portfolio_delta(self.legs, self.market.spot, self._T(), RISK_FREE_RATE, self._ivs())
        vertical, reason = select_roll(self.market.chain, tested_side, residual, self.market.spot,
                                        self._T(), RISK_FREE_RATE)
        if vertical is None:
            self._emit("roll_failed", reason=reason)
            self.state = StrategyState.ROLLBACK
            self._rollback(untested_side, old_short, old_long)
            return

        qty = self.lots * LOT_SIZE
        new_short = Leg(vertical.short_k, vertical.side, -qty)
        new_long = Leg(vertical.long_k, vertical.side, +qty)
        open_results = [self.broker.place_leg(new_short), self.broker.place_leg(new_long)]
        if any(r.status != "FILLED" for r in open_results):
            self._emit("roll_open_rejected", side=untested_side, reason=reason)
            self.state = StrategyState.ROLLBACK
            self._rollback(untested_side, old_short, old_long)
            return

        self.legs += [replace(new_short, entry_price=open_results[0].avg_price),
                      replace(new_long, entry_price=open_results[1].avg_price)]
        self.roll_count[untested_side] += 1
        self.delta_breach_since = None
        self.state = StrategyState.ACTIVE
        self._emit("roll_completed", side=untested_side, vertical=vertical.__dict__, log=reason)

    def _rollback(self, side: str, old_short: Leg, old_long: Leg) -> None:
        """Roll failed (close or reopen rejected) -- try to restore the
        original untested vertical so we're not left unhedged."""
        r1 = self.broker.place_leg(old_short)
        r2 = self.broker.place_leg(old_long)
        if r1.status == "FILLED" and r2.status == "FILLED":
            # Fresh re-entry at the current fill price -- NOT the original entry
            # price, since the earlier close already realized that leg's P&L above.
            self.legs += [replace(old_short, entry_price=r1.avg_price),
                          replace(old_long, entry_price=r2.avg_price)]
            self.state = StrategyState.ACTIVE
            self._emit("rollback_restored", side=side)
        else:
            self.halted_reason = f"rollback failed on {side} side, position may be unhedged"
            self._emit("rollback_failed", side=side)
            self.flatten(self.halted_reason)

    def _legs_for_side(self, side: str) -> tuple[Leg, Leg]:
        side_legs = [l for l in self.legs if l.opt_type == side]
        short_leg = next(l for l in side_legs if l.qty < 0)
        long_leg = next(l for l in side_legs if l.qty > 0)
        return short_leg, long_leg

    # ---------- 5 min risk loop ----------

    def risk_loop(self, current_equity: float) -> None:
        if self.state in (StrategyState.HALTED, StrategyState.DONE, StrategyState.IDLE,
                           StrategyState.WAITING_FOR_FILTERS):
            return
        drawdown_pct = (self.day_start_equity - current_equity) / self.day_start_equity
        if drawdown_pct >= DAILY_LOSS_PCT:
            self.flatten(f"daily loss limit hit: {drawdown_pct:.2%} >= {DAILY_LOSS_PCT:.2%}")
            return

        now_t = self._now().time()
        eod_h, eod_m = (int(x) for x in EOD_FLATTEN.split(":"))
        if now_t >= dtime(eod_h, eod_m):
            self.flatten(f"EOD flatten time {EOD_FLATTEN} reached")

    # ---------- flatten ----------

    def flatten(self, reason: str) -> None:
        if self.state in (StrategyState.HALTED, StrategyState.DONE):
            return
        self.state = StrategyState.FLATTENING
        self._emit("flattening", reason=reason)
        for leg in list(self.legs):
            result = self.broker.close_leg(leg)
            if result.status != "FILLED":
                self._emit("flatten_leg_failed", leg=leg.__dict__, reason=result.reason)
            else:
                self.realized_pnl += (result.avg_price - leg.entry_price) * leg.qty
                self.legs.remove(leg)
        self.state = StrategyState.HALTED
        self.halted_reason = reason
        self._emit("halted", reason=reason)

    # ---------- P&L / currency ----------

    def _close_cost_pts(self) -> Optional[float]:
        """Live points cost to buy back the open condor (close all four legs)
        at current chain LTPs. Returns None -- not 0.0 -- if any of the four
        strikes are missing from the chain (stale/partial snapshot), so
        callers don't silently report a fake P&L.
        """
        if self.condor is None:
            return None
        calls, puts = self.market.chain.calls, self.market.chain.puts
        try:
            sc = calls[self.condor.short_call_k].ltp
            lc = calls[self.condor.long_call_k].ltp
            sp = puts[self.condor.short_put_k].ltp
            lp = puts[self.condor.long_put_k].ltp
        except KeyError:
            return None
        if None in (sc, lc, sp, lp):
            return None
        # Buy back shorts (pay LTP), sell back longs (receive LTP).
        return (sc - lc) + (sp - lp)

    def _pnl_dict(self) -> dict:
        qty = self.lots * LOT_SIZE if self.lots else 0
        entry_pts = self.condor.net_credit if self.condor else None
        close_pts = self._close_cost_pts()
        unrl_pts = (
            entry_pts - close_pts
            if entry_pts is not None and close_pts is not None
            else None
        )
        be_low = be_high = None
        if self.condor:
            be_low, be_high = breakevens(
                self.condor.short_call_k, self.condor.short_put_k,
                self.condor.net_credit,
            )
        return {
            "lot_size": LOT_SIZE,
            "lots": self.lots,
            "qty": qty,
            "entry_credit_pts": entry_pts,
            "entry_credit_rs": entry_pts * qty if entry_pts is not None else None,
            "close_cost_pts": close_pts,
            "close_cost_rs": close_pts * qty if close_pts is not None else None,
            "unrealized_pnl_pts": unrl_pts,
            "unrealized_pnl_rs": unrl_pts * qty if unrl_pts is not None else None,
            "max_profit_rs": entry_pts * qty if entry_pts is not None else None,
            "max_loss_rs": (
                (WING_WIDTH - entry_pts) * qty if entry_pts is not None else None
            ),
            "breakeven_low": be_low,
            "breakeven_high": be_high,
            "margin_used_rs": self.lots * MARGIN_PER_LOT_PAPER if self.lots else 0.0,
            "realized_pnl_rs": self.realized_pnl,
            "day_start_equity_rs": self.day_start_equity,
        }

    # ---------- snapshot for dashboard ----------

    def to_dict(self) -> dict:
        cur_delta = self.current_net_delta() if self.legs else 0.0
        qty = self.lots * LOT_SIZE if self.lots else 0
        return {
            "state": self.state.value,
            "condor": self.condor.__dict__ if self.condor else None,
            "legs": [l.__dict__ for l in self.legs],
            "roll_count": self.roll_count,
            "halted_reason": self.halted_reason,
            "net_delta": cur_delta,
            "delta": {
                # Per-unit net deltas (normalized per lots*LOT_SIZE). Entry is the
                # ~neutral baseline snapshotted at entry; current drifts with spot.
                "entry_net_delta": self.entry_net_delta,
                "current_net_delta": cur_delta if self.legs else None,
                # ₹ exposure = signed P&L change per 1-point move in the underlying
                # (per-unit delta × total qty). None until a condor is live.
                "entry_delta_rs": (
                    self.entry_net_delta * qty if self.entry_net_delta is not None else None
                ),
                "current_delta_rs": cur_delta * qty if self.legs else None,
            },
            "spot": self.market.spot,
            "pnl": self._pnl_dict(),
        }
