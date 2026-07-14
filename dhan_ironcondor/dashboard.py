"""Read-only Streamlit dashboard. Never imports feed.py or execution.py --
only reads runtime/state.json (full snapshot) and runtime/events.jsonl
(append-only log), both written atomically by main.py.

Run: streamlit run dashboard.py
"""
from __future__ import annotations
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from config import STRATEGY as DEFAULT_STRATEGY

RUNTIME_ROOT = Path(__file__).parent / "runtime"


def discover_strategies() -> list[str]:
    """Runtime subdirs that actually hold a state.json, i.e. books that have run.
    Falls back to config.STRATEGY so the page still renders before the first flush."""
    found = sorted(p.name for p in RUNTIME_ROOT.glob("*") if (p / "state.json").exists())
    return found or [DEFAULT_STRATEGY]


def load_state(state_file: Path) -> dict | None:
    if not state_file.exists():
        return None
    try:
        return json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None  # tolerate a read landing mid-write; next refresh will catch it


def load_events(events_file: Path, limit: int = 200) -> list[dict]:
    if not events_file.exists():
        return []
    events = []
    try:
        with events_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # tolerate a torn last line if we're reading mid-append
    except OSError:
        return []
    return events[-limit:]


def _num(x, fmt="{:.2f}"):
    return fmt.format(x) if isinstance(x, (int, float)) else "—"


def _pct(x):
    return f"{x:.3f}%" if isinstance(x, (int, float)) else "—"


def _rs(x):
    """₹ display for signed amounts, e.g. '₹4,225.00' / '-₹1,300.00'."""
    if not isinstance(x, (int, float)):
        return "—"
    sign = "-" if x < 0 else ""
    return f"{sign}₹{abs(x):,.2f}"


# Must be the FIRST Streamlit call. `wide` uses the full window width -- without it
# the page is a ~730px centered column, so Side-by-side/Tabs squeeze two books into
# half the screen and every metric truncates ("0...", "Spot (..."). Wide gives each
# book real room.
st.set_page_config(page_title="NIFTY Options — Live Monitor",
                   page_icon="📈", layout="wide")

# ---------------- sidebar controls (OUTSIDE the auto-refresh fragment, so they
# keep their values and stay interactive -- no full-page reload) ----------------
st.sidebar.header("Controls")
# One dashboard can watch EVERY runtime/<strategy> book at once (condor + butterfly
# side by side or in tabs), or focus on a single book. Books are discovered from
# runtime/ so new strategies show up automatically once they've flushed once.
_strategies = discover_strategies()
_default_idx = _strategies.index(DEFAULT_STRATEGY) if DEFAULT_STRATEGY in _strategies else 0
_multi = len(_strategies) > 1
view = st.sidebar.radio(
    "View", ["Tabs (all books)", "Side by side", "Single book"],
    index=0 if _multi else 2,
    help="Tabs / Side by side render every book running under runtime/. "
         "Single shows just the one picked below.",
)
strategy = st.sidebar.selectbox("Single-view book", _strategies, index=_default_idx,
                                help="Which runtime/<strategy> book the Single view shows.")
auto = st.sidebar.checkbox("Auto-refresh", value=True,
                           help="Refreshes ONLY the data below on a timer. "
                                "Uncheck to freeze the view while you read/scroll.")
refresh_s = st.sidebar.number_input("Refresh every (seconds)", 1, 120, 5, step=1)
st.sidebar.button("🔄 Refresh now")  # any click reruns the script once
st.sidebar.caption("Each book's data panel reruns on its own timer; the page shell "
                   "(and these controls) stays put and keeps its values.")

st.title("NIFTY Options — Live Monitor (read-only)")

# run_every drives the fragment timer. None = no auto rerun (frozen).
run_every = int(refresh_s) if auto else None


