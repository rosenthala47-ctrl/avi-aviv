"""LLM branch: bounded signal extraction from unstructured narrative text."""

from crr.llm.anthropic_extractor import DEFAULT_MODEL, AnthropicExtractor
from crr.llm.batch import extract_all
from crr.llm.extraction import (
    ExtractionResult,
    ExtractionSource,
    Extractor,
    NarrativeBundle,
    TextExtraction,
)
from crr.llm.prompts import SYSTEM_PROMPT, build_extraction_prompt
from crr.llm.reference_extractor import ReferenceExtractor

# crr.llm.cache (CachingExtractor, content_hash) is deliberately not
# re-exported here: it imports crr.api.repository for the ExtractionRepository
# Protocol, and crr.api imports back from crr.llm.cache for wiring — a real
# cycle that only stays latent because every call site already imports it
# submodule-direct (`from crr.llm.cache import CachingExtractor`), which never
# triggers this file. Re-exporting it here would make that the first import
# path that does. Keep using the submodule import for those two names.

__all__ = [
    "DEFAULT_MODEL",
    "SYSTEM_PROMPT",
    "AnthropicExtractor",
    "ExtractionResult",
    "ExtractionSource",
    "Extractor",
    "NarrativeBundle",
    "ReferenceExtractor",
    "TextExtraction",
    "build_extraction_prompt",
    "extract_all",
]
