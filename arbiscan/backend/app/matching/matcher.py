"""Four-stage market matching engine.

Stage 1  Candidate discovery   IDF-weighted token overlap via an inverted index,
                               filtered by date proximity.
Stage 2  Semantic comparison   entity / token / threshold / outcome similarity
                               (+ optional LLM opinion, advisory only).
Stage 3  Settlement checks     deadlines, thresholds, sources, time zones,
                               exceptional-outcome clauses, payout, orientation.
Stage 4  Classification        verified_compatible | likely_compatible |
                               incompatible | insufficient_information.

The rules are deliberately conservative: any hard difference -> incompatible,
any soft difference or missing information -> needs human review. Only a
match with *zero* warnings, or a manual approval, is verified.
"""
from __future__ import annotations

import hashlib
import math
from difflib import SequenceMatcher
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

from ..core.domain import Market, MarketStatus
from .normalize import MarketFeatures, extract


class MatchStatus(str, Enum):
    VERIFIED = "verified_compatible"
    LIKELY = "likely_compatible"
    INCOMPATIBLE = "incompatible"
    INSUFFICIENT = "insufficient_information"


@dataclass
class Check:
    code: str
    label: str
    result: str  # pass | warn | fail | unknown
    detail: str
    a: str | None = None
    b: str | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class MatchResult:
    market_a: str
    market_b: str
    candidate_score: float
    semantic_score: float
    orientation: str  # "same": YES_a == YES_b ; "inverted": YES_a == NO_b
    checks: list[Check]
    status: MatchStatus
    reasons: list[str]
    method: str = "auto"
    llm: dict | None = None
    implication: dict | None = None  # set when only thresholds differ (Type D candidate)
    extra: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        a, b = sorted([self.market_a, self.market_b])
        return hashlib.sha1(f"{a}|{b}".encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "market_a": self.market_a,
            "market_b": self.market_b,
            "candidate_score": round(self.candidate_score, 4),
            "semantic_score": round(self.semantic_score, 4),
            "orientation": self.orientation,
            "checks": [c.to_dict() for c in self.checks],
            "status": self.status.value,
            "reasons": self.reasons,
            "method": self.method,
            "llm": self.llm,
            "implication": self.implication,
        }


# predicate pairs that make two otherwise-similar questions different events
ANTONYMS = [
    ({"cut", "cuts", "lower", "lowers", "decrease", "decreases"}, {"hike", "hikes", "raise", "raises", "increase", "increases"}),
    ({"win", "wins", "winner"}, {"lose", "loses", "loser"}),
    ({"above", "over", "higher", "exceed"}, {"below", "under", "lower"}),
    ({"pass", "passes", "approve", "approved"}, {"reject", "rejected", "fail", "fails", "veto"}),
    ({"resign", "resigns", "out", "leave", "leaves"}, {"stay", "stays", "remain", "remains"}),
]
# tokens whose presence in only one title does not by itself change the event
NEUTRAL_TOKENS = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
                  "december", "year", "end", "united", "states", "federal", "reserve", "rate", "price", "market", "event", "happen",
                  "officially", "announce", "announces", "us", "u.s", "would", "interest", "make", "total", "level", "official"}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class CandidateIndex:
    def __init__(self, feats: list[MarketFeatures]):
        self.feats = {f.key: f for f in feats}
        self.postings: dict[str, list[str]] = defaultdict(list)
        df: dict[str, int] = defaultdict(int)
        for f in feats:
            for t in f.tokens | f.entities:
                self.postings[t].append(f.key)
                df[t] += 1
        n = max(1, len(feats))
        self.idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}
        self.n = n

    def weight(self, t: str) -> float:
        return self.idf.get(t, math.log(self.n + 1))

    def query(self, f: MarketFeatures, limit: int, min_score: float, max_postings: int = 2000) -> list[tuple[str, float]]:
        q_terms = f.tokens | f.entities
        if not q_terms:
            return []
        scores: dict[str, float] = defaultdict(float)
        for t in q_terms:
            plist = self.postings.get(t)
            if not plist or len(plist) > max_postings:
                continue
            w = self.weight(t)
            for key in plist:
                scores[key] += w
        q_weight = sum(self.weight(t) for t in q_terms)
        out = []
        for key, shared in scores.items():
            g = self.feats[key]
            denom = q_weight + sum(self.weight(t) for t in (g.tokens | g.entities)) - shared
            s = shared / denom if denom > 0 else 0.0
            if s >= min_score:
                out.append((key, s))
        out.sort(key=lambda kv: kv[1], reverse=True)
        return out[:limit]