@st.fragment(run_every=run_every)
def render(strategy: str) -> None:
    state_file = RUNTIME_ROOT / strategy / "state.json"
    events_file = RUNTIME_ROOT / strategy / "events.jsonl"

    st.markdown(f"## {strategy.title()} book")
    state = load_state(state_file)
    if state is None:
        st.warning(f"No runtime/{strategy}/state.json yet — is main.py running "
                   f"with STRATEGY={strategy}?")
        return

    mode = state.get("mode", "unknown")
    badge = "🟢 PAPER" if mode == "paper" else "🔴 LIVE"
    st.subheader(f"{badge}  |  strategy state: `{state.get('state')}`")

    diag = state.get("diagnostics")
    if diag is None:
        st.warning("This state.json has no diagnostics block — it was written by an "
                   "OLDER main.py. Restart the trader (Ctrl+C, then `python main.py`) "
                   "to populate the entry-gate and prospective-condor panels below.")
        diag = {}
    elif diag.get("error"):
        st.warning(f"diagnostics unavailable this flush: {diag['error']}")

    lv = diag.get("levels", {})

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Spot (NIFTY FUT)", _num(lv.get("spot_fut", state.get("spot"))))
    c2.metric("Prev close (FUT settle)", _num(lv.get("prev_close")))
    c3.metric("Today's open", _num(lv.get("open")))
    c4.metric("Net delta", _num(state.get("net_delta", 0), "{:.4f}"))
    c5.metric("Rolls (CALL/PUT)", f"{state.get('roll_count', {}).get('CALL', 0)} / "
                                  f"{state.get('roll_count', {}).get('PUT', 0)}")

    if state.get("halted_reason"):
        st.error(f"Halted: {state['halted_reason']}")

    # ------------------------------- entry gate -------------------------------
    st.markdown("### ⏱ Entry gate")
    ow = diag.get("or_window", {})
    _ws = ow.get("state")
    if _ws is None:  # older runtime without the state field
        _ws = ("closed" if ow.get("established")
               else "forming" if ow.get("in_window") else "before")
    or_status = {
        "closed": "range CLOSED — entry evaluated",
        "forming": "range FORMING now",
        "before": "before range window",
        "late": "⏩ range MISSED — delta-anchored late entry active",
        "missed": "⚠ range MISSED — bot started after 10:15",
    }.get(_ws, "—")
    gc1, gc2, gc3 = st.columns(3)
    gc1.metric("Now (IST)", diag.get("now_ist", "—"))
    gc2.metric(f"Opening range {ow.get('start','?')}–{ow.get('end','?')}", or_status)
    gc3.metric("Entry attempted?", "yes" if diag.get("entry_attempted") else "not yet")
    st.caption(f"OR high/low: {_num(lv.get('or_high'))} / {_num(lv.get('or_low'))}  ·  "
               f"expiry {diag.get('expiry','—')} ({diag.get('days_to_expiry','—')} days)  ·  "
               f"EOD flatten {diag.get('eod_flatten','—')}")

    filt = diag.get("filters", [])
    if filt:
        def _status(f):
            if f.get("value_pct") is None:
                return "⏳ pending"
            return "✅ pass" if f.get("pass") else "❌ FAIL"
        st.table(pd.DataFrame([{
            "Condition": f["name"],
            "Current": _pct(f.get("value_pct")),
            "Limit": f"≤ {f.get('limit_pct')}%",
            "Status": _status(f),
        } for f in filt]))

    verdict = diag.get("verdict", {})
    vstatus, vreason = verdict.get("status"), verdict.get("reason", "")
    if vstatus == "PASS":
        st.success(f"Entry filters: PASS — {vreason}")
    elif vstatus == "FAIL":
        st.error(f"Entry filters: FAIL — {vreason} (no trade today)")
    elif vstatus == "SKIP":
        st.warning(f"Entry filters: SKIPPED — {vreason}")
    elif vstatus == "PENDING":
        st.info(f"Entry filters: PENDING — {vreason}")

    # ------------------- prospective condor (what we'd sell) -------------------
    st.markdown("### 🎯 Prospective condor — what the selector would sell now")
    pc = diag.get("prospective_condor", {})
    if pc.get("available"):
        credit, minc = pc.get("net_credit"), pc.get("min_credit_req")
        m1, m2 = st.columns(2)
        m1.metric("Net credit (pts)", _num(credit), help=f"must be ≥ MIN_CREDIT_PTS ({minc})")
        m2.metric("Basis", pc.get("basis", "—"))
        st.table(pd.DataFrame([
            {"Leg": "Long Call (hedge)", "Strike": _num(pc.get("long_call_k")), "Short delta": "—"},
            {"Leg": "Short Call", "Strike": _num(pc.get("short_call_k")),
             "Short delta": _num(pc.get("short_call_delta"), "{:.3f}")},
            {"Leg": "Short Put", "Strike": _num(pc.get("short_put_k")),
             "Short delta": _num(pc.get("short_put_delta"), "{:.3f}")},
            {"Leg": "Long Put (hedge)", "Strike": _num(pc.get("long_put_k")), "Short delta": "—"},
        ]))
        if isinstance(credit, (int, float)) and isinstance(minc, (int, float)) and credit < minc:
            st.warning(f"Credit {credit:.2f} < MIN_CREDIT_PTS {minc} — this candidate would be rejected.")
    else:
        tgt = diag.get("config", {}).get("TARGET_SHORT_DELTA", "?")
        st.caption(f"No candidate right now: {pc.get('reason','—')}  ·  basis: {pc.get('basis','—')}")
        st.caption(f"(Short strikes target ≈{tgt} delta outside the opening range; wings are that ± WING_WIDTH.)")

    # ------------------------------- rules -------------------------------
    with st.expander("📋 Strategy rules (config.py)"):
        cfg = diag.get("config", {})
        if cfg:
            st.table(pd.DataFrame([{"Parameter": k, "Value": v} for k, v in cfg.items()]))
        else:
            st.caption("No config in snapshot yet (restart main.py).")

    # ------------------------- open positions & log -------------------------
    # ------------------------------- P&L (₹) -------------------------------
    st.markdown("### 💰 Position P&L")
    condor = state.get("condor")
    pnl = state.get("pnl", {})
    if condor and pnl.get("entry_credit_pts") is not None:
        qty = pnl.get("qty", 0)
        lots, lot_size = pnl.get("lots"), pnl.get("lot_size")
        entry_rs, close_rs, unrl_rs = (
            pnl.get("entry_credit_rs"), pnl.get("close_cost_rs"), pnl.get("unrealized_pnl_rs"),
        )
        st.caption(
            f"Sold {lots} lot(s) × {lot_size} qty/lot = {qty} qty. "
            f"Entered for {_rs(entry_rs)} credit; that's what you received when the "
            f"condor was opened, and it's also this trade's **max profit** "
            f"(kept in full if all four legs expire worthless)."
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("Entered (received)", _rs(entry_rs), help=f"{_num(pnl.get('entry_credit_pts'))} pts × {qty} qty")
        c2.metric("Cost to close now", _rs(close_rs) if close_rs is not None else "—",
                   help=f"{_num(pnl.get('close_cost_pts'))} pts × {qty} qty — what buying back all 4 legs would cost right now")
        c3.metric("Unrealized P&L", _rs(unrl_rs) if unrl_rs is not None else "—",
                   delta=(round(unrl_rs, 2) if unrl_rs is not None else None))
        if close_rs is None:
            st.warning("Live chain doesn't currently price all 4 open strikes — "
                       "unrealized P&L unavailable this refresh (will reappear once ticks catch up).")
        ww = diag.get("config", {}).get("WING_WIDTH", "?")
        max_loss = pnl.get("max_loss_rs")
        c4, c5, c6 = st.columns(3)
        c4.metric("Max profit (best case)", _rs(pnl.get("max_profit_rs")),
                   help="All 4 legs expire worthless — you keep the full entry credit.")
        c5.metric("Max loss (worst case)", _rs(-abs(max_loss)) if max_loss is not None else "—",
                   help=f"Wing width {ww} pts − credit received, × qty — capped loss on a breached side.")
        c6.metric("Margin used", _rs(pnl.get("margin_used_rs")))
        bl, bh = pnl.get("breakeven_low"), pnl.get("breakeven_high")
        if bl is not None and bh is not None:
            st.caption(f"Breakevens: spot below {_num(bl)} or above {_num(bh)} at expiry starts eating into the credit.")
        st.caption(
            f"Realized P&L today: {_rs(pnl.get('realized_pnl_rs'))}  ·  "
            f"Day-start equity: {_rs(pnl.get('day_start_equity_rs'))}  ·  "
            "Realized P&L only updates on a full EOD flatten in this build — "
            "it will read ₹0.00 intraday even after a roll; the unrealized figures above are the live truth."
        )
    else:
        st.caption("No condor open — nothing to mark P&L against yet.")

    # ------------------------------- delta drift -------------------------------
    st.markdown("### 📐 Delta drift (entry vs live)")
    dl = state.get("delta", {})
    entry_d, cur_d = dl.get("entry_net_delta"), dl.get("current_net_delta")
    entry_rs, cur_rs = dl.get("entry_delta_rs"), dl.get("current_delta_rs")
    if cur_d is not None:
        drift_d = (cur_d - entry_d) if entry_d is not None else None
        drift_rs = (cur_rs - entry_rs) if (cur_rs is not None and entry_rs is not None) else None
        d1, d2, d3 = st.columns(3)
        d1.metric("Delta at entry", _num(entry_d, "{:+.4f}"),
                  help=f"≈ {_rs(entry_rs)} per 1-pt NIFTY move — the near-neutral baseline when the condor was opened.")
        d2.metric("Delta now", _num(cur_d, "{:+.4f}"),
                  delta=(round(drift_d, 4) if drift_d is not None else None),
                  help=f"≈ {_rs(cur_rs)} per 1-pt NIFTY move — live directional exposure.")
        d3.metric("Drift since entry", _num(drift_d, "{:+.4f}") if drift_d is not None else "—",
                  help=f"≈ {_rs(drift_rs)} per 1-pt move — how far the position has drifted off neutral." if drift_rs is not None else None)
        st.caption(
            "Delta is normalized per unit (lots × lot size). The ₹ figures are P&L change "
            "**per 1-point move** in NIFTY: a positive delta gains when NIFTY rises, negative when it falls. "
            f"Band is ±{diag.get('config', {}).get('DELTA_BAND', '?')}; a persistent breach triggers a roll."
        )
    else:
        st.caption("No live delta yet — condor not open.")

    st.markdown("### Condor (open)")
    if condor:
        st.table(pd.DataFrame([condor]))
    else:
        st.caption("No condor open.")

    st.markdown("### Open legs")
    legs = state.get("legs", [])
    if legs:
        st.table(pd.DataFrame(legs))
    else:
        st.caption("No open legs.")

    st.markdown("### Recent events")
    events = load_events(events_file)
    if events:
        df = pd.DataFrame(events)
        if "ts" in df.columns:
            # epoch seconds -> IST (Asia/Kolkata) for display
            df["ts"] = (
                pd.to_datetime(df["ts"], unit="s", errors="coerce", utc=True)
                  .dt.tz_convert("Asia/Kolkata")
                  .dt.strftime("%Y-%m-%d %H:%M:%S IST")
            )
            df = df.sort_values("ts", ascending=False)
        st.dataframe(df, use_container_width=True, height=400)
    else:
        st.caption("No events yet.")

    st.caption(f"Data auto-refreshes every {refresh_s}s (fragment rerun — read-only, "
               "no background threads)." if auto else
               "Auto-refresh is OFF — view is frozen. Re-enable it in the sidebar.")


# Dispatch the chosen layout. Each render() is its own auto-refresh fragment, so
# every book below refreshes independently on the same timer without reloading the
# page shell. Single view falls back automatically when only one book exists.
if view == "Single book" or not _multi:
    render(strategy)
elif view == "Side by side":
    for col, s in zip(st.columns(len(_strategies)), _strategies):
        with col:
            render(s)
else:  # "Tabs (all books)"
    for tab, s in zip(st.tabs([s.title() for s in _strategies]), _strategies):
        with tab:
            render(s)
