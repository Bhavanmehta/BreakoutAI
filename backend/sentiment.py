"""
Local, self-computed sentiment scoring -- used by fetch_news.py (headlines) and
fetch_social.py (Reddit post titles/selftext) so sentiment coverage doesn't depend
on a provider's own entity-tagging (Marketaux only tags a matched entity on a
fraction of articles, which is why sentiment used to show up on almost no stocks
once the news-recency filter shrank the article pool per stock -- see NEWS_MAX_AGE_DAYS
in settings.py). Scoring every headline ourselves means any stock with ANY cached
headline gets a sentiment label, regardless of which provider it came from.

Base engine is VADER (vaderSentiment) -- a lexicon + rule-based sentiment analyzer
that needs no training data or model download, tuned for short, informal text (which
headlines and social posts both are). Its stock lexicon doesn't know finance-specific
words though (e.g. "beats estimates", "downgrade", "fraud probe" don't carry the
charge a trader would read into them), so FINANCE_LEXICON below extends it with a
small hand-picked set of finance terms and their VADER-scale intensities (-4..+4).
"""
from __future__ import annotations

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

import event_classifier
import settings

# Extends VADER's general-purpose lexicon with finance-specific terms so headlines
# like "Board approves buyback" or "probe into accounting fraud" score sensibly --
# VADER's stock lexicon has no opinion on most of these and either scores them
# neutral or (worse) picks up an unrelated generic sense of the word.
FINANCE_LEXICON = {
    # bullish
    "beats": 2.0, "outperform": 2.5, "upgrade": 2.5, "upgraded": 2.5,
    "buyback": 1.8, "rally": 2.5, "rallies": 2.5, "surge": 2.8, "surges": 2.8,
    "surged": 2.8, "record profit": 3.0, "record high": 2.5, "order win": 2.5,
    "bags order": 2.0, "wins order": 2.2, "expansion": 1.5, "acquires": 1.2,
    "stake buy": 1.5, "strong guidance": 2.2, "beats estimates": 2.8,
    "outlook raised": 2.3, "raises guidance": 2.3, "bullish": 2.5,
    "breakout": 1.8, "multibagger": 2.5, "turnaround": 1.8,
    # bearish
    "downgrade": -2.5, "downgraded": -2.5, "probe": -2.2, "raid": -2.5,
    "fraud": -3.2, "scam": -3.2, "default": -3.0, "defaults": -3.0,
    "layoffs": -2.2, "lawsuit": -2.0, "sued": -2.0, "penalty": -2.0,
    "penalised": -2.0, "penalized": -2.0, "plunge": -2.8, "plunges": -2.8,
    "plunged": -2.8, "crash": -2.8, "crashes": -2.8, "crashed": -2.8,
    "misses estimates": -2.6, "profit warning": -2.8, "resigns": -1.5,
    "resignation": -1.2, "bearish": -2.5, "slump": -2.2, "slumps": -2.2,
    "writedown": -2.2, "write-down": -2.2, "insolvency": -3.0,
    "bankruptcy": -3.2, "ban": -2.0, "banned": -2.0, "halted": -1.5,
    "scrutiny": -1.5, "outlook cut": -2.3, "cuts guidance": -2.3,
}

_analyzer = SentimentIntensityAnalyzer()
_analyzer.lexicon.update(FINANCE_LEXICON)


def label_for_score(score: float) -> str:
    if score >= settings.NEWS_SENTIMENT_BULLISH:
        return "Bullish"
    if score <= settings.NEWS_SENTIMENT_BEARISH:
        return "Bearish"
    return "Neutral"


def score_text(text: str) -> float:
    """Compound sentiment (-1..1) for a single piece of text."""
    return _analyzer.polarity_scores(text)["compound"]


def score_text_with_event(text: str) -> tuple[float, str | None]:
    """(blended score, matched event name or None) -- see event_classifier for why a
    single headline's word-level VADER score gets nudged by what *kind* of corporate
    event it describes (an order win, a SEBI penalty, ...), which VADER alone can't see."""
    vader = score_text(text)
    match = event_classifier.classify(text)
    if match is None:
        return vader, None
    name, bias = match
    return event_classifier.blend(vader, bias), name


def score_texts(texts: list[str]) -> dict | None:
    """Average event-blended sentiment across multiple texts (headlines, post titles) ->
    {score, label, event}, or None if there's nothing to score. Simple mean rather than
    a weighted one: with only a handful of texts per stock, weighting by recency/source
    would be tuning noise, not signal. `event` is whichever single text's classified
    event had the largest swing on the blended score -- surfaced so the label isn't a
    black box (e.g. "Bullish (order_win)")."""
    texts = [t for t in texts if t and t.strip()]
    if not texts:
        return None
    scored = [score_text_with_event(t) for t in texts]
    scores = [s for s, _ in scored]
    avg = round(sum(scores) / len(scores), 2)
    # whichever single headline's classified event had the most extreme blended score
    dominant_event = next((e for s, e in sorted(scored, key=lambda se: abs(se[0]), reverse=True) if e), None)
    return {"score": avg, "label": label_for_score(avg), "event": dominant_event}
