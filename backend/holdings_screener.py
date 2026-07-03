"""
Ownership / shareholding from screener.in — a more reliable, quarterly source than
NSE's XBRL filings (NSE rate-limits aggressively and only cleanly surfaces annual
promoter %).

screener.in renders a "Shareholding Pattern" table inline in each company page with
~13 quarters of Promoters / FIIs / DIIs / Government / Public %, plus a yearly view.
Each category row carries a stable classification key in its onclick
(`Company.showShareholders('foreign_institutions', 'quarterly', ...)`), which we parse
against instead of the display text.

Returns the SAME dict shape as holdings.fetch_holdings() so it's a drop-in source:
  {as_of, promoter, fii, dii, mf, public, history:[{date,promoter,fii,dii,mf,public}...]}
`history` is newest-first (history[0] == as_of), matching the NSE path's convention.

MF (mutual funds) is a sub-category of DIIs on screener and isn't in the top-level
table, so `mf` is None here (DII already includes it) — the card's headline story is
promoter / FII / DII, which this gives cleanly across every quarter.
"""
from __future__ import annotations
import re
import html

import requests

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

# screener classification key -> our bucket
_CLS = {
    "promoters": "promoter",
    "foreign_institutions": "fii",
    "domestic_institutions": "dii",
    "public": "public",
}

_MONTHS = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
           "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12"}


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"})
    return s


def _norm_date(label: str) -> str:
    """'Jun 2023' -> '2023-06' (sortable). Falls back to the raw label."""
    m = re.match(r"([A-Za-z]{3})\s+(\d{4})", label.strip())
    if not m:
        return label.strip()
    mon = _MONTHS.get(m.group(1).lower())
    return f"{m.group(2)}-{mon}" if mon else label.strip()


def _pct(s: str):
    s = s.replace("%", "").replace(",", "").strip()
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def parse_shareholding(page_html: str) -> dict | None:
    """Parse the quarterly Shareholding Pattern table out of a screener company page."""
    start = page_html.find('id="shareholding"')
    if start == -1:
        return None
    seg = page_html[start:start + 12000]
    ti = seg.find("<table")
    te = seg.find("</table>")
    if ti == -1 or te == -1:
        return None
    table = seg[ti:te]

    # Header quarter labels (skip the first, empty, corner cell).
    thead = re.search(r"<thead>(.*?)</thead>", table, re.S)
    if not thead:
        return None
    ths = re.findall(r"<th[^>]*>(.*?)</th>", thead.group(1), re.S)
    dates = [html.unescape(re.sub(r"<[^>]+>", "", h)).strip() for h in ths]
    dates = [d for d in dates if d]  # drop the empty corner
    if not dates:
        return None

    # Each category row: a classification key + one % per quarter column.
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)
    cats: dict[str, list] = {}
    for row in rows:
        cls = re.search(r"showShareholders\('([^']+)'", row)
        if not cls or cls.group(1) not in _CLS:
            continue
        vals = re.findall(r"<td>\s*([\d.,]+%?)\s*</td>", row)
        cats[_CLS[cls.group(1)]] = [_pct(v) for v in vals]

    if not cats:
        return None

    # Build per-quarter history, aligning each category's values to the date columns
    # from the right (most recent columns are always populated; older ones may be short).
    n = len(dates)
    history = []
    for i, label in enumerate(dates):
        point = {"date": _norm_date(label), "mf": None}
        for bucket, vals in cats.items():
            # right-align: vals[-1] is the latest quarter == dates[-1]
            off = i - (n - len(vals))
            point[bucket] = vals[off] if 0 <= off < len(vals) else None
        history.append(point)
    history = [p for p in history if any(p.get(b) is not None for b in _CLS.values())]
    if not history:
        return None
    history.reverse()  # newest-first

    latest = history[0]
    return {
        "as_of": latest["date"],
        "promoter": latest.get("promoter"),
        "fii": latest.get("fii"),
        "dii": latest.get("dii"),
        "mf": None,
        "public": latest.get("public"),
        "history": history,
        "source": "screener",
    }


def fetch_holdings_screener(symbol: str, session: requests.Session | None = None) -> dict | None:
    """Fetch quarterly ownership for one NSE symbol from screener.in. Tries the
    consolidated page first (falls back to standalone). Returns None on any failure."""
    s = session or make_session()
    # The base (standalone) page always exists and carries the same shareholding table;
    # consolidated is a fallback only if the base somehow lacks it.
    for path in (f"/company/{symbol}/", f"/company/{symbol}/consolidated/"):
        try:
            r = s.get(f"https://www.screener.in{path}", timeout=25)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        parsed = parse_shareholding(r.text)
        if parsed:
            return parsed
    return None


if __name__ == "__main__":
    import time
    s = make_session()
    for sym in ["RELIANCE", "TCS", "CGPOWER", "SBIN", "POLICYBZR"]:
        h = fetch_holdings_screener(sym, s)
        if not h:
            print(f"{sym:10s} -> None")
        else:
            hist = h["history"]
            print(f"{sym:10s} -> as_of {h['as_of']} | P{h['promoter']} F{h['fii']} D{h['dii']} Pub{h['public']} | {len(hist)} quarters")
            print(f"{'':12s}" + " ".join(f"{p['date']}:F{p['fii']}" for p in hist[:6]))
        time.sleep(1.0)
