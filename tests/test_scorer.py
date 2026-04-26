"""Tests for the Scoring data class and multi-backend factory."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ai_job_radar.scorer import (
    MultiBackendScorer,
    Scoring,
    build_scorer,
)
from ai_job_radar.sources import Job

# ---------------------------------------------------------------------------
# Scoring dataclass
# ---------------------------------------------------------------------------


def test_from_dict_with_full_payload() -> None:
    s = Scoring.from_dict(
        {
            "score": 87,
            "verdict": "STRONG_FIT",
            "seniority_match": "MATCH",
            "salary_usd_estimate": "USD 3000-3500/mo",
            "remote": True,
            "english_required": "B2",
            "ai_focus": "GENAI_LLM",
            "top_reasons_fit": ["Junior fit", "LangChain match", "LATAM remote"],
            "red_flags": ["Some travel required"],
        },
        backend="groq",
    )
    assert s.score == 87
    assert s.verdict == "STRONG_FIT"
    assert s.remote is True
    assert s.backend == "groq"
    assert s.top_reasons_fit == ["Junior fit", "LangChain match", "LATAM remote"]


def test_from_dict_with_missing_fields_uses_defaults() -> None:
    s = Scoring.from_dict({"score": 30})
    assert s.score == 30
    assert s.verdict == "NOT_A_FIT"
    assert s.salary_usd_estimate == "Not specified"
    assert s.top_reasons_fit == []
    assert s.red_flags == []
    assert s.backend == "unknown"


def test_from_dict_caps_lists() -> None:
    payload = {
        "score": 50,
        "top_reasons_fit": [f"r{i}" for i in range(10)],
        "red_flags": [f"f{i}" for i in range(10)],
    }
    s = Scoring.from_dict(payload)
    assert len(s.top_reasons_fit) == 5
    assert len(s.red_flags) == 5


def test_from_dict_coerces_score_to_int() -> None:
    s = Scoring.from_dict({"score": "75"})
    assert s.score == 75
    assert isinstance(s.score, int)


# ---------------------------------------------------------------------------
# MultiBackendScorer
# ---------------------------------------------------------------------------

DUMMY_JOB = Job(title="AI Engineer", url="https://example.com", source="Test", description="desc")
DUMMY_SCORING = Scoring.from_dict({"score": 85, "verdict": "STRONG_FIT"}, backend="mock")


def _mock_backend(name: str, result: Scoring | None) -> MagicMock:
    b = MagicMock()
    b.name = name
    b.score.return_value = result
    return b


def test_multi_uses_first_backend_on_success() -> None:
    primary = _mock_backend("groq", DUMMY_SCORING)
    fallback = _mock_backend("gemini", DUMMY_SCORING)
    scorer = MultiBackendScorer([primary, fallback])

    result = scorer.score(DUMMY_JOB, "cv", "prefs")

    assert result is DUMMY_SCORING
    primary.score.assert_called_once()
    fallback.score.assert_not_called()   # fallback never needed


def test_multi_falls_back_when_primary_fails() -> None:
    primary = _mock_backend("groq", None)       # primary fails
    fallback = _mock_backend("gemini", DUMMY_SCORING)
    scorer = MultiBackendScorer([primary, fallback])

    result = scorer.score(DUMMY_JOB, "cv", "prefs")

    assert result is DUMMY_SCORING
    primary.score.assert_called_once()
    fallback.score.assert_called_once()


def test_multi_returns_none_when_all_fail() -> None:
    scorer = MultiBackendScorer([
        _mock_backend("groq", None),
        _mock_backend("gemini", None),
    ])
    assert scorer.score(DUMMY_JOB, "cv", "prefs") is None


def test_multi_requires_at_least_one_backend() -> None:
    with pytest.raises(ValueError):
        MultiBackendScorer([])


# ---------------------------------------------------------------------------
# build_scorer factory
# ---------------------------------------------------------------------------


def test_build_scorer_gemini_only() -> None:
    scorer = build_scorer(gemini_api_key="fake-key")
    assert len(scorer._backends) == 1
    assert scorer._backends[0].name == "gemini"


def test_build_scorer_groq_plus_gemini() -> None:
    scorer = build_scorer(gemini_api_key="fake-gemini", groq_api_key="fake-groq")
    assert len(scorer._backends) == 2
    assert scorer._backends[0].name == "groq"
    assert scorer._backends[1].name == "gemini"
