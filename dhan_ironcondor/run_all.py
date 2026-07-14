"""One command to run every strategy book at once.

Each book is `main.py` launched as its own subprocess with STRATEGY set, so the
existing isolation holds: each keeps its own runtime/<strategy>/ dir (state.json,
events.jsonl, orders.json) and a crash in one book never takes down the other.
Both children's stdout/stderr are line-prefixed [condor]/[butterfly] and
interleaved into this terminal. Ctrl+C stops every child, then waits for exit.

  python run_all.py                    # both books (condor + butterfly)
  python run_all.py condor             # just one (== STRATEGY=condor python main.py)
  python run_all.py condor butterfly   # explicit list
  python run_all.py --with-dash        # also launch `streamlit run dashboard.py`

The merged dashboard reads runtime/<strategy>/ for every book, so once this is
running you get all books on the single `streamlit run dashboard.py` page.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).parent
STRATEGIES = ("condor", "butterfly")
DASH_PORT = 8501


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _pump(stream, tag: str) -> None:
    """Forward a child's merged stdout/stderr to our terminal, line-prefixed."""
    for line in stream:
        sys.stdout.write(f"[{tag}] {line}")
        sys.stdout.flush()


def _spawn(args: list[str], env: dict, tag: str) -> subprocess.Popen:
    # PYTHONUNBUFFERED + -u so each child's prints reach us line-by-line, not in
    # a block at exit. text=True gives us str lines; stderr folds into stdout.
    proc = subprocess.Popen(
        [sys.executable, "-u", *args],
        cwd=str(HERE),
        env={**env, "PYTHONUNBUFFERED": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    threading.Thread(target=_pump, args=(proc.stdout, tag), daemon=True).start()
    return proc


def main(argv: list[str]) -> int:
    args = argv[1:]
    with_dash = "--with-dash" in args
    strategies = [a for a in args if not a.startswith("-")] or list(STRATEGIES)

    unknown = [s for s in strategies if s not in STRATEGIES]
    if unknown:
        raise SystemExit(f"unknown strategy {unknown}; choose from {list(STRATEGIES)}")

    procs: list[tuple[str, subprocess.Popen]] = []
    for i, strat in enumerate(strategies):
        # Stagger launches: each book fetches prev_close from Dhan's quote API at
        # startup. Firing them in the same instant trips Dhan's rate limiter and a
        # book can come back with prev_close=None -> its entry gate never passes.
        # A short gap spaces the startup quote calls out. (fetch_prev_close also
        # retries, so this is belt-and-braces.)
        if i:
            time.sleep(2.0)
        p = _spawn([str(HERE / "main.py")], {**os.environ, "STRATEGY": strat}, strat)
        procs.append((strat, p))
        print(f"[run_all] started {strat} book (pid {p.pid})")

    if with_dash:
        if _port_in_use(DASH_PORT):
            # A Streamlit server is already on the port. Spawning another just
            # collides (Streamlit grabs the next free port, so you'd be viewing a
            # stale server on :8501 while the new one hides on :8502). The running
            # dashboard already discovers every book from runtime/, so reuse it.
            print(f"[run_all] a dashboard is already running on "
                  f"http://localhost:{DASH_PORT} -- reusing it (not starting a "
                  f"duplicate). It shows every book automatically.")
        else:
            # Streamlit is its own long-lived web server; STRATEGY is irrelevant to
            # it (the dashboard discovers every book from runtime/), but set one so
            # the config import doesn't reject an empty value. Pin the port so we
            # know exactly where it landed.
            d = _spawn(["-m", "streamlit", "run", str(HERE / "dashboard.py"),
                        "--server.port", str(DASH_PORT)],
                       {**os.environ, "STRATEGY": strategies[0]}, "dash")
            procs.append(("dash", d))
            print(f"[run_all] started dashboard (pid {d.pid}) -> "
                  f"http://localhost:{DASH_PORT}")

    print("[run_all] all up. Ctrl+C to stop everything.")

    reported: set[str] = set()
    try:
        while True:
            for tag, p in procs:
                rc = p.poll()
                if rc is not None and tag not in reported:
                    reported.add(tag)
                    print(f"[run_all] {tag} exited with code {rc}")
            if all(p.poll() is not None for _, p in procs):
                break  # nothing left to supervise
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[run_all] Ctrl+C -> stopping all children...")
    finally:
        for _, p in procs:
            if p.poll() is None:
                p.terminate()
        for _, p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()  # last resort if a child ignores terminate

    return max((p.returncode or 0) for _, p in procs)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
