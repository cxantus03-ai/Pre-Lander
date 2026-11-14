"""Optional LLM second opinion on settlement equivalence (ADVISORY ONLY).

Disabled by default (ARBISCAN_LLM_MATCHING_ENABLED=false). When enabled, a
"likely compatible" match is sent with both contracts' rule texts to Claude
for a structured judgment. The result is stored on the match and can only
*add caution*: a "not equivalent" or "uncertain" verdict adds a warning check.
It can never promote a match to verified - that requires deterministic checks
or a human approval.

Requires `pip install anthropic` and ARBISCAN_ANTHROPIC_API_KEY on the backend.
"""
from __future__ import annotations

import json
import logging

from ..config import Settings
from ..core.domain import Market
from .matcher import Check, MatchResult, MatchStatus

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "properties": {
        "equivalent": {"type": "string", "enum": ["yes", "no", "uncertain"]},
        "orientation": {"type": "string", "enum": ["same", "inverted", "unknown"]},
        "concerns": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["equivalent", "orientation", "concerns"],
    "additionalProperties": False,
}

SYSTEM = (
    "You compare two prediction-market contracts and decide whether they settle on exactly the same real-world "
    "outcome. Be strict: different deadlines, thresholds, time zones, resolution sources, tie/cancellation handling "
    "or event definitions mean they are NOT equivalent. 'orientation' is 'same' if YES on A pays exactly when YES on "
    "B pays, 'inverted' if YES on A pays exactly when NO on B pays. List every concrete concern."
)


def _describe(m: Market) -> str:
    return (f"Exchange: {m.exchange}\nTitle: {m.title}\nOutcome: {m.outcome_label or '-'}\n"
            f"Close: {m.close_time}\nSources: {', '.join(m.resolution_sources) or '-'}\nRules:\n{m.full_rules[:6000]}")


class LLMJudge:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None
        if settings.llm_matching_enabled and settings.anthropic_api_key:
            try:
                import anthropic

                self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
            except ImportError:
                log.warning("LLM matching enabled but the 'anthropic' package is not installed")

    @property
    def enabled(self) -> bool:
        return self.client is not None

    async def judge(self, a: Market, b: Market) -> dict | None:
        if not self.client:
            return None
        try:
            resp = await self.client.beta.messages.create(
                model=self.settings.llm_model,
                max_tokens=2000,
                system=SYSTEM,
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{"role": "user", "content": f"Contract A\n{_describe(a)}\n\nContract B\n{_describe(b)}"}],
            )
        except Exception as exc:  # noqa: BLE001 - advisory feature must never break matching
            log.warning("LLM judgment failed: %s", exc)
            return None
        if resp.stop_reason == "refusal":
            return {"equivalent": "uncertain", "orientation": "unknown", "concerns": ["model declined to assess"]}
        text = next((blk.text for blk in resp.content if blk.type == "text"), "")
        try:
            return json.loads(text)
        except ValueError:
            return None


def apply_llm_opinion(match: MatchResult, opinion: dict | None) -> MatchResult:
    """Record the opinion; only ever adds caution, never promotes."""
    if not opinion:
        return match
    match.llm = opinion | {"advisory_only": True}
    eq = opinion.get("equivalent")
    if eq != "yes" or (opinion.get("orientation") not in ("unknown", match.orientation)):
        match.checks.append(Check("llm", "LLM second opinion (advisory)", "warn",
                                  "LLM flagged possible settlement differences: " + "; ".join(opinion.get("concerns") or [eq or "uncertain"])))
        if match.status == MatchStatus.VERIFIED and match.method != "manual":
            match.status = MatchStatus.LIKELY
    return match
