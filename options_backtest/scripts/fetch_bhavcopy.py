"""
Download NSE F&O Bhavcopy for a date range, extract NIFTY (OPTIDX + FUTIDX) rows only,
and consolidate into a single CSV. Uses concurrent downloads, skips non-trading days
silently (404), and writes progress so it's resumable.
"""
import csv
import io
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import requests

OUT_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_DIR.mkdir(parents=True, exist_ok=True)
CONSOLIDATED = OUT_DIR / "nifty_fo_daily.csv"
LOG = OUT_DIR / "fetch_log.txt"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}

URL_TMPL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{ds}_F_0000.csv.zip"

# expected header names in the new UDiFF bhavcopy format (as of 2024+)
# we don't hardcode columns; we read the CSV header row directly.


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def fetch_one(d: date, session: requests.Session):
    ds = d.strftime("%Y%m%d")
    url = URL_TMPL.format(ds=ds)
    try:
        r = session.get(url, headers=HEADERS, timeout=20)
    except Exception as e:
        return d, None, f"EXC {e}"
    if r.status_code != 200:
        return d, None, f"HTTP {r.status_code}"
    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
        name = z.namelist()[0]
        raw = z.read(name).decode("utf-8", errors="ignore")
    except Exception as e:
        return d, None, f"ZIPERR {e}"
    reader = csv.DictReader(io.StringIO(raw))
    rows = []
    for row in reader:
        # UDiFF format columns include: TckrSymb / SYMBOL, FinInstrmTp / INSTRUMENT,
        # XpryDt / EXPIRY_DT, StrkPric / STRIKE_PR, OptnTp / OPTION_TYP,
        # OpnPric/HghPric/LwPric/ClsPric / OPEN/HIGH/LOW/CLOSE, SttlmPric / SETTLE_PR
        sym = row.get("TckrSymb") or row.get("SYMBOL") or ""
        if sym != "NIFTY":
            continue
        rows.append(row)
    return d, rows, "OK"


def main():
    start_str, end_str = sys.argv[1], sys.argv[2]
    start = date.fromisoformat(start_str)
    end = date.fromisoformat(end_str)
    dates = [d for d in daterange(start, end) if d.weekday() < 5]  # skip Sat/Sun
    print(f"Attempting {len(dates)} weekday dates from {start} to {end}")

    all_rows = []
    header_written = False
    ok_count = 0
    fail_count = 0
    fieldnames = None

    session = requests.Session()

    with open(LOG, "w") as logf, open(CONSOLIDATED, "w", newline="") as outf:
        writer = None
        with ThreadPoolExecutor(max_workers=10) as ex:
            futures = {ex.submit(fetch_one, d, session): d for d in dates}
            done = 0
            for fut in as_completed(futures):
                d, rows, status = fut.result()
                done += 1
                if rows is not None and status == "OK":
                    ok_count += 1
                    if rows:
                        if writer is None:
                            fieldnames = list(rows[0].keys()) + ["BizDt_fetch"]
                            writer = csv.DictWriter(outf, fieldnames=fieldnames)
                            writer.writeheader()
                        for row in rows:
                            row["BizDt_fetch"] = d.isoformat()
                            writer.writerow(row)
                else:
                    fail_count += 1
                logf.write(f"{d.isoformat()}\t{status}\trows={len(rows) if rows else 0}\n")
                if done % 25 == 0:
                    print(f"progress {done}/{len(dates)}  ok={ok_count} fail={fail_count}")

    print(f"DONE. ok={ok_count} fail={fail_count}. Consolidated -> {CONSOLIDATED}")


if __name__ == "__main__":
    main()
