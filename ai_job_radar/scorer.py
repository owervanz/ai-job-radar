"""LLM-powered job-fit scoring against a candidate CV."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import google.generativeai as genai

from ai_job_radar.sources import Job

log = logging.getLogger(__name__)

SCORING_PROMPT = """You are a hiring-fit analyst. Compare ONE job posting against a
candidate's CV and stated preferences. Output STRICT JSON only — no markdown, no prose.

CANDIDATE CV
============
{cv}

CANDIDATE PREFERENCES
=====================
{prefs}

JOB POSTING
===========
TITLE: {job_title}
SOURCE: {job_source}

DESCRIPTION:
{job_desc}

Return ONLY this JSON object (no surrounding text):
{{
  "score": <int 0-100, where 100 = perfect fit>,
  "verdict": "<STRONG_FIT | GOOD_FIT | WEAK_FIT | NOT_A_FIT>",
  "seniority_match": "<MATCH | OVERQUALIFIED | UNDERQUALIFIED>",
  "salary_usd_estimate": "<e.g. 'USD 2500-3500/mo' or 'Not specified'>",
  "remote": <true | false | null>,
  "english_required": "<NONE | B2 | C1 | C2 | UNKNOWN>",
  "ai_focus": "<GENAI_LLM | CLASSICAL_ML | MIXED | NOT_AI>",
  "top_reasons_fit": ["<reason 1>", "<reason 2>", "<reason 3>"],
  "red_flags": ["<flag 1>", "<flag 2>"]
}}

Scoring guidance:
- Junior/Mid GenAI/LLM roles aligned with the candidate stack: 75-95
- Mid AI roles requiring some skills the candidate is learning (PyTorch): 60-75
- Senior 5+ yrs ML, recommender systems, or PhD required: 10-30
- Non-AI roles (pure backend/devops): 30-50 unless great match
- On-site only or strict C1 English required: cap at 45
"""


@dataclass(frozen=True)
class Scoring:
    score: int
    verdict: str
    seniority_match: str
    salary_usd_estimate: str
    remote: bool | None
    english_required: str
    ai_focus: str
    top_reasons_fit: list[str]
    red_flags: list[str]

    @classmethod
    def from_dict(cls, data: dict) -> Scoring:
        return cls(
            score=int(data.get("score", 0)),
            verdict=str(data.get("verdict", "NOT_A_FIT")),
            seniority_match=str(data.get("seniority_match", "UNKNOWN")),
            salary_usd_estimate=str(data.get("salary_usd_estimate") or "Not specified"),
            remote=data.get("remote"),
            english_required=str(data.get("english_required", "UNKNOWN")),
            ai_focus=str(data.get("ai_focus", "NOT_AI")),
            top_reasons_fit=[str(r) for r in (data.get("top_reasons_fit") or [])][:5],
            red_flags=[str(r) for r in (data.get("red_flags") or [])][:5],
        )


class GeminiScorer:
    """Wraps Gemini with retries and strict JSON parsing."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-flash-latest",
        max_retries: int = 2,
    ) -> None:
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model)
        self._max_retries = max_retries

    def score(self, job: Job, cv: str, preferences: str) -> Scoring | None:
        prompt = SCORING_PROMPT.format(
            cv=cv.strip(),
            prefs=preferences.strip(),
            job_title=job.title,
            job_source=job.source,
            job_desc=job.description[:5000],
        )

        for attempt in range(1, self._max_retries + 2):
            try:
                resp = self._model.generate_content(
                    prompt,
                    generation_config={
                        "response_mime_type": "application/json",
                        "temperature": 0.2,
                    },
                )
                data = json.loads(resp.text)
                return Scoring.from_dict(data)
            except json.JSONDecodeError as e:
                log.warning(
                    "Gemini returned non-JSON for '%s' (attempt %d): %s",
                    job.title[:60], attempt, e,
                )
            except Exception as e:
                log.warning(
                    "Gemini call failed for '%s' (attempt %d): %s",
                    job.title[:60], attempt, e,
                )
            if attempt <= self._max_retries:
                time.sleep(2 * attempt)

        log.error("Giving up scoring for: %s", job.title[:80])
        return None
