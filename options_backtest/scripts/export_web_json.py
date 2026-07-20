"""Export options backtest summaries -> data/options_strategies.json for options_strategies.html.

Rerun whenever the backtest output CSVs change:
    python options_backtest/scripts/export_web_json.py
"""
from __future__ import annotations
import csv
import datetime
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "options_backtest" / "output"
DEST = ROOT / "data" / "options_strategies.json"

# The four production-worthy strategies shown as full cards on the page.
CURATED = {
    "Momentum-Expiry-HoldToSettle",
    "MeanReversion-Expiry",
    "FlowExpiry-pcr-Medium",
    "IC-1pct-manage50",
}

# (summary csv, family, trades-file prefix)
SOURCES = [
    ("strategy_summary.csv", "credit_spread", "trades_"),
    ("iron_condor_summary.csv", "iron_condor", "ic_trades_"),
    ("round3_flow_expiry_summary.csv", "flow_expiry", "trades_"),
]

STAT_KEYS = [
    "trades", "win_rate_pct", "total_return_pct", "cagr_pct",
    "max_drawdown_pct", "avg_win_rs", "avg_loss_rs", "profit_factor",
    "sharpe_like", "period_days", "notes",
]


def sparkline(trades_csv: Path, max_pts: int = 50) -> list[float]:
    if not trades_csv.exists():
        return []
    with trades_csv.open(newline="") as f:
        cum = [round(float(r["cum_pnl"]), 2) for r in csv.DictReader(f)]
    if len(cum) <= max_pts:
        return cum
    stride = (len(cum) - 1) / (max_pts - 1)
    return [cum[round(i * stride)] for i in range(max_pts)]


def main() -> None:
    strategies = []
    for fname, family, prefix in SOURCES:
        with (OUT_DIR / fname).open(newline="") as f:
            for row in csv.DictReader(f):
                name = row["strategy"]
                stats = {k: row.get(k, "") for k in STAT_KEYS}
                for k, v in stats.items():
                    if k != "notes":
                        try:
                            stats[k] = float(v)
                        except ValueError:
                            pass
                strategies.append({
                    "name": name,
                    "curated": name in CURATED,
                    "family": family,
                    "stats": stats,
                    "sparkline": sparkline(OUT_DIR / f"{prefix}{name}.csv"),
                })
    # curated first (best return first), then losers worst-first
    strategies.sort(key=lambda s: (
        not s["curated"],
        -s["stats"]["total_return_pct"] if s["curated"] else s["stats"]["total_return_pct"],
    ))
    out = {
        "generated": datetime.date.today().isoformat(),
        "basis_rs": 100000,
        "lot_size": 75,
        "strategies": strategies,
    }
    DEST.write_text(json.dumps(out, indent=1))

    # self-check
    assert sum(s["curated"] for s in strategies) == len(CURATED), "missing curated row"
    assert all(len(s["sparkline"]) <= 50 for s in strategies)
    assert all(s["sparkline"] for s in strategies if s["curated"]), "curated needs sparkline"
    print(f"wrote {DEST} ({len(strategies)} strategies, {sum(s['curated'] for s in strategies)} curated)")


if __name__ == "__main__":
    main()