def _dates_close(f: MarketFeatures, g: MarketFeatures, max_days: int = 60) -> bool:
    if f.close_date and g.close_date:
        return abs((f.close_date - g.close_date).days) <= max_days
    return True


def compare(a: Market, b: Market, fa: MarketFeatures, fb: MarketFeatures, candidate_score: float, tol_days: int = 1) -> MatchResult:
    checks: list[Check] = []
    reasons: list[str] = []
    implication = None

    # --- Stage 2: semantic similarity --------------------------------------
    tok = _jaccard(fa.title_tokens, fb.title_tokens)
    ea = {w for e in fa.entities for w in e.split()}
    eb = {w for e in fb.entities for w in e.split()}
    ent = _jaccard(ea, eb)
    thr_equal = fa.thresholds == fb.thresholds
    semantic = 0.45 * tok + 0.35 * ent + 0.2 * (1.0 if thr_equal else 0.0)

    # entities (token level, tolerant of spelling variants)
    xa, xb = set(ea - eb), set(eb - ea)
    for t in list(xa):
        twin = next((u for u in xb if len(t) >= 5 and SequenceMatcher(None, t, u).ratio() >= 0.85), None)
        if twin:
            xa.discard(t)
            xb.discard(twin)
    if ea and eb and xa and xb:
        checks.append(Check("participants", "Participants / subjects", "fail",
                            "Each market names people or organisations the other does not.", ", ".join(sorted(xa)), ", ".join(sorted(xb))))
    if not fa.entities or not fb.entities:
        checks.append(Check("entities", "Named entities", "unknown", "Could not extract entities from one market."))
    elif ent >= 0.6 or ea <= eb or eb <= ea:
        checks.append(Check("entities", "Named entities", "pass", "Same named entities.", ", ".join(sorted(fa.entities)), ", ".join(sorted(fb.entities))))
    elif ent >= 0.25 or min(len(ea), len(eb)) <= 1:
        checks.append(Check("entities", "Named entities", "warn", "Entity sets only partially overlap.", ", ".join(sorted(fa.entities)), ", ".join(sorted(fb.entities))))
    else:
        checks.append(Check("entities", "Named entities", "fail", "Different named entities.", ", ".join(sorted(fa.entities)), ", ".join(sorted(fb.entities))))

    # predicate / wording differences
    words_a = set(" ".join([a.title, a.outcome_label or ""]).lower().replace("?", " ").split())
    words_b = set(" ".join([b.title, b.outcome_label or ""]).lower().replace("?", " ").split())
    for x, y in ANTONYMS:
        if (words_a & x and words_b & y and not words_b & x) or (words_a & y and words_b & x and not words_b & y):
            checks.append(Check("predicate", "Event predicate", "fail", "Questions use opposite or different predicates.",
                                ", ".join(sorted(words_a & (x | y))), ", ".join(sorted(words_b & (x | y)))))
            break
    diff_a = {t for t in fa.title_tokens - fb.title_tokens if t not in NEUTRAL_TOKENS and not t.isdigit()}
    diff_b = {t for t in fb.title_tokens - fa.title_tokens if t not in NEUTRAL_TOKENS and not t.isdigit()}
    for t in list(diff_a):  # tolerate spelling variants (zelensky / zelenskyy)
        twin = next((u for u in diff_b if len(t) >= 5 and SequenceMatcher(None, t, u).ratio() >= 0.85), None)
        if twin:
            diff_a.discard(t)
            diff_b.discard(twin)
    shared = len({t for t in fa.title_tokens & fb.title_tokens if t not in NEUTRAL_TOKENS})
    overlap = shared / max(1, shared + len(diff_a) + len(diff_b))
    if diff_a and diff_b and overlap < 0.5:
        checks.append(Check("wording", "Question wording", "fail",
                            "Each question contains distinctive terms the other lacks: they likely describe different events.",
                            ", ".join(sorted(diff_a)), ", ".join(sorted(diff_b))))
    elif diff_a or diff_b:
        checks.append(Check("wording", "Question wording", "warn",
                            "Titles contain words the other lacks; the events may be defined differently.",
                            ", ".join(sorted(diff_a)) or "—", ", ".join(sorted(diff_b)) or "—"))
    else:
        checks.append(Check("wording", "Question wording", "pass", "Same substantive wording."))

    # outcome labels inside multi-outcome events
    if fa.outcome_label and fb.outcome_label:
        la, lb = fa.outcome_label, fb.outcome_label
        if la == lb or la in lb or lb in la:
            checks.append(Check("outcome", "Outcome", "pass", "Same outcome.", la, lb))
        else:
            checks.append(Check("outcome", "Outcome", "fail", "Different outcomes within the event.", la, lb))

    # --- Stage 3: settlement compatibility ----------------------------------
    ta = ", ".join(f"{t.direction} {t.value:f}{t.unit}" for t in sorted(fa.thresholds, key=lambda t: t.value))
    tb = ", ".join(f"{t.direction} {t.value:f}{t.unit}" for t in sorted(fb.thresholds, key=lambda t: t.value))
    if thr_equal:
        checks.append(Check("thresholds", "Thresholds", "pass", "Identical thresholds." if fa.thresholds else "No numeric thresholds in either market.", ta or None, tb or None))
    elif not fa.thresholds or not fb.thresholds:
        checks.append(Check("thresholds", "Thresholds", "warn", "Threshold stated in only one market.", ta or None, tb or None))
    else:
        checks.append(Check("thresholds", "Thresholds", "fail", "Different thresholds or directions.", ta, tb))
        if len(fa.thresholds) == 1 and len(fb.thresholds) == 1:
            x, y = next(iter(fa.thresholds)), next(iter(fb.thresholds))
            if x.direction == y.direction and x.unit == y.unit and x.direction in ("gt", "lt") and x.value != y.value:
                # "above high" implies "above low"; "below low" implies "below high"
                hi, lo = (a, b) if x.value > y.value else (b, a)
                implying, implied = (hi, lo) if x.direction == "gt" else (lo, hi)
                implication = {"implying": implying.key, "implied": implied.key, "direction": x.direction}

    if fa.deadline_dates and fb.deadline_dates:
        da, db = max(fa.deadline_dates), max(fb.deadline_dates)
        diff = abs((da - db).days)
        if diff <= tol_days - 1 or da == db:
            checks.append(Check("deadline", "Cutoff / event date", "pass", "Same cutoff date.", da.isoformat(), db.isoformat()))
        elif diff <= tol_days:
            checks.append(Check("deadline", "Cutoff / event date", "warn", f"Cutoff dates differ by {diff} day (time-zone boundary?).", da.isoformat(), db.isoformat()))
        else:
            checks.append(Check("deadline", "Cutoff / event date", "fail", f"Different cutoff dates ({diff} days apart).", da.isoformat(), db.isoformat()))
    elif fa.close_date and fb.close_date:
        diff = abs((fa.close_date - fb.close_date).days)
        res = "warn" if diff <= 31 else "fail"
        checks.append(Check("deadline", "Cutoff / event date", res,
                            f"No explicit cutoff in wording; market close dates differ by {diff} days." if diff else "No explicit cutoff in wording; close dates equal.",
                            fa.close_date.isoformat(), fb.close_date.isoformat()))
    else:
        checks.append(Check("deadline", "Cutoff / event date", "unknown", "Cutoff date could not be determined."))

    if fa.timezones and fb.timezones and not (fa.timezones & fb.timezones):
        checks.append(Check("timezone", "Time zone", "warn", "Rules reference different time zones.", ",".join(sorted(fa.timezones)), ",".join(sorted(fb.timezones))))

    if fa.sources and fb.sources:
        if fa.sources & fb.sources:
            checks.append(Check("sources", "Resolution source", "pass", "Shared resolution source.", "; ".join(sorted(fa.sources)), "; ".join(sorted(fb.sources))))
        else:
            checks.append(Check("sources", "Resolution source", "warn", "Different resolution sources: outcomes could diverge.", "; ".join(sorted(fa.sources)), "; ".join(sorted(fb.sources))))
    else:
        checks.append(Check("sources", "Resolution source", "unknown", "Resolution source missing for at least one market.",
                            "; ".join(sorted(fa.sources)) or None, "; ".join(sorted(fb.sources)) or None))

    only_a, only_b = fa.clauses - fb.clauses, fb.clauses - fa.clauses
    if only_a or only_b:
        checks.append(Check("exceptional", "Exceptional-outcome clauses", "warn",
                            "Rules handle cancellation / ties / early close differently; exceptional outcomes may leave uncovered exposure.",
                            ", ".join(sorted(fa.clauses)) or "none", ", ".join(sorted(fb.clauses)) or "none"))
    else:
        checks.append(Check("exceptional", "Exceptional-outcome clauses", "pass", "Same exceptional-outcome provisions detected.",
                            ", ".join(sorted(fa.clauses)) or "none", ", ".join(sorted(fb.clauses)) or "none"))

    if a.can_close_early is not None and b.can_close_early is not None and a.can_close_early != b.can_close_early:
        checks.append(Check("early_close", "Early closure", "warn", "Only one market can close early.", str(a.can_close_early), str(b.can_close_early)))

    if a.payout == b.payout:
        checks.append(Check("payout", "Payout per contract", "pass", f"Both pay ${a.payout} per winning contract.", str(a.payout), str(b.payout)))
    else:
        checks.append(Check("payout", "Payout per contract", "warn", "Different payout per contract; quantities must be scaled.", str(a.payout), str(b.payout)))

    orientation = "same"
    if fa.negated != fb.negated:
        orientation = "inverted"
        checks.append(Check("orientation", "YES/NO orientation", "warn", "One question is phrased negatively: YES on one corresponds to NO on the other. Verify manually."))
    else:
        checks.append(Check("orientation", "YES/NO orientation", "pass", "Both questions phrased in the same direction."))

    if not fa.has_rules or not fb.has_rules:
        checks.append(Check("rules", "Rule text", "unknown", "Settlement rule text unavailable for at least one market."))

    if a.status not in (MarketStatus.OPEN, MarketStatus.SUSPENDED) or b.status not in (MarketStatus.OPEN, MarketStatus.SUSPENDED):
        checks.append(Check("status", "Market status", "warn", f"Status {a.status.value} / {b.status.value}."))

    # --- Stage 4: classification ----------------------------------------------
    fails = [c for c in checks if c.result == "fail"]
    warns = [c for c in checks if c.result == "warn"]
    unknown = [c for c in checks if c.result == "unknown"]
    if fails:
        status = MatchStatus.INCOMPATIBLE
        reasons += [f"{c.label}: {c.detail}" for c in fails]
    elif any(c.code in ("deadline", "rules") for c in unknown) or semantic < 0.35:
        status = MatchStatus.INSUFFICIENT
        reasons += [f"{c.label}: {c.detail}" for c in unknown] or ["Low textual similarity."]
    elif not warns and not unknown and semantic >= 0.6:
        status = MatchStatus.VERIFIED
        reasons.append("All automated settlement checks passed with no differences detected.")
    else:
        status = MatchStatus.LIKELY
        reasons += [f"{c.label}: {c.detail}" for c in warns + unknown]
    return MatchResult(a.key, b.key, candidate_score, semantic, orientation, checks, status, reasons, implication=implication)


