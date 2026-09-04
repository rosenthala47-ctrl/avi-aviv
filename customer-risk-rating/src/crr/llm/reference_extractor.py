"""A deterministic, offline reference extractor — the zero-infrastructure
default, same role as every other in-memory implementation in this project.

Read this as a cheap NLP baseline, in the spirit of the bag-of-words check in
``scripts/validate_dataset.py``, not as a stand-in for the real extractor. It
is intentionally a hand-built keyword scorer, not a lookup against the
synthetic generator's own phrase banks — reusing the generator's exact
sentences would make this an oracle in disguise and its measured accuracy
meaningless, exactly the trap ``docs/ROADMAP.md``'s phase-7 caveat about
bag-of-words already warns against for this corpus. The keyword lists below
were written from general judgement about how English and Hebrew financial
notes describe distress and concealment, not copied from
``crr.data.narratives``, so this extractor's measured Cohen's kappa is a real
(if modest) number — a floor a real LLM should clear by a wide margin, not a
result rigged to look good.

Its purpose: the test/CI/no-API-key path end to end, a concrete "what does
this cost with zero infrastructure" data point, and a sanity baseline the
Anthropic-backed extractor's own measured kappa can be compared against.
"""

from __future__ import annotations

from crr.llm.extraction import ExtractionResult, NarrativeBundle, TextExtraction

VERSION = "reference-v1"

# (phrase, severity 1-3). Matched case-insensitively (a no-op for Hebrew, which
# has no case) against the concatenated narrative text. Severity reflects how
# strongly the single phrase, alone, implies the signal — not tuned against
# this corpus's actual label distribution.
_DISTRESS_SIGNALS: tuple[tuple[str, int], ...] = (
    ("restructuring", 2), ("lost their largest client", 2), ("lost my job", 3), ("employment ended", 3),
    ("stopped working", 2), ("medical expense", 2), ("medical issue", 2), ("arrears", 3), ("overdue", 2),
    ("cannot afford", 2), ("can't afford", 2), ("will not be able to", 3), ("won't be able to", 3),
    ("missed payment", 2), ("hardship", 2), ("payment holiday", 1), ("juggling", 3), ("insolvency", 3),
    ("minimum payments", 2), ("tighter than usual", 1), ("delayed salary", 1), ("severance", 2),
    ("covering payroll from personal funds", 3), ("negative", 1), ("shortfall", 2),
    ("ארגון מחדש", 2), ("פוטר", 3), ("פיטורים", 3), ("איבד את העבודה", 3), ("הפסיק לעבוד", 2),
    ("הפסיקה לעבוד", 2), ("הוצאה רפואית", 2), ("בעיה רפואית", 2), ("פיגור", 2), ("באיחור", 2),
    ("לא יוכל לעמוד", 3), ("לא תוכל לעמוד", 3), ("מצוקה", 2), ("הקפאת תשלום", 1), ("מגלגל", 3),
    ("חדלות פירעון", 3), ("תשלומי מינימום", 2), ("פיצויים", 2), ("החודש היה מאתגר", 1),
)

_CONCEALMENT_SIGNALS: tuple[tuple[str, int], ...] = (
    ("preferred not to say", 2), ("changed the subject", 2), ("declined to", 2), ("would not identify", 3),
    ("refused to identify", 3), ("refused to", 2), ("could not answer", 2), ("not documented", 2),
    ("no supporting documentation", 2), ("without providing documentation", 2), ("third party", 2),
    ("intermediate holding", 2), ("not evidenced", 2), ("do not show up as one transaction", 3),
    ("travelling and unreachable", 2), ("ended the call abruptly", 3), ("reporting threshold", 2),
    ("family gift", 1), ("no discernible commercial rationale", 3), ("conflict with the register", 3),
    ("העדיף שלא", 2), ("שינה נושא", 2), ("סירב", 3), ("לא ידע להשיב", 2), ("ללא תיעוד", 2),
    ("ללא מסמכים", 2), ("צד שלישי", 2), ("חברות אחזקה מתווכות", 2), ("אינו מגובה בראיות", 2),
    ("לא יופיעו כעסקה אחת", 3), ('בחו"ל ולא זמין', 2), ("ניתק את השיחה", 3), ("מתחת לסף הדיווח", 2),
    ("מתנה משפחתית", 1), ("חסר היגיון מסחרי", 3), ("סותרים את תמצית הרישום", 3),
)

