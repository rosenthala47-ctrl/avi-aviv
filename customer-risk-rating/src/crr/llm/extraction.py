"""Structured extraction from free text (requirement 1a): what the numbers miss.

Two extraction dimensions are model features; two are explainability only. That
split is not arbitrary — it follows the generator's own causal structure
(``crr.data.synthetic``, ``BETA_CREDIT``/``BETA_CRIME``): ``distress_level`` and
``concealment_level`` are the two latents that actually carry an independent
log-odds weight in the outcome equations (``text_distress``, ``text_concealment``),
and the narrative text is *quantile-binned from those exact latents* — so a
correct extraction of the level is, by construction, the signal a tabular
model is missing. ``stated_life_events`` and ``evasiveness_detected`` are
surface realisations of the *same* underlying draw (which template sentence got
picked), not an independent causal channel, so they are not expected to move
AUC beyond what the two levels already capture. They still earn their place:
a reviewer reading "distress=2" wants to know *why*, and "employer restructuring
mentioned, unprompted hardship enquiry" is a materially more useful audit trail
than a bare number — the same reason phase 3 reports SHAP factors as statements,
not just contribution weights.

The LLM never produces a score, a band, or a rule outcome — only these four
fields, each independently bounded and validated (see ``ExtractionResult`` in
this module). That is the structural half of the prompt-injection defence
described in ``crr.llm.prompts``: even a fully successful injection cannot
express "set risk_band to Low" because the schema has no field that means
that. The worst a hijacked extraction can do is report a wrong distress level
(bounded 0-3) or a nonsense life-event label a human reviewer sees and ignores.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

#: Extractor implementation identity, kept on every result for audit — "why did
#: this score use a stale/degraded extraction" should never require a guess.
ExtractionSource = Literal["llm", "reference", "unavailable"]

#: A short, human-readable vocabulary the prompt steers the LLM toward. Not
#: enforced as a closed set (see ExtractionResult.stated_life_events) — real
#: notes will describe events this list does not anticipate, and a rigid enum
#: would silently drop them rather than surface something imperfect but real.
SUGGESTED_LIFE_EVENT_TAGS: tuple[str, ...] = (
    "job_loss", "employer_restructuring", "household_income_loss", "business_loss_of_client",
    "medical_expense", "hardship_inquiry", "income_shortfall_expected", "multiple_lender_arrears",
    "insolvency_consideration", "debt_juggling",
)

#: Bounds every ``ExtractionResult`` must satisfy, from any extractor. This is
#: the schema half of the injection defence: a response that does not fit
#: these bounds fails validation before it ever reaches a feature column,
#: regardless of what the narrative text tried to make the model say.
_MAX_LIFE_EVENTS = 6
_MAX_LABEL_LENGTH = 64


class ExtractionResult(BaseModel):
    """The LLM's (or reference extractor's) raw structured output, validated.

    Deliberately the ONLY shape a caller can hand back from ``Extractor.extract``.
    Every field is bounded; nothing here can express a decision, only an
    observation about the text.
    """

    model_config = ConfigDict(extra="forbid")

    distress_level: int = Field(ge=0, le=3)
    distress_confidence: float = Field(ge=0, le=1)
    concealment_level: int = Field(ge=0, le=3)
    concealment_confidence: float = Field(ge=0, le=1)
    stated_life_events: list[str] = Field(default_factory=list, max_length=_MAX_LIFE_EVENTS)
    evasiveness_detected: bool = False
    evasiveness_confidence: float = Field(ge=0, le=1, default=0.0)

    @property
    def clipped_life_events(self) -> tuple[str, ...]:
        """Each tag hard-capped in length — a defence-in-depth measure against
        an injected label so long it is really trying to be a payload, not a
        label. The bound above (max 6 items) is the primary control; this is
        what stops any one of those six from being unreasonably large."""
        return tuple(tag[:_MAX_LABEL_LENGTH] for tag in self.stated_life_events)


@dataclass(frozen=True)
class TextExtraction:
    """The extraction result attached to one customer, plus the provenance a
    reviewer or an audit needs: which extractor produced it, and whether it
    is real or a fallback."""

    customer_id: str
    source: ExtractionSource
    extractor_version: str
    degraded: bool = False
    """True when no real extraction happened (LLM unavailable, timed out, or
    failed) — the caller falls back to a tabular-only score, and every field
    below is None. Distinct from ``source="reference"``, which is a real,
    if cheaper, extraction — not a failure."""
    distress_level: int | None = None
    distress_confidence: float | None = None
    concealment_level: int | None = None
    concealment_confidence: float | None = None
    stated_life_events: tuple[str, ...] = field(default_factory=tuple)
    evasiveness_detected: bool | None = None
    evasiveness_confidence: float | None = None

    @classmethod
    def from_result(cls, customer_id: str, source: ExtractionSource, extractor_version: str,
                     result: ExtractionResult) -> TextExtraction:
        return cls(
            customer_id=customer_id, source=source, extractor_version=extractor_version, degraded=False,
            distress_level=result.distress_level, distress_confidence=result.distress_confidence,
            concealment_level=result.concealment_level, concealment_confidence=result.concealment_confidence,
            stated_life_events=result.clipped_life_events,
            evasiveness_detected=result.evasiveness_detected, evasiveness_confidence=result.evasiveness_confidence,
        )

    @classmethod
    def unavailable(cls, customer_id: str, extractor_version: str = "") -> TextExtraction:
        """The fallback: tabular-only, marked degraded rather than failing
        the request (roadmap phase 7, requirement 1a)."""
        return cls(customer_id=customer_id, source="unavailable", extractor_version=extractor_version, degraded=True)


@dataclass(frozen=True)
class NarrativeBundle:
    """The free text available for one customer. Any field may be empty —
    not every customer has all three note types."""

    customer_id: str
    support_call_summary: str = ""
    underwriter_note: str = ""
    kyc_document_extract: str = ""

    def is_empty(self) -> bool:
        return not (self.support_call_summary or self.underwriter_note or self.kyc_document_extract)


class Extractor(Protocol):
    """Turns one customer's narrative bundle into a validated extraction.

    Implementations: ``ReferenceExtractor`` (deterministic, no network, the
    default and the test/CI path) and ``AnthropicExtractor`` (the real LLM,
    behind the same interface — the same "runs with no infrastructure by
    default, real backend is a swap" pattern as every other backend in this
    project). Never raises for a malformed or hostile narrative — a caller
    that cannot get a valid extraction returns ``TextExtraction.unavailable``,
    not an exception, so one bad note degrades one customer's score rather
    than the request.
    """

    version: str

    def extract(self, bundle: NarrativeBundle) -> TextExtraction: ...
