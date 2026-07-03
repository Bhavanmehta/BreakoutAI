"""
Split / bonus adjustment.

Raw NSE prices (from jugaad-data) are NOT adjusted for splits and bonuses, so a
stock that did a 1:1 bonus shows a fake ~50% overnight "crash". This module:

  1. fetch_corporate_actions() -> pulls the official event list from NSE.
  2. parse_ratio()             -> reads the free-text "subject" into a number:
                                  how many shares you end up with per share held.
  3. apply_adjustments()       -> scales the older prices down so the history is
                                  one smooth line (matches professionally-adjusted data).

parse_ratio() is the delicate bit — a misread ratio silently corrupts a stock's
history — so it has a built-in self-test (run `python adjust_for_splits.py`).
"""
from __future__ import annotations
from datetime import date
import re
import pandas as pd
import requests

_NSE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-actions",
}


def _nse_session() -> requests.Session:
    """A session primed with NSE cookies so the API doesn't reject us."""
    s = requests.Session()
    s.headers.update(_NSE_HEADERS)
    s.get("https://www.nseindia.com", timeout=15)
    s.get("https://www.nseindia.com/companies-listing/corporate-filings-actions", timeout=15)
    return s


def fetch_corporate_actions(symbol: str | None, from_date: date, to_date: date) -> pd.DataFrame:
    """Return corporate-action events. If `symbol` is None, returns the whole
    market (useful for bulk work); otherwise filters to that symbol."""
    s = _nse_session()
    url = ("https://www.nseindia.com/api/corporates-corporateActions?index=equities"
           f"&from_date={from_date.strftime('%d-%m-%Y')}&to_date={to_date.strftime('%d-%m-%Y')}")
    r = s.get(url, timeout=30)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    if len(df) == 0:
        return df
    if symbol is not None and "symbol" in df.columns:
        df = df[df["symbol"] == symbol].copy()
    return df


def parse_ratio(subject: str) -> float | None:
    """Turn a corporate-action description into a price-adjustment multiplier
    (the factor by which the share count grows; price divides by this).

    Returns None for events that don't affect price (dividends, buybacks, AGMs).

    Examples:
      "Bonus 1:1"                                    -> 2.0   (1 free per 1 held)
      "Bonus 3:1"                                    -> 4.0   (3 free per 1 held)
      "Bonus 1:2"                                    -> 1.5   (1 free per 2 held)
      "Face Value Split ... From Rs 10 To Rs 2"      -> 5.0   (10/2)
      "Dividend - Rs 8 Per Share"                    -> None
    """
    if not subject:
        return None
    text = subject.strip()
    low = text.lower()

    # --- Bonus "a:b" -> (a + b) / b ---
    if "bonus" in low:
        m = re.search(r"(\d+)\s*:\s*(\d+)", text)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b > 0:
                return (a + b) / b
        return None

    # --- Face value split "From Rs X To Rs Y" -> X / Y ---
    if "split" in low or "sub-division" in low or "face value" in low:
        nums = re.findall(r"(?:rs\.?|re\.?)\s*([\d.]+)", low)
        if len(nums) >= 2:
            old_fv, new_fv = float(nums[0]), float(nums[1])
            if new_fv > 0 and old_fv > new_fv:
                return old_fv / new_fv
        # some splits are written as "a:b" too
        m = re.search(r"(\d+)\s*:\s*(\d+)", text)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > 0 and b > 0 and a != b:
                return max(a, b) / min(a, b)
        return None

    # Dividends, buybacks, AGMs, rights, etc. -> no price adjustment here.
    return None


def apply_adjustments(prices: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Given raw daily prices (columns: date, open, high, low, close, volume) and
    an NSE corporate-actions DataFrame, return prices with OHLC back-adjusted for
    splits/bonuses. Volume is scaled up correspondingly so it stays comparable."""
    prices = prices.copy()
    if events is None or len(events) == 0 or "subject" not in events.columns:
        return prices

    factor = pd.Series(1.0, index=prices.index)
    for _, row in events.iterrows():
        ratio = parse_ratio(str(row.get("subject", "")))
        ex = row.get("exDate")
        if not ratio or not ex:
            continue
        try:
            ex_date = pd.to_datetime(ex, dayfirst=True).tz_localize(None).normalize()
        except Exception:
            continue
        # Every bar strictly BEFORE the ex-date is on the "old" scale -> shrink it.
        mask = prices["date"] < ex_date
        factor[mask] *= (1.0 / ratio)

    for col in ["open", "high", "low", "close"]:
        if col in prices.columns:
            prices[col] = prices[col].values * factor.values
    if "volume" in prices.columns:
        # fewer (larger) shares before a split => scale volume the other way
        prices["volume"] = prices["volume"].values / factor.values
    return prices


# --- Self-test: run `python adjust_for_splits.py` ---------------------------
def _self_test():
    cases = {
        "Bonus 1:1": 2.0,
        "Bonus 3:1": 4.0,
        "Bonus 1:2": 1.5,
        "Bonus 5:1": 6.0,
        "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share": 5.0,
        "Face Value Split (Sub-Division) - From Rs10/- Per Share To Re 1/- Per Share": 10.0,
        "Dividend - Rs 8 Per Share": None,
        "Buy Back": None,
        "Annual General Meeting": None,
    }
    ok = True
    for subject, expected in cases.items():
        got = parse_ratio(subject)
        status = "PASS" if got == expected else "FAIL"
        if got != expected:
            ok = False
        print(f"  [{status}] {subject[:55]:55s} -> {got} (expected {expected})")
    print("\nAll parser tests passed." if ok else "\nSOME TESTS FAILED.")
    return ok


if __name__ == "__main__":
    _self_test()