# Subset of concealment signals that describe an ACTIVE dodge, not passive
# structural opacity — see the module docstring on why these are separated.
_EVASIVE_ACT_PHRASES: frozenset[str] = frozenset(
    {
        "changed the subject", "declined to", "refused to identify", "refused to", "would not identify",
        "could not answer", "ended the call abruptly", "travelling and unreachable",
        "שינה נושא", "סירב", "לא ידע להשיב", "ניתק את השיחה", 'בחו"ל ולא זמין',
    }
)

_LIFE_EVENT_SIGNALS: tuple[tuple[str, str], ...] = (
    ("restructuring", "employer_restructuring"), ("ארגון מחדש", "employer_restructuring"),
    ("stopped working", "household_income_loss"), ("הפסיק לעבוד", "household_income_loss"),
    ("הפסיקה לעבוד", "household_income_loss"),
    ("lost their largest client", "business_loss_of_client"),
    ("medical", "medical_expense"), ("רפואית", "medical_expense"),
    ("payment holiday", "hardship_inquiry"), ("hardship", "hardship_inquiry"), ("הקפאת תשלום", "hardship_inquiry"),
    ("will not be able to", "income_shortfall_expected"), ("won't be able to", "income_shortfall_expected"),
    ("לא יוכל לעמוד", "income_shortfall_expected"), ("לא תוכל לעמוד", "income_shortfall_expected"),
    ("arrears with two other lenders", "multiple_lender_arrears"), ("בפיגור מול שני", "multiple_lender_arrears"),
    ("employment ended", "job_loss"), ("lost my job", "job_loss"), ("פוטר", "job_loss"), ("פיטורים", "job_loss"),
    ("insolvency", "insolvency_consideration"), ("חדלות פירעון", "insolvency_consideration"),
    ("juggling", "debt_juggling"), ("מגלגל", "debt_juggling"),
)

_MAX_LIFE_EVENTS = 6


def _hits(lowered_text: str, signals: tuple[tuple[str, int], ...]) -> list[int]:
    return [weight for phrase, weight in signals if phrase.lower() in lowered_text]


def _level_and_confidence(hits: list[int]) -> tuple[int, float]:
    """Level follows the single strongest matched phrase; two or more
    distinct hits nudge it up one tier, since independent corroborating
    mentions are more informative than one phrase repeated. Confidence grows
    with how much text evidence supports the call; zero hits is treated as
    fairly (not fully) confident the signal is genuinely absent."""
    if not hits:
        return 0, 0.70
    peak = max(hits)
    corroborated = len(hits) >= 2
    level = min(3, peak + (1 if corroborated and peak < 3 else 0))
    confidence = min(0.95, 0.55 + 0.10 * len(hits))
    return level, round(confidence, 2)


class ReferenceExtractor:
    """Deterministic keyword scorer. No network, no API key, safe default."""

    version = VERSION

    def extract(self, bundle: NarrativeBundle) -> TextExtraction:
        if bundle.is_empty():
            return TextExtraction.unavailable(bundle.customer_id, extractor_version=self.version)

        text = " \n ".join(
            part for part in (bundle.support_call_summary, bundle.underwriter_note, bundle.kyc_document_extract) if part
        )
        lowered = text.lower()

        distress_level, distress_confidence = _level_and_confidence(_hits(lowered, _DISTRESS_SIGNALS))
        concealment_level, concealment_confidence = _level_and_confidence(_hits(lowered, _CONCEALMENT_SIGNALS))

        evasive_hits = sum(1 for phrase in _EVASIVE_ACT_PHRASES if phrase.lower() in lowered)
        evasiveness_detected = evasive_hits > 0
        evasiveness_confidence = round(min(0.95, 0.55 + 0.15 * evasive_hits), 2) if evasive_hits else 0.70

        life_events = []
        for phrase, tag in _LIFE_EVENT_SIGNALS:
            if phrase.lower() in lowered and tag not in life_events:
                life_events.append(tag)
            if len(life_events) >= _MAX_LIFE_EVENTS:
                break

        result = ExtractionResult(
            distress_level=distress_level, distress_confidence=distress_confidence,
            concealment_level=concealment_level, concealment_confidence=concealment_confidence,
            stated_life_events=life_events,
            evasiveness_detected=evasiveness_detected, evasiveness_confidence=evasiveness_confidence,
        )
        return TextExtraction.from_result(bundle.customer_id, "reference", self.version, result)
