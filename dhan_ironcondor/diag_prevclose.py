"""One-off diagnostic: show exactly what Dhan returns for the FUT quote, so we
can see why prev_close auto-fetch failed. Prints structure + any error, never
the access token. Run in the terminal where DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN
are set:  python diag_prevclose.py
"""
import os
import json
import traceback

from instruments import download_master, load_universe, _find_close


def shape(o, depth=0, max_depth=6):
    """Print the response skeleton: dict keys / list lengths / leaf types."""
    pad = "  " * depth
    if depth > max_depth:
        print(pad + "...(deeper)")
        return
    if isinstance(o, dict):
        for k, v in o.items():
            if isinstance(v, (dict, list)):
                kind = "dict" if isinstance(v, dict) else f"list[{len(v)}]"
                print(f"{pad}{k!r}: {kind}")
                shape(v, depth + 1, max_depth)
            else:
                print(f"{pad}{k!r}: {v!r}")
    elif isinstance(o, list):
        for i, v in enumerate(o[:3]):
            print(f"{pad}[{i}]:")
            shape(v, depth + 1, max_depth)
        if len(o) > 3:
            print(f"{pad}...(+{len(o) - 3} more)")
    else:
        print(f"{pad}{o!r}")


def main():
    cid = os.environ.get("DHAN_CLIENT_ID", "")
    tok = os.environ.get("DHAN_ACCESS_TOKEN", "")
    print(f"CLIENT_ID set: {bool(cid)}   ACCESS_TOKEN set: {bool(tok)}")
    if not (cid and tok):
        print("!! Creds not in this terminal. Run this in the SAME window as main.py.")
        return

    download_master()
    uni = load_universe(spot_hint=None)
    sid = int(uni.underlying_security_id)
    seg = uni.underlying_segment_key
    print(f"FUT secid={sid}  segment_key={seg!r}")

    from dhanhq import DhanContext, dhanhq
    dhan = dhanhq(DhanContext(cid, tok))
    securities = {seg: [sid]}

    for meth in ("quote_data", "ohlc_data"):
        print(f"\n================= {meth} =================")
        fn = getattr(dhan, meth, None)
        if fn is None:
            print("  (method not present on this dhanhq version)")
            continue
        try:
            try:
                resp = fn(securities=securities)
            except TypeError:
                resp = fn(securities)
        except Exception:
            print("  RAISED an exception:")
            traceback.print_exc()
            continue
        print(f"  type: {type(resp).__name__}")
        print("  --- structure ---")
        shape(resp)
        found = _find_close(resp, sid)
        print(f"  _find_close -> {found!r}")


if __name__ == "__main__":
    main()
