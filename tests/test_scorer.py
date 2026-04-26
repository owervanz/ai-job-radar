"""Tests for the Scoring data class parsing."""

from __future__ import annotations

from ai_job_radar.scorer import Scoring


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
        }
    )
    assert s.score == 87
    assert s.verdict == "STRONG_FIT"
    assert s.remote is True
    assert s.top_reasons_fit == ["Junior fit", "LangChain match", "LATAM remote"]


def test_from_dict_with_missing_fields_uses_defaults() -> None:
    s = Scoring.from_dict({"score": 30})
    assert s.score == 30
    assert s.verdict == "NOT_A_FIT"
    assert s.salary_usd_estimate == "Not specified"
    assert s.top_reasons_fit == []
    assert s.red_flags == []


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
