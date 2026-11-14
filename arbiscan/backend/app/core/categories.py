"""Map exchange-specific categories/tags onto ArbiScan's category set."""
from __future__ import annotations

CATEGORIES = ["politics", "sports", "economics", "crypto", "weather", "entertainment", "science_tech", "finance", "other"]

_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("crypto", ("crypto", "bitcoin", "ethereum", "solana", "btc", "eth")),
    ("weather", ("weather", "climate", "temperature", "hurricane", "rain", "snow")),
    ("sports", ("sport", "nfl", "nba", "mlb", "nhl", "soccer", "football", "basketball", "baseball", "hockey", "tennis",
                "golf", "ufc", "mma", "f1", "formula 1", "cricket", "boxing", "esports", "ncaa", "premier league", "champions league")),
    ("economics", ("econom", "fed", "fomc", "inflation", "cpi", "gdp", "jobs", "unemployment", "interest rate", "recession", "tariff")),
    ("finance", ("financ", "stock", "s&p", "nasdaq", "equities", "commodit", "companies", "ipo", "earnings", "business")),
    ("politics", ("politic", "election", "congress", "senate", "president", "geopolit", "world", "government", "trump", "court")),
    ("entertainment", ("entertain", "culture", "pop culture", "movie", "music", "oscars", "grammy", "tv", "celebrit", "awards", "media")),
    ("science_tech", ("science", "tech", " ai", "artificial intelligence", "space", "health", "mentions")),
]


def normalize_category(*labels: str | None) -> str:
    text = " " + " ".join(l.lower() for l in labels if l) + " "
    if not text.strip():
        return "other"
    for cat, needles in _RULES:
        if any(n in text for n in needles):
            return cat
    return "other"
