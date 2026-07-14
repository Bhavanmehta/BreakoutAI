"""MarketState (the one shared mutable dataclass ticks write into) + Feed
interface. DhanFeed is the only concrete live implementation; dhanhq /
websockets are imported inside it so paper-without-feed and the offline
checks (black76.py, replay_smoke.py) never need them installed.
"""
from __future__ import annotations
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Callable, Optional

from strategy import OptionChain
from config import TZ, RISK_FREE_RATE


@dataclass
class MarketState:
    """Single shared mutable snapshot. WS ticks (and, in replay, the
    simulator) write directly into this. Everything else only reads it.

    `spot` holds the current-month NIFTY FUT LTP (the pricing underlying F, per
    black76.py -- never cash spot). The instrument maps below are populated once
    at startup from the scrip master; raw ticks carry only security_id + LTP, so
    _apply_tick uses them to recover (strike, opt_type) and to back IV out of LTP.
    """
    spot: float = 0.0
    chain: OptionChain = field(default_factory=lambda: OptionChain({}, {}))
    last_tick_ts: float = 0.0
    open_price: Optional[float] = None
    prev_close: Optional[float] = None
    or_high: Optional[float] = None
    or_low: Optional[float] = None
    or_established: bool = False
    connected: bool = False

    # instrument wiring (set at startup from instruments.Universe)
    underlying_secid: Optional[str] = None            # FUT security_id -> writes spot
    secid_to_option: dict = field(default_factory=dict)  # secid -> (strike, "CALL"|"PUT")
    expiry: Optional[date] = None                     # option expiry -> feeds T for IV

    def now_ist(self) -> datetime:
        return datetime.now(TZ)


OnTick = Callable[[MarketState], None]


class Feed(ABC):
    """One method: run forever, mutate `state` on each tick, then call
    on_tick(state) synchronously so the hedge-wall check happens inline
    in the tick callback (spec: not on a timer)."""

    @abstractmethod
    async def start(self, state: MarketState, on_tick: OnTick) -> None:
        ...


class DhanFeed(Feed):
    """Live NIFTY spot + option chain via Dhan's websocket market feed."""

    def __init__(self, client_id: str, access_token: str, instruments: list[tuple]):
        # instruments: (exchange_segment, security_id[, subscription_type]) tuples,
        # spot + all chain strikes; 2-tuples are upgraded to Quote subscriptions.
        self._client_id = client_id
        self._access_token = access_token
        self._instruments = instruments

    async def start(self, state: MarketState, on_tick: OnTick) -> None:
        # guarded import -- live mode only. dhanhq >= 2.1 API: DhanContext + MarketFeed
        # (the old marketfeed.DhanFeed class was removed in v2.x).
        import asyncio
        from dhanhq import DhanContext, MarketFeed

        # Translate our string segment keys -> dhanhq MarketFeed constants.
        # getattr fallbacks guard against library attribute renames across
        # dhanhq point releases (values are Dhan's stable numeric segment ids).
        seg_const = {
            "IDX_I": getattr(MarketFeed, "IDX_I", 0),
            "NSE_FNO": getattr(MarketFeed, "NSE_FNO", 2),
            "NSE_EQ": getattr(MarketFeed, "NSE", 1),
        }
        instruments = []
        for t in self._instruments:
            seg = seg_const.get(t[0], t[0])  # allow already-resolved constants
            sub = t[2] if len(t) >= 3 else MarketFeed.Quote
            instruments.append((seg, str(t[1]), sub))
        ctx = DhanContext(self._client_id, self._access_token)
        # NOTE: MarketFeed.__init__ creates its own event loop for its *sync*
        # helpers (run_forever/get_data). We bypass those and await its async
        # internals (connect / get_instrument_data) on our own running loop.
        feed = MarketFeed(ctx, instruments, version="v2")
        try:
            while True:
                try:
                    await feed.connect()  # connects websocket + subscribes instruments
                    state.connected = True
                    while True:
                        tick = await feed.get_instrument_data()
                        if not isinstance(tick, dict):
                            continue  # heartbeat / disconnect frames
                        _apply_tick(state, tick)
                        state.last_tick_ts = time.time()
                        on_tick(state)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    state.connected = False
                    await asyncio.sleep(2.0)  # backoff, then reconnect
        finally:
            state.connected = False
            try:
                await feed.disconnect()
            except Exception:
                pass


def _apply_tick(state: MarketState, tick: dict) -> None:
    """Merge one Dhan feed tick into MarketState.

    Raw dhanhq MarketFeed ticks carry only an identity + a price -- roughly
    {'type': 'Quote Data'|'Ticker Data', 'security_id': ..., 'LTP': ...} -- and
    NOT strike / option_type / IV. We recover those from the startup instrument
    maps and back IV out of the LTP with Black-76. security_id may arrive as int
    or str depending on packet type, so it is normalised to str for lookup.
    """
    sid = tick.get("security_id")
    if sid is None:
        return
    sid = str(sid)
    raw_ltp = tick.get("LTP", tick.get("ltp"))
    if raw_ltp is None:
        return
    try:
        ltp = float(raw_ltp)
    except (TypeError, ValueError):
        return
    if ltp <= 0:
        return

    if sid == state.underlying_secid:
        state.spot = ltp                 # current-month FUT LTP = pricing underlying F
        return

    opt = state.secid_to_option.get(sid)
    if opt is None:
        return                           # not the FUT, not a subscribed strike -> ignore
    strike, opt_type = opt
    iv = _solve_iv(state, strike, opt_type, ltp)
    from strategy import ChainLeg
    book = state.chain.calls if opt_type == "CALL" else state.chain.puts
    book[strike] = ChainLeg(strike, ltp, iv)


def _solve_iv(state: MarketState, strike: float, opt_type: str, ltp: float) -> float:
    """Back IV out of the option LTP. The feed never sends IV, so every delta in
    the system depends on this. Returns 0.0 (which strategy treats as unusable)
    until we have a positive underlying and an expiry -- never raises into the
    tick loop."""
    F = state.spot
    if F <= 0.0 or state.expiry is None:
        return 0.0
    from black76 import time_to_expiry, implied_vol
    try:
        T = time_to_expiry(state.now_ist(), state.expiry)
        return implied_vol(ltp, F, strike, T, RISK_FREE_RATE, opt_type)
    except Exception:
        return 0.0
