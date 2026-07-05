"""
Layers a small corporate-event classifier on top of sentiment.py's VADER+lexicon score.

Why: plenty of market-moving headlines read as flat/neutral to word-level sentiment --
"SEBI issues show-cause notice to X" or "X wins order from Indian Railways" don't
contain obviously charged words, but a trader reads clear direction into them. VADER
(even extended with FINANCE_LEXICON) misses this because it's word-level, not about
what *kind* of corporate event just happened.

EVENT_PATTERNS is an ordered list of (name, base_bias, [regexes]); classify() returns
the FIRST category whose pattern matches (one event tag per headline, not a sum of
whichever categories happen to match) -- ordered here roughly by how the reader is
picking specificity, since narrower phrasing should win over a coincidental generic
match. base_bias is deliberately modest (+-0.15 to 0.35): a single headline should
nudge the score, not swing it.

blend() combines the event bias with the underlying VADER score, weighted by how
confident VADER already is: when VADER's own signal is strong (|score| > 0.3), the
event bias is just a small nudge (20%); when VADER reads near-neutral, the event
knowledge dominates (70%), since that's exactly where a word-level scorer has nothing
useful to say and domain context adds the most.
"""
from __future__ import annotations
import re

# (event name, base sentiment bias [-1..1], patterns -- first match wins)
EVENT_PATTERNS: list[tuple[str, float, list[re.Pattern]]] = [
    # --- bullish corporate events ---
    ("buyback", 0.25, [
        re.compile(r"\bbuy[\s-]?back\b", re.I),
    ]),
    ("dividend_bonus_split", 0.20, [
        re.compile(r"\b(special |interim |final )?dividend\b", re.I),
        re.compile(r"\bbonus shares?\b", re.I),
        re.compile(r"\bstock split\b", re.I),
    ]),
    ("order_win", 0.30, [
        re.compile(r"\bwins?\s+(a |the |major |key )?(order|contract|deal)\b", re.I),
        re.compile(r"\bbags?\s+(a |the |major |key )?(order|contract|deal)\b", re.I),
        re.compile(r"\bsecures?\s+(a |the |major |key )?(order|contract|deal)\b", re.I),
        re.compile(r"\bletter of award\b", re.I),
        re.compile(r"\breceives?\s+(a |the )?(order|purchase order)\b", re.I),
    ]),
    ("earnings_beat", 0.30, [
        re.compile(r"\bbeats?\b.{0,20}\bestimates?\b", re.I),
        re.compile(r"\brecord\b.{0,15}\b(profit|revenue|sales|quarter)\b", re.I),
        re.compile(r"\bprofit\s+(jumps|surges|soars|more than doubles)\b", re.I),
        re.compile(r"\bnet profit rises\b", re.I),
    ]),
    ("rating_upgrade", 0.25, [
        re.compile(r"\brating\s+upgrad", re.I),
        re.compile(r"\bupgrades?\s+(its |the )?rating\b", re.I),
        re.compile(r"\boutlook\s+(revised|raised)\s+to\s+positive\b", re.I),
        re.compile(r"\bupgrades?\b.{0,20}\bto\s+(aa|a|bbb)[a-z+-]*\b", re.I),
    ]),
    ("regulatory_approval", 0.20, [
        re.compile(r"\busfda\s+approval\b", re.I),
        re.compile(r"\breceives?\s+(regulatory\s+)?approval\b", re.I),
        re.compile(r"\bclears?\s+merger\b", re.I),
        re.compile(r"\bnod from\b", re.I),
    ]),
    ("expansion", 0.15, [
        re.compile(r"\b(capacity|plant)\s+expansion\b", re.I),
        re.compile(r"\bnew\s+(plant|facility|unit)\b", re.I),
        re.compile(r"\bforays?\s+into\b", re.I),
    ]),
    ("stake_acquisition", 0.15, [
        re.compile(r"\bto acquire\b", re.I),
        re.compile(r"\bacquires?\s+(a |the |majority |controlling )?stake\b", re.I),
        re.compile(r"\bcompletes?\s+acquisition\b", re.I),
    ]),
    ("promoter_buying", 0.20, [
        re.compile(r"\bpromoter\s+(increases|raises|hikes)\s+stake\b", re.I),
        re.compile(r"\bpromoter\s+buys?\s+shares\b", re.I),
    ]),

    # --- bearish corporate events ---
    ("sebi_penalty", -0.30, [
        re.compile(r"\bshow-?cause notice\b", re.I),
        re.compile(r"\bsebi\b.{0,30}\b(penalty|penalises?|penalizes?|bars?|fines?)\b", re.I),
        re.compile(r"\b(penalty|penalised|penalized|fined|barred)\b.{0,30}\bsebi\b", re.I),
        re.compile(r"\bmarket regulator (fines|penalises|penalizes|bars)\b", re.I),
    ]),
    ("fraud_probe", -0.35, [
        re.compile(r"\bfraud\b", re.I),
        re.compile(r"\b(ed|cbi|income tax)\s+raids?\b", re.I),
        re.compile(r"\bprobe\s+(ordered|launched)\b", re.I),
    ]),
    ("rating_downgrade", -0.25, [
        re.compile(r"\brating\s+downgrad", re.I),
        re.compile(r"\bdowngrades?\s+(its |the )?rating\b", re.I),
        re.compile(r"\bdowngrades?\s+(its |the )?outlook\b", re.I),
        re.compile(r"\boutlook\b.{0,20}\b(to|as)\s+negative\b", re.I),
    ]),
    ("earnings_miss", -0.25, [
        re.compile(r"\bmisses?\s+estimates?\b", re.I),
        re.compile(r"\bprofit\s+(falls|declines|plunges|drops sharply)\b", re.I),
        re.compile(r"\bnet loss\b", re.I),
        re.compile(r"\bslips? into (the )?red\b", re.I),
    ]),
    ("contract_loss", -0.25, [
        re.compile(r"\bloses?\s+(a |the |major |key )?(order|contract)\b", re.I),
        re.compile(r"\border\s+cancell?ed\b", re.I),
        re.compile(r"\bcontract\s+terminat", re.I),
    ]),
    ("debt_stress", -0.35, [
        re.compile(r"\bdefaults?\s+on\b", re.I),
        re.compile(r"\binsolvency\b", re.I),
        re.compile(r"\bnclt\b", re.I),
        re.compile(r"\bliquidation\b", re.I),
    ]),
    ("management_exit", -0.20, [
        re.compile(r"\b(md|ceo|cfo|md\s*&\s*ceo|director)\s+(resigns?|steps? down|quits)\b", re.I),
        re.compile(r"\bresignation of\b", re.I),
    ]),
    ("guidance_cut", -0.25, [
        re.compile(r"\bcuts?\b.{0,25}\bguidance\b", re.I),
        re.compile(r"\blowers?\s+(its |the )?outlook\b", re.I),
        re.compile(r"\bguidance\s+(revised|cut)\s+(down|lower)", re.I),
    ]),
    ("promoter_pledge", -0.20, [
        re.compile(r"\bpromoter\s+pledg", re.I),
        re.compile(r"\bpledged shares?\s+(rise|increase)", re.I),
    ]),
    ("stake_sale", -0.15, [
        re.compile(r"\bpromoter\s+sells?\s+stake\b", re.I),
        re.compile(r"\bstake sale\b", re.I),
        re.compile(r"\bdivests?\b", re.I),
        re.compile(r"\bexits?\s+(the )?business\b", re.I),
    ]),
]


def classify(text: str) -> tuple[str, float] | None:
    """(event_name, base_bias) for the first matching category, or None."""
    for name, bias, patterns in EVENT_PATTERNS:
        if any(p.search(text) for p in patterns):
            return name, bias
    return None


def blend(vader_score: float, bias: float, confident_threshold: float = 0.3) -> float:
    """Combine an event's base bias with VADER's own score -- see module docstring
    for why the weighting flips based on how confident VADER already is."""
    if abs(vader_score) > confident_threshold:
        blended = 0.8 * vader_score + 0.2 * bias
    else:
        blended = 0.3 * vader_score + 0.7 * bias
    return max(-1.0, min(1.0, round(blended, 4)))
