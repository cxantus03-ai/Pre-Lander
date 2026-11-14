"""Feature extraction for market matching.

Turns free-text market titles and rules into structured facts that can be
compared deterministically: tokens, named entities, numeric thresholds with
their direction, deadline dates, time zones, resolution sources and
exceptional-settlement clauses.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from ..core.domain import Market

STOPWORDS = set(
    """a an the will be is are was were to of in on at by for from with and or as it its this that these those
    yes no market resolve resolves resolved resolution if then than there their what which who whom when where
    how does do did any all each other into within during before after above below over under more less least most
    reach hit get have has had not more than or higher lower equal between per one two up down end 's""".split()
)

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9

SYNONYMS = {
    "fed": "federal reserve",
    "fomc": "federal reserve fomc",
    "btc": "bitcoin",
    "eth": "ethereum",
    "us": "united states",
    "u.s.": "united states",
    "usa": "united states",
    "gop": "republican",
    "dem": "democratic",
    "dems": "democratic",
    "potus": "president",
    "yoy": "year over year",
    "cpi-u": "cpi",
    "jan": "january", "feb": "february", "mar": "march", "apr": "april", "jun": "june", "jul": "july",
    "aug": "august", "sep": "september", "sept": "september", "oct": "october", "nov": "november", "dec": "december",
}

KNOWN_SOURCES = [
    "associated press", "ap", "reuters", "bureau of labor statistics", "bls", "federal reserve", "espn", "nhl", "nba",
    "nfl", "mlb", "national hurricane center", "noaa", "national weather service", "coinbase", "binance", "cf benchmarks",
    "coingecko", "box office mojo", "congress.gov", "office of management and budget", "bureau of economic analysis",
    "cme", "lbma", "fox news", "cnn", "new york times", "wall street journal", "politico", "decision desk",
    "official", "consensus of credible reporting",
]

# clauses that change payout in exceptional situations
CLAUSES = {
    "fair_price": r"fair price",
    "fifty_fifty": r"50[\-/ ]?50|fifty[\- ]fifty",
    "cancellation": r"cancel+ed|cancel+ation|postpone",
    "void": r"\bvoid\b|refund",
    "overtime": r"overtime|extra time",
    "tie": r"\btie\b|\bties\b|draw",
    "recount": r"recount|certif",
    "early_close": r"close (?:and expire )?early|expire early",
    "dispute": r"disput",
}

TZ_RE = re.compile(r"\b(ET|EST|EDT|UTC|GMT|PT|PST|PDT|CT|CST|CDT)\b")

NUM_RE = re.compile(
    r"(?P<dir>above|over|greater than|more than|at least|exceed(?:s|ing)?|higher than|reach(?:es)?|hit|below|under|less than|lower than|at most|>=|<=|>|<|↑|↓)?\s*"
    r"(?P<cur>\$)?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?P<unit>%|k\b|m\b|b\b|bn\b|million|billion|thousand|bps|basis points)?"
    r"(?:\s*(?P<post>or (?:more|higher|above|greater|stronger|later|less|lower|below|fewer|weaker|earlier)|\+))?",
    re.IGNORECASE,
)

DATE_PATTERNS = [
    re.compile(r"\b(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<year>20\d{2})\b", re.I),
    re.compile(r"\b(?P<year>20\d{2})-(?P<month>\d{2})-(?P<day>\d{2})\b"),
]
YEAR_BOUND = re.compile(r"\b(?P<kw>before|by|in|during|end of|through)\s+(?P<year>20\d{2})\b", re.I)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_text(s: str) -> str:
    s = strip_accents(s or "").lower()
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", s)  # year-over-year -> year over year
    s = re.sub(r"[^a-z0-9$%.\-' ]+", " ", s)
    words = []
    for w in s.split():
        w = w.strip(".'-")
        if not w:
            continue
        words.append(SYNONYMS.get(w, w))
    return re.sub(r"\s+", " ", " ".join(words)).strip()


def tokens(s: str) -> set[str]:
    out = set()
    for w in normalize_text(s).split():
        if w in STOPWORDS or len(w) < 2:
            continue
        if re.fullmatch(r"\$?\d[\d,.]*%?", w):
            continue  # numbers are compared structurally, not lexically
        out.add(stem(w))
    return out


