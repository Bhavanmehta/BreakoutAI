"""Live-feed preflight. Connects the REAL Dhan socket for a few seconds and
reports what actually arrives -- WITHOUT starting the trader, timers, or any
order path. This is the one check that de-risks the parts that cannot be
verified offline: the raw tick field names, security_id types, and whether IV
backs out to sane numbers.

Run it before the OR window closes:

    python preflight.py            # ~20s, default
    python preflight.py 40         # custom seconds

Green flags to look for:
  - "spot (FUT LTP)" prints a number close to NIFTY FUT on your broker app
  - "option secids seen" is a healthy fraction of the subscribed strikes
  - sample IVs are plausible (~0.05-0.40), not 0.0 or the 3.0 solver ceiling
  - the raw-tick key dump matches what _apply_tick reads ('security_id','LTP')
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

from feed import MarketState, DhanFeed, _apply_tick
from instruments import download_master, load_universe


async def main(seconds: float) -> int:
    client_id = os.environ.get("DHAN_CLIENT_ID", "")
    access_token = os.environ.get("DHAN_ACCESS_TOKEN", "")
    if not client_id or not access_token:
        print("FAIL: set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN first.")
        return 2

    download_master()
    hint_raw = os.environ.get("NIFTY_FUT_PREV_CLOSE", "").strip()
    hint = float(hint_raw) if hint_raw else None
    uni = load_universe(spot_hint=hint, strike_window=40)
    print(f"universe: FUT secid={uni.underlying_security_id} expiry={uni.expiry} "
          f"lot={uni.lot_size} subscribing {len(uni.subscription_list())} instruments")
    if hint is None:
        print("note: NIFTY_FUT_PREV_CLOSE unset -> subscribing a wide strike window "
              "(fine for preflight; set it for the real run to center the window).")

    state = MarketState()
    state.underlying_secid = uni.underlying_security_id
    state.secid_to_option = uni.secid_to_option
    state.expiry = uni.expiry

    seen_secids: set[str] = set()
    first_raw_keys = {}
    n_ticks = 0

    # Wrap the real merge so we also observe raw shape + count, without changing
    # feed behaviour. This exercises the exact _apply_tick path the trader uses.
    orig_now = state.now_ist

    def observing_on_tick(_s: MarketState) -> None:
        pass

    class _ObservingFeed(DhanFeed):
        pass

    feed = DhanFeed(client_id, access_token, instruments=uni.subscription_list())

    # Monkeypatch the module-level _apply_tick used inside feed.start's loop by
    # wrapping via a subclass is not possible (it's a free function), so instead
    # run feed.start with an on_tick that snapshots state, and separately sniff
    # raw ticks by temporarily swapping the function.
    import feed as feed_module
    real_apply = feed_module._apply_tick

    def sniffing_apply(s: MarketState, tick: dict) -> None:
        nonlocal n_ticks
        n_ticks += 1
        if not first_raw_keys and isinstance(tick, dict):
            first_raw_keys.update({k: type(tick[k]).__name__ for k in tick})
        real_apply(s, tick)
        sid = str(tick.get("security_id")) if tick.get("security_id") is not None else None
        if sid and sid in s.secid_to_option:
            seen_secids.add(sid)

    feed_module._apply_tick = sniffing_apply
    try:
        task = asyncio.create_task(feed.start(state, observing_on_tick))
        await asyncio.sleep(seconds)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        feed_module._apply_tick = real_apply

    print("\n----- preflight results -----")
    print(f"connected           : {state.connected}")
    print(f"raw ticks received  : {n_ticks}")
    print(f"first raw tick keys : {first_raw_keys or '(none -- no dict ticks arrived)'}")
    print(f"spot (FUT LTP)      : {state.spot}")
    print(f"option secids seen  : {len(seen_secids)} / {len(uni.secid_to_option)} subscribed")
    # Show strikes NEAREST spot -- that's where the strategy actually sells and
    # where IV is well-conditioned. The extreme wings read garbage IV by nature.
    spot = state.spot or 0.0
    calls = sorted(state.chain.calls.items(), key=lambda kv: abs(kv[0] - spot))[:3]
    puts = sorted(state.chain.puts.items(), key=lambda kv: abs(kv[0] - spot))[:3]
    print("ATM CALL legs       :", [(k, round(v.ltp, 2), round(v.iv, 4)) for k, v in calls])
    print("ATM PUT  legs       :", [(k, round(v.ltp, 2), round(v.iv, 4)) for k, v in puts])
    # IV sanity is judged on the ATM neighborhood only, not the unreliable wings.
    atm_ivs = [v.iv for _, v in calls + puts]
    print(f"ATM IV range        : {min(atm_ivs):.4f} - {max(atm_ivs):.4f}" if atm_ivs else "ATM IV range        : (none)")

    ok = state.spot > 0 and len(seen_secids) > 0
    sane_iv = any(0.02 < iv < 1.5 for iv in atm_ivs)
    print("\nverdict:",
          "PASS" if (ok and sane_iv) else "REVIEW NEEDED")
    if not ok:
        print("  - no FUT spot and/or no option ticks -> check field names above "
              "against _apply_tick (security_id / LTP) and market hours.")
    elif not sane_iv:
        print("  - ticks arrive but IVs are all ~0 or pinned at the ceiling -> "
              "check that spot/expiry are set and LTPs are premiums, not prices*100.")
    return 0 if (ok and sane_iv) else 1


if __name__ == "__main__":
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    raise SystemExit(asyncio.run(main(secs)))
