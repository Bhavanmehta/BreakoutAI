"""Offline smoke test. No keys, no network: `python replay_smoke.py`.

Drives the RiskManager state machine through the full happy path plus the
two failure paths cheap enough to simulate:

  IDLE -> (enter) -> ACTIVE
       -> delta breach persists >180s (sim time) -> ROLLING -> ACTIVE (rolled)
       -> violent move hits a long-wing hedge wall -> HEDGE_BREACH -> FLATTENING -> HALTED

  (separate run) ACTIVE -> breach -> ROLLING -> reopen leg rejected -> ROLLBACK -> ACTIVE

Asserts each transition in order and prints a PASS/FAIL summary.
"""
from __future__ import annotations
import sys
import uuid
from datetime import datetime, time as dtime
from typing import Callable, Optional

from black76 import call_price, put_price, delta
from strategy import ChainLeg, OptionChain, Leg, select_butterfly
from execution import Broker, OrderResult, PaperBroker
from feed import MarketState
from risk import RiskManager, StrategyState
from config import (TZ, RISK_FREE_RATE, LOT_SIZE,
                    BUTTERFLY_DELTA_BAND, BUTTERFLY_ROLL_ENABLED)

FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    if condition:
        print(f"PASS: {label}")
    else:
        print(f"FAIL: {label}")
        FAILURES.append(label)


def build_chain(spot: float, sigma: float, T: float, strikes: range) -> OptionChain:
    calls, puts = {}, {}
    for k in strikes:
        k = float(k)
        c = call_price(spot, k, sigma, T, RISK_FREE_RATE)
        p = put_price(spot, k, sigma, T, RISK_FREE_RATE)
        calls[k] = ChainLeg(k, c, sigma)
        puts[k] = ChainLeg(k, p, sigma)
    return OptionChain(calls, puts)


class ScriptedBroker(Broker):
    """Wraps PaperBroker; can be told to reject specific legs by predicate,
    to cheaply simulate an order rejection for the rollback path."""

    def __init__(self, chain_provider: Callable[[], OptionChain],
                 reject_place: Optional[Callable[[Leg], bool]] = None):
        self._inner = PaperBroker(chain_provider)
        self._reject_place = reject_place or (lambda leg: False)

    def place_leg(self, leg: Leg) -> OrderResult:
        if self._reject_place(leg):
            action = "BUY" if leg.qty > 0 else "SELL"
            return OrderResult(f"REJ-{uuid.uuid4().hex[:6]}", "REJECTED", 0.0, leg, action,
                                reason="scripted rejection")
        return self._inner.place_leg(leg)

    def close_leg(self, leg: Leg) -> OrderResult:
        return self._inner.close_leg(leg)


def new_state(spot: float, sigma: float, T: float) -> tuple[MarketState, OptionChain]:
    chain = build_chain(spot, sigma, T, range(int(spot) - 1000, int(spot) + 1000, 50))
    state = MarketState()
    state.chain = chain
    state.spot = spot
    state.open_price = spot
    state.prev_close = spot * 1.001  # 0.1% gap, well inside the 0.5% filter
    state.or_high = spot + spot * 0.003   # 0.3% OR width, inside 0.75% filter
    state.or_low = spot - spot * 0.003
    state.or_established = True
    return state, chain


