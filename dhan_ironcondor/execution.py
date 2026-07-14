"""Broker interface + PaperBroker (fills at current chain LTP) + DhanBroker
(guarded import -- dhanhq only imported inside DhanBroker so paper mode and
`python black76.py` / `python replay_smoke.py` work without the package
installed).

Order ids are persisted to runtime/orders.json (atomic write: tmp file then
os.replace) BEFORE place_leg/close_leg returns to the caller, per spec --
so a crash right after a fill still leaves a durable record of what's live.
"""
from __future__ import annotations
import json
import os
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Optional

from config import STRATEGY
from strategy import Leg, OptionChain

# Per-strategy runtime dir so a condor book and a butterfly book don't clobber
# each other's orders.json. Must match main.py's runtime/<STRATEGY> layout.
RUNTIME_DIR = Path(__file__).parent / "runtime" / STRATEGY
ORDERS_FILE = RUNTIME_DIR / "orders.json"


def _atomic_write_json(path: Path, data) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    os.replace(tmp, path)


def _load_orders() -> list[dict]:
    if not ORDERS_FILE.exists():
        return []
    try:
        return json.loads(ORDERS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return []


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    status: str  # "FILLED" | "REJECTED" | "PENDING"
    avg_price: float
    leg: Leg
    action: str  # "BUY" | "SELL"
    reason: str = ""


def _persist_order(result: OrderResult) -> None:
    """Append order id + fill info to runtime/orders.json (atomic)."""
    orders = _load_orders()
    orders.append({
        "order_id": result.order_id,
        "status": result.status,
        "avg_price": result.avg_price,
        "action": result.action,
        "strike": result.leg.strike,
        "opt_type": result.leg.opt_type,
        "qty": result.leg.qty,
        "reason": result.reason,
        "ts": time.time(),
    })
    _atomic_write_json(ORDERS_FILE, orders)


def _lookup_ltp(chain: OptionChain, leg: Leg) -> Optional[float]:
    book = chain.calls if leg.opt_type == "CALL" else chain.puts
    entry = book.get(leg.strike)
    return entry.ltp if entry is not None else None


class Broker(ABC):
    """Two operations only: open a leg, close a leg. Everything else
    (verticals, condors, rolls) is composed from these by risk.py."""

    @abstractmethod
    def place_leg(self, leg: Leg) -> OrderResult:
        """Open `leg` (qty>0 => BUY to open long, qty<0 => SELL to open short)."""

    @abstractmethod
    def close_leg(self, leg: Leg) -> OrderResult:
        """Close a previously opened `leg` (reverses the original action)."""


class PaperBroker(Broker):
    """Fills instantly at current chain LTP. No slippage model (spec silent
    on it) -- deterministic and good enough for paper mode + replay smoke."""

    def __init__(self, chain_provider: Callable[[], OptionChain]):
        self._chain_provider = chain_provider

    def place_leg(self, leg: Leg) -> OrderResult:
        chain = self._chain_provider()
        ltp = _lookup_ltp(chain, leg)
        action = "BUY" if leg.qty > 0 else "SELL"
        if ltp is None:
            result = OrderResult(f"PAPER-{uuid.uuid4().hex[:8]}", "REJECTED", 0.0, leg, action,
                                  reason="strike not in chain")
        else:
            result = OrderResult(f"PAPER-{uuid.uuid4().hex[:8]}", "FILLED", ltp, leg, action)
        _persist_order(result)
        return result

    def close_leg(self, leg: Leg) -> OrderResult:
        chain = self._chain_provider()
        ltp = _lookup_ltp(chain, leg)
        # closing reverses the original opening action
        action = "SELL" if leg.qty > 0 else "BUY"
        if ltp is None:
            result = OrderResult(f"PAPER-{uuid.uuid4().hex[:8]}", "REJECTED", 0.0, leg, action,
                                  reason="strike not in chain")
        else:
            result = OrderResult(f"PAPER-{uuid.uuid4().hex[:8]}", "FILLED", ltp, leg, action)
        _persist_order(result)
        return result


class DhanBroker(Broker):
    """Live/sandbox broker via dhanhq. dhanhq is imported here (not at module
    scope) so the rest of the system runs without the package installed."""

    def __init__(self, client_id: str, access_token: str, security_id_lookup: Callable[[Leg], str]):
        from dhanhq import dhanhq  # guarded import -- only needed for live/sandbox mode

        self._dhan = dhanhq(client_id, access_token)
        self._security_id_lookup = security_id_lookup

    def _place(self, leg: Leg, action: str) -> OrderResult:
        security_id = self._security_id_lookup(leg)
        try:
            resp = self._dhan.place_order(
                security_id=security_id,
                exchange_segment=self._dhan.NSE_FNO,
                transaction_type=self._dhan.BUY if action == "BUY" else self._dhan.SELL,
                quantity=abs(leg.qty),
                order_type=self._dhan.MARKET,
                product_type=self._dhan.MARGIN,
                price=0,
            )
            order_id = str(resp.get("data", {}).get("orderId", "UNKNOWN"))
            status = "FILLED" if resp.get("status") == "success" else "REJECTED"
            avg_price = float(resp.get("data", {}).get("averagePrice", 0.0))
            result = OrderResult(order_id, status, avg_price, leg, action)
        except Exception as exc:  # broker/network failure -> reject, never crash the loop
            result = OrderResult(f"ERR-{uuid.uuid4().hex[:8]}", "REJECTED", 0.0, leg, action, reason=str(exc))
        _persist_order(result)
        return result

    def place_leg(self, leg: Leg) -> OrderResult:
        return self._place(leg, "BUY" if leg.qty > 0 else "SELL")

    def close_leg(self, leg: Leg) -> OrderResult:
        return self._place(leg, "SELL" if leg.qty > 0 else "BUY")
