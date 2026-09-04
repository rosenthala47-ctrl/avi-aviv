"""The real extractor: Claude reads the notes, ``extract_signals`` forces its
answer through :class:`~crr.llm.extraction.ExtractionResult`'s schema.

Two things make this safe to hand untrusted narrative text (see
``crr.llm.prompts`` for the fuller argument): the tool's ``input_schema`` is
generated directly from ``ExtractionResult.model_json_schema()`` — the same
model every result is re-validated against on the way back out, so there is
one schema, not two that could quietly drift apart — and ``tool_choice``
forces the model to answer *through* that schema rather than free text,
so there is no code path from "the model said something odd" to an
unvalidated value reaching a feature column.

Never raises. Anything that goes wrong — no API key, a network error, a
timeout, a response that fails validation, a missing tool call — degrades to
:meth:`TextExtraction.unavailable`, matching the "fall back to the
tabular-only score, mark it degraded" requirement. That is deliberately a
broad exception boundary: this call sits on the request path, and a
customer's score must not fail because the extractor had a bad day.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError

from crr.llm.extraction import ExtractionResult, NarrativeBundle, TextExtraction
from crr.llm.prompts import build_extraction_prompt

if TYPE_CHECKING:
    import anthropic
    from anthropic.types import MessageParam, ToolChoiceToolParam, ToolParam, ToolUseBlock

logger = logging.getLogger("crr.llm")

#: Fast, cheap model: extraction is a bounded classification task run at
#: customer volume, not open-ended generation — the "notes change rarely and
#: LLM calls dominate cost" requirement argues directly for the cheapest
#: model that holds accuracy, not the largest available.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_TOKENS = 1024

_TOOL_NAME = "extract_signals"


def _tool_schema() -> ToolParam:
    schema = ExtractionResult.model_json_schema()
    return {
        "name": _TOOL_NAME,
        "description": "Report the four extracted signals for this customer's notes.",
        "input_schema": schema,
    }


class AnthropicExtractor:
    """Calls the Messages API with a forced tool call. Requires the
    ``anthropic`` package (``pip install "crr[llm]"``) and an API key —
    imported lazily so the rest of the project has no hard dependency on
    either."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.version = f"anthropic:{model}"
        self._client: anthropic.Anthropic | None = None
        self._init_error: str | None = None
        try:
            import anthropic
        except ImportError:
            self._init_error = "the 'anthropic' package is not installed"
            return
        if not api_key:
            self._init_error = "no API key configured"
            return
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds)

    @property
    def available(self) -> bool:
        return self._client is not None

    def extract(self, bundle: NarrativeBundle) -> TextExtraction:
        client = self._client
        if client is None:
            logger.warning("extraction.unavailable customer_id=%s reason=%s", bundle.customer_id, self._init_error)
            return TextExtraction.unavailable(bundle.customer_id, extractor_version=self.version)
        if bundle.is_empty():
            return TextExtraction.unavailable(bundle.customer_id, extractor_version=self.version)

        prompt = build_extraction_prompt(bundle)
        messages: list[MessageParam] = [{"role": "user", "content": prompt.user}]
        tool_choice: ToolChoiceToolParam = {"type": "tool", "name": _TOOL_NAME}
        try:
            message = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=prompt.system,
                messages=messages,
                tools=[_tool_schema()],
                tool_choice=tool_choice,
            )
        except Exception:  # noqa: BLE001 — any transport/API failure degrades this one customer, not the request
            logger.exception("extraction.api_error customer_id=%s", bundle.customer_id)
            return TextExtraction.unavailable(bundle.customer_id, extractor_version=self.version)

        # Duck-typed on purpose: real tests exercise this against lightweight
        # fakes (see tests/test_extraction_security.py), not the SDK's own
        # block classes, and a string check tolerates future block types the
        # installed SDK version doesn't have a class for yet. The cast below
        # tells mypy what the filter already guarantees at runtime.
        tool_call = next((block for block in message.content if getattr(block, "type", None) == "tool_use"), None)
        if tool_call is None:
            logger.warning("extraction.no_tool_call customer_id=%s stop_reason=%s", bundle.customer_id, message.stop_reason)
            return TextExtraction.unavailable(bundle.customer_id, extractor_version=self.version)

        try:
            result = ExtractionResult.model_validate(cast("ToolUseBlock", tool_call).input)
        except ValidationError:
            logger.exception("extraction.invalid_response customer_id=%s", bundle.customer_id)
            return TextExtraction.unavailable(bundle.customer_id, extractor_version=self.version)

        return TextExtraction.from_result(bundle.customer_id, "llm", self.version, result)