def main() -> None:
    events: list[dict] = []
    spot0 = 24000.0
    sigma = 0.50  # elevated-IV day -- needed so 0.20-delta strikes 200pts out still clear MIN_CREDIT_PTS
    T0 = 315.0 / (365.0 * 1440.0)  # ~5h15m to expiry (10:15 entry, 15:30 expiry)

    # ------------------------------------------------------------------
    # Run 1: happy path -> breach -> roll -> hedge wall -> flatten -> HALTED
    # ------------------------------------------------------------------
    state, chain = new_state(spot0, sigma, T0)
    broker = ScriptedBroker(lambda: state.chain)
    rm = RiskManager(market=state, broker=broker, on_event=events.append, lots=1)

    check(rm.state == StrategyState.IDLE, "initial state is IDLE")

    rm.state = StrategyState.WAITING_FOR_FILTERS
    rm.try_enter()
    check(rm.state == StrategyState.ACTIVE, "try_enter() -> ACTIVE")
    check(rm.condor is not None, "condor spec recorded")
    check(len(rm.legs) == 4, "4 legs opened")
    entered_events = [e for e in events if e["kind"] == "entered"]
    check(len(entered_events) == 1, "exactly one 'entered' event emitted")

    # drive spot toward the call wall to force a delta breach on the call side
    state.spot = rm.condor.short_call_k + 0.6 * (rm.condor.long_call_k - rm.condor.short_call_k)
    rm.sample_greeks()
    check(rm.state == StrategyState.DELTA_BREACH, "sustained call-side move -> DELTA_BREACH")
    check(rm.delta_breach_since is not None, "breach timer started")

    # simulate >180s elapsed without needing to actually sleep
    rm.delta_breach_since -= 181
    rm.sample_greeks()
    check(rm.state == StrategyState.ACTIVE, "breach persisted >180s -> roll executes -> back to ACTIVE")
    roll_events = [e for e in events if e["kind"] == "roll_completed"]
    check(len(roll_events) == 1, "exactly one roll_completed event")
    net_delta_after_roll = rm.current_net_delta()
    check(abs(net_delta_after_roll) <= 0.10, f"net delta after roll within reason ({net_delta_after_roll:.4f})")

    # violent move: underlying trades through the (rolled) long call wing -> hedge wall
    state.spot = rm.condor.long_call_k + 25.0
    rm.on_tick_hedge_check()
    check(rm.state == StrategyState.HALTED, "hedge wall breach -> flatten -> HALTED")
    check(len(rm.legs) == 0, "all legs closed on flatten")
    hedge_events = [e for e in events if e["kind"] == "hedge_wall_hit"]
    flatten_events = [e for e in events if e["kind"] == "flattening"]
    halted_events = [e for e in events if e["kind"] == "halted"]
    check(len(hedge_events) == 1, "exactly one hedge_wall_hit event")
    check(len(flatten_events) == 1, "exactly one flattening event")
    check(len(halted_events) == 1, "exactly one halted event")

    seq = [e["kind"] for e in events]
    expected_order = ["entered", "breach_started", "roll_started", "roll_completed",
                       "hedge_wall_hit", "flattening", "halted"]
    filtered = [k for k in seq if k in expected_order]
    check(filtered == expected_order, f"event order matches expected transitions: {filtered}")

    # ------------------------------------------------------------------
    # Run 2: rollback path -- reopen leg of the roll gets rejected
    # ------------------------------------------------------------------
    events2: list[dict] = []
    state2, chain2 = new_state(spot0, sigma, T0)

    rejected_strikes: set[float] = set()

    def reject_predicate(leg: Leg) -> bool:
        # reject only the *reopen* leg of the untested-side vertical during
        # the roll (i.e. a leg opened after entry, on a strike not part of
        # the original condor) -- cheap, deterministic way to force ROLLBACK.
        return leg.strike in rejected_strikes

    broker2 = ScriptedBroker(lambda: state2.chain, reject_place=reject_predicate)
    rm2 = RiskManager(market=state2, broker=broker2, on_event=events2.append, lots=1)
    rm2.state = StrategyState.WAITING_FOR_FILTERS
    rm2.try_enter()
    check(rm2.state == StrategyState.ACTIVE, "run2: try_enter() -> ACTIVE")

    # force a put-side breach this time
    state2.spot = rm2.condor.short_put_k - 0.6 * (rm2.condor.short_put_k - rm2.condor.long_put_k)
    rm2.sample_greeks()
    check(rm2.state == StrategyState.DELTA_BREACH, "run2: put-side move -> DELTA_BREACH")
    rm2.delta_breach_since -= 181

    # untested side on a put-side breach is CALL -- reject any new CALL short
    # strike beyond the original short call (i.e. the roll's reopen leg)
    rejected_strikes.update(
        k for k in state2.chain.calls if k != rm2.condor.short_call_k and k != rm2.condor.long_call_k
    )

    rm2.sample_greeks()
    check(rm2.state in (StrategyState.ACTIVE, StrategyState.HALTED),
          "run2: rejected reopen -> rollback resolves to ACTIVE or HALTED (never stuck in ROLLING)")
    rollback_events = [e for e in events2 if e["kind"] == "rollback_restored"]
    rollback_failed = [e for e in events2 if e["kind"] == "rollback_failed"]
    check(len(rollback_events) + len(rollback_failed) == 1,
          "run2: exactly one rollback resolution event (restored xor failed)")
    if rollback_events:
        check(rm2.state == StrategyState.ACTIVE, "run2: rollback restored -> state back to ACTIVE")
        check(len(rm2.legs) == 4, "run2: 4 legs present after rollback restore")

    # ------------------------------------------------------------------
    # Run 3: short iron butterfly -- both shorts at ATM, no-roll structure.
    # A persistent delta breach must FLATTEN (not roll) straight to HALTED.
    # ------------------------------------------------------------------
    events3: list[dict] = []
    state3, chain3 = new_state(spot0, sigma, T0)
    broker3 = ScriptedBroker(lambda: state3.chain)
    rm3 = RiskManager(market=state3, broker=broker3, on_event=events3.append, lots=1,
                      select_structure=select_butterfly,
                      delta_band=BUTTERFLY_DELTA_BAND,
                      roll_enabled=BUTTERFLY_ROLL_ENABLED)
    rm3.state = StrategyState.WAITING_FOR_FILTERS
    rm3.try_enter()
    check(rm3.state == StrategyState.ACTIVE, "fly: try_enter() -> ACTIVE")
    check(rm3.condor is not None, "fly: spec recorded")
    check(len(rm3.legs) == 4, "fly: 4 legs opened")
    check(rm3.condor is not None and rm3.condor.short_call_k == rm3.condor.short_put_k,
          "fly: both shorts sit on the same ATM strike")

    # drive spot toward the call wing to force a sustained delta breach
    state3.spot = rm3.condor.short_call_k + 0.6 * (rm3.condor.long_call_k - rm3.condor.short_call_k)
    rm3.sample_greeks()
    check(rm3.state == StrategyState.DELTA_BREACH, "fly: sustained move -> DELTA_BREACH")
    rm3.delta_breach_since -= 181
    rm3.sample_greeks()
    check(rm3.state == StrategyState.HALTED, "fly: persistent breach on no-roll structure -> flatten -> HALTED")
    check(len(rm3.legs) == 0, "fly: all legs closed on flatten")
    check(not any(e["kind"] == "roll_completed" for e in events3),
          "fly: no roll ever executed (roll_enabled=False)")
    seq3 = [e["kind"] for e in events3]
    expected3 = ["entered", "breach_started", "flattening", "halted"]
    filtered3 = [k for k in seq3 if k in expected3]
    check(filtered3 == expected3, f"fly: event order matches no-roll flatten path: {filtered3}")

    # ------------------------------------------------------------------
    print()
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
