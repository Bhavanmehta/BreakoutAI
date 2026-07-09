# Alpha watch

Lightweight, manual log of the live **benchmark alpha** — do the site's published
calls beat just holding the index over the same tracking window? Diagnostic only:
this is *not* wired into scoring, it's a sanity check on whether the suggestions
are actually adding value as the resolved sample grows.

**Source:** `data/performance.json` (IN) and `data/us/performance.json` (US),
`analytics.benchmark` block, rewritten every scan by `backend/build_performance.py`.

**What "alpha" means here:** mean per-call `(call window return − index window return)`
over the ~`PERF_TRACK_BARS`-day tracking window. `beat_rate` = share of calls that
beat the index. `n` = calls with a computable window return (open + resolved);
`resolved_n` = fully graded (won/lost) calls behind `expectancy_r`.

## What to watch for
- Baseline (below) is **negative on both markets** on a thin sample. Too early to
  act on — negative alpha at `resolved_n` in the teens/20s is mostly noise.
- The question is whether alpha stays negative **as `resolved_n` grows past ~50–100**.
  If it does, the calls are genuinely trailing a passive index hold and the signal
  set / conviction gating needs another look. If it drifts toward zero-or-positive,
  the early reading was just small-sample noise.
- Also glance at `hindsight` in the same block: if the top conviction buckets don't
  show a higher hit-rate than the low ones once each bucket clears its ≥5-resolved
  threshold, the score isn't stratifying live follow-through.

## Log

| Date       | Market | resolved_n | n (alpha) | mean_alpha_pct | beat_rate | expectancy_r |
|------------|--------|-----------:|----------:|---------------:|----------:|-------------:|
| 2026-07-08 | IN     | 14         | 91        | −0.96%         | 33%       | −0.143       |
| 2026-07-08 | US     | 26         | 267       | −0.53%         | 32%       | +0.154       |

_Baseline reading — first sprint of the Hindsight Loop + benchmark work._

### How to add a row
After any scan (or a standalone `python backend/build_performance.py`), read the
numbers straight from the analytics block:

```bash
# IN
python -c "import json; a=json.load(open('data/performance.json'))['analytics']; b=a['benchmark']; print(a['resolved_n'], b['n'], b['mean_alpha_pct'], b['beat_rate'], a['expectancy']['expectancy_r'])"
# US
python -c "import json; a=json.load(open('data/us/performance.json'))['analytics']; b=a['benchmark']; print(a['resolved_n'], b['n'], b['mean_alpha_pct'], b['beat_rate'], a['expectancy']['expectancy_r'])"
```

No need to log every scan — a reading every week or two is enough to see the trend.
