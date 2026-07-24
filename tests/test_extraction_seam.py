"""Structured-extraction seam tests (issue #142).

Covers the validation-retry contract of `llm.StructuredExtractor` — the single
seam every LLM extractor now goes through — without hitting a real provider, and
asserts the typed schemas `.model_dump()` back to the dict shape the persistence
layer consumes.
"""
import pytest
from pydantic import ValidationError

from llm import StructuredExtractor, get_extractor
from agents.extraction_schemas import (
    ExperienceList, ExperienceItem, SkillList, JobSkillList, JobMetadata,
)


class _FlakyRunnable:
    """A with_structured_output stand-in that fails `fails` times, then returns."""

    def __init__(self, result, fails=0, exc=ValueError("bad model output")):
        self.result = result
        self.fails = fails
        self.exc = exc
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        if self.calls <= self.fails:
            raise self.exc
        return self.result


def test_extractor_returns_validated_model_first_try():
    payload = ExperienceList(experiences=[ExperienceItem(title="ML Engineer")])
    runnable = _FlakyRunnable(payload, fails=0)
    out = StructuredExtractor(runnable, max_attempts=2).invoke("msgs")
    assert out is payload
    assert runnable.calls == 1


def test_extractor_retries_once_then_succeeds():
    """Malformed model output on the first call is retried and recovers."""
    payload = SkillList(skills=[])
    runnable = _FlakyRunnable(payload, fails=1)
    out = StructuredExtractor(runnable, max_attempts=2).invoke("msgs")
    assert out is payload
    assert runnable.calls == 2  # proves the retry path fired


def test_extractor_raises_after_exhausting_attempts():
    """Exhausted retries re-raise so the call site's try/except degrades to []."""
    runnable = _FlakyRunnable(None, fails=99, exc=ValidationError.from_exception_data("x", []))
    extractor = StructuredExtractor(runnable, max_attempts=2)
    with pytest.raises(ValidationError):
        extractor.invoke("msgs")
    assert runnable.calls == 2


def test_extractor_treats_none_result_as_a_miss():
    """with_structured_output returning None (no tool call) is retried, not returned."""
    runnable = _FlakyRunnable(None, fails=0)  # always returns None
    with pytest.raises(Exception):
        StructuredExtractor(runnable, max_attempts=2).invoke("msgs")
    assert runnable.calls == 2


def test_get_extractor_requires_schema():
    with pytest.raises(ValueError):
        get_extractor(schema=None)


def test_get_extractor_wraps_provided_llm_without_network():
    """Passing llm= reuses the instance and never calls the provider factory."""

    class _FakeLLM:
        def with_structured_output(self, schema):
            return _FlakyRunnable(schema())  # empty valid model

    extractor = get_extractor(schema=SkillList, llm=_FakeLLM())
    assert isinstance(extractor, StructuredExtractor)
    assert extractor.invoke("msgs").skills == []


def test_schema_model_dump_matches_persistence_keys():
    """The dict shape the extractors emit still carries the keys _save_* read."""
    exp = ExperienceItem(
        title="Data Scientist", company="Acme", start_date="2023-01",
        end_date="Present", description="d", bullets=["b1", "b2"],
    ).model_dump()
    assert set(exp) == {"title", "company", "start_date", "end_date", "description", "bullets"}
    assert exp["bullets"] == ["b1", "b2"]

    js = JobSkillList(skills=[]).model_dump()
    assert js == {"skills": []}
    meta = JobMetadata().model_dump()
    assert meta == {"title": None, "company": None}