def stem(w: str) -> str:
    """Very small plural stemmer: passes->pass, cuts->cut, policies->policy."""
    if len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("sses", "shes", "ches", "xes", "zes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


ENTITY_RE = re.compile(r"\b([A-Z][a-zA-Z'’.&-]+(?:\s+(?:of|the|de|van|von)?\s*[A-Z][a-zA-Z'’.&-]+)*)")
ENTITY_STOP = {"Will", "Yes", "No", "The", "If", "This", "Before", "After", "By", "In", "On", "At", "Resolves", "Market",
               "Otherwise", "Source", "Note", "Who", "What", "Which", "When", "How", "Does", "Do", "And", "Or", "For", "Is",
               "Are", "Be", "ET", "EST", "EDT", "UTC", "GMT", "PT", "A", "An"} | {m.capitalize() for m in MONTHS}


def entities(s: str) -> set[str]:
    out = set()
    for m in ENTITY_RE.finditer(s or ""):
        ent = m.group(1).strip(" .'’")
        parts = [p for p in ent.split() if p not in ENTITY_STOP]
        while parts and parts[0].lower() in ("the", "of", "de", "van", "von"):
            parts.pop(0)
        if not parts:
            continue
        e = normalize_text(" ".join(parts))
        if e and e not in STOPWORDS and not e.isdigit():
            out.add(e)
    return out


@dataclass(frozen=True)
class Threshold:
    value: Decimal
    direction: str  # "gt" | "lt" | "eq"
    unit: str  # "%", "$", "", "bps"

    def to_dict(self) -> dict:
        return {"value": format(self.value, "f"), "direction": self.direction, "unit": self.unit}


def _scale(unit: str | None) -> Decimal:
    u = (unit or "").lower()
    return {"k": Decimal(1_000), "thousand": Decimal(1_000), "m": Decimal(1_000_000), "million": Decimal(1_000_000),
            "b": Decimal(1_000_000_000), "bn": Decimal(1_000_000_000), "billion": Decimal(1_000_000_000)}.get(u, Decimal(1))


def thresholds(s: str) -> set[Threshold]:
    out: set[Threshold] = set()
    text = s or ""
    for m in NUM_RE.finditer(text):
        raw = m.group("num").replace(",", "")
        unit = (m.group("unit") or "").lower()
        cur = m.group("cur")
        direction_word = (m.group("dir") or "").lower()
        post = (m.group("post") or "").lower()
        # ignore bare years, days of month and ordinals unless clearly a threshold
        if not (cur or unit or direction_word or post):
            continue
        try:
            value = Decimal(raw) * _scale(unit)
        except InvalidOperation:
            continue
        if not cur and not unit and 1900 <= value <= 2100 and not direction_word:
            continue
        if direction_word in ("below", "under", "less than", "lower than", "at most", "<", "<=", "↓") or any(
            w in post for w in ("less", "lower", "below", "fewer")
        ):
            d = "lt"
        elif direction_word or post:
            d = "gt"
        else:
            d = "eq"
        u = "%" if unit == "%" else ("bps" if unit in ("bps", "basis points") else ("$" if cur else ""))
        out.add(Threshold(value.normalize(), d, u))
    return out


def dates(s: str) -> set[date]:
    out: set[date] = set()
    for pat in DATE_PATTERNS:
        for m in pat.finditer(s or ""):
            gd = m.groupdict()
            try:
                if gd.get("month"):
                    month = int(gd["month"])
                else:
                    mon = gd["mon"].lower().rstrip(".")
                    month = MONTHS.get(mon) or MONTHS[mon[:3]]
                out.add(date(int(gd["year"]), month, int(gd["day"])))
            except (KeyError, ValueError):
                continue
    for m in YEAR_BOUND.finditer(s or ""):
        y = int(m.group("year"))
        kw = m.group("kw").lower()
        out.add(date(y - 1, 12, 31) if kw == "before" else date(y, 12, 31))
    return out


def sources(m: Market) -> set[str]:
    text = normalize_text(" ".join([m.full_rules, " ".join(m.resolution_sources)]))
    found = {s for s in KNOWN_SOURCES if re.search(rf"\b{re.escape(s)}\b", text)}
    for s in m.resolution_sources:
        if s:
            found.add(normalize_text(s))
    found = {("bureau of labor statistics" if s == "bls" else "associated press" if s == "ap" else s) for s in found}
    found.discard("official")
    return found


def clauses(text: str) -> set[str]:
    t = (text or "").lower()
    return {name for name, pat in CLAUSES.items() if re.search(pat, t)}


def timezones(text: str) -> set[str]:
    norm = {"EST": "ET", "EDT": "ET", "PST": "PT", "PDT": "PT", "CST": "CT", "CDT": "CT", "GMT": "UTC"}
    return {norm.get(t, t) for t in TZ_RE.findall(text or "")}


NEGATION_RE = re.compile(r"\b(not|no longer|fail to|fails to|won't|will not|lose|loses)\b", re.I)


@dataclass
class MarketFeatures:
    key: str
    exchange: str
    tokens: set[str]
    title_tokens: set[str]
    entities: set[str]
    thresholds: set[Threshold]
    deadline_dates: set[date]
    close_date: date | None
    sources: set[str]
    clauses: set[str]
    timezones: set[str]
    negated: bool
    outcome_label: str | None
    category: str
    has_rules: bool
    extra: dict = field(default_factory=dict)


def extract(m: Market) -> MarketFeatures:
    title_text = " ".join(t for t in [m.title, m.outcome_label or "", m.yes_label if m.yes_label not in ("Yes", "No") else ""] if t)
    rules = m.full_rules
    all_text = f"{title_text}\n{rules}"
    title_dates = dates(title_text)
    rule_dates = dates(rules)
    close = m.close_time.date() if isinstance(m.close_time, datetime) else None
    return MarketFeatures(
        key=m.key,
        exchange=m.exchange,
        tokens=tokens(title_text + " " + (m.event_title or "")),
        title_tokens=tokens(title_text),
        entities=entities(title_text) | entities(m.event_title or ""),
        thresholds=thresholds(title_text) or thresholds(m.rules_primary.split(".")[0] if m.rules_primary else ""),
        deadline_dates=title_dates or rule_dates,
        close_date=close,
        sources=sources(m),
        clauses=clauses(rules),
        timezones=timezones(all_text),
        negated=bool(NEGATION_RE.search(m.title or "")),
        outcome_label=normalize_text(m.outcome_label) if m.outcome_label else None,
        category=m.category,
        has_rules=bool(rules.strip()),
    )