def eligible(m: Market) -> bool:
    return m.status in (MarketStatus.OPEN, MarketStatus.SUSPENDED)


def match_markets(markets_a: list[Market], markets_b: list[Market], *, min_candidate: float = 0.25,
                  per_market: int = 5, tol_days: int = 1) -> list[MatchResult]:
    """Match every market on exchange A against exchange B."""
    a_ok = [m for m in markets_a if eligible(m)]
    b_ok = [m for m in markets_b if eligible(m)]
    fb_list = [extract(m) for m in b_ok]
    by_key_b = {m.key: m for m in b_ok}
    index = CandidateIndex(fb_list)
    results: list[MatchResult] = []
    for m in a_ok:
        fa = extract(m)
        for key, score in index.query(fa, per_market * 3, min_candidate):
            fb = index.feats[key]
            if not _dates_close(fa, fb):
                continue
            res = compare(m, by_key_b[key], fa, fb, score, tol_days)
            if res.semantic_score < 0.3:
                continue
            results.append(res)
    # keep best few per A market
    best: dict[str, list[MatchResult]] = defaultdict(list)
    for r in results:
        best[r.market_a].append(r)
    out = []
    for lst in best.values():
        lst.sort(key=lambda r: (r.status != MatchStatus.INCOMPATIBLE, r.semantic_score), reverse=True)
        out.extend(lst[:per_market])
    return out
