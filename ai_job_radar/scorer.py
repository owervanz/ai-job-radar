"""LLM-powered job-fit scoring against a candidate CV.

Supports two backends with automatic fallback:
  1. Groq  (primary)   — LLaMA 3.3 70B, ~14 400 req/day free
  2. Gemini (fallback) — gemini-2.0-flash, 1 500 req/day free

If GROQ_API_KEY is set, Groq is tried first; any failure falls through
to Gemini. If only one key is present, that backend is used exclusively.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Protocol

import google.generativeai as genai
from groq import Groq

from ai_job_radar.sources import Job

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

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
  "red_flags": ["<flag 1>", "<flag 2>"],
  "why_interested_draft": "<see instructions below>"
}}

Scoring guidance:
- Junior/Mid GenAI/LLM roles aligned with candidate stack (Python, RAG, LangChain, APIs): 75-95
- Mid AI roles requiring skills candidate is still learning (PyTorch, deep ML): 60-75
- Python/Node backend or API-integration role at an AI/LLM product company: 60-72
- Senior 5+ yrs ML engineering, recommender systems, or PhD required: 10-30
- Non-AI roles at non-AI companies (pure backend/devops, no AI product): 30-50
- On-site only or strict C1+ English hard requirement: cap at 45
- Role in Americas/LATAM timezone or worldwide remote: bonus +5 if borderline

why_interested_draft instructions:
- Write a 3-4 sentence paragraph in English, first person, human and natural tone.
- No em-dashes (—), no bullet lists, no filler phrases like "I am excited to".
- Mention ONE specific thing from THIS job posting (tech stack, product, mission, or team detail).
- Connect it to a real project or skill from the candidate CV (YouTube pipeline, RAG KB, CCNP infra, GenAI APIs, PUC Diploma).
- Aim for 400-600 characters. Ready to paste into a job application field.
- If score < 50, set this field to an empty string "".
"""

# ---------------------------------------------------------------------------
# Scoring result
# ---------------------------------------------------------------------------


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
    backend: str = "unknown"          # which backend produced this result
    why_interested_draft: str = ""    # auto-generated application paragraph

    @classmethod
    def from_dict(cls, data: dict, backend: str = "unknown") -> Scoring:
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
            backend=backend,
            why_interested_draft=str(data.get("why_interested_draft") or "")[:700],
        )


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------


class ScorerBackend(Protocol):
    """Anything that can score a single job."""

    name: str

    def score(self, job: Job, cv: str, preferences: str) -> Scoring | None:
        ...


# ---------------------------------------------------------------------------
# Groq backend  (primary — LLaMA 3.3 70B, ~14 400 req/day free)
# ---------------------------------------------------------------------------


class GroqScorer:
    """Groq-hosted LLaMA 3.3 70B. Fastest + most generous free tier."""

    name = "groq"

    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
        max_retries: int = 2,
    ) -> None:
        self._client = Groq(api_key=api_key)
        self._model = model
        self._max_retries = max_retries

    def score(self, job: Job, cv: str, preferences: str) -> Scoring | None:
        prompt = _build_prompt(job, cv, preferences)

        for attempt in range(1, self._max_retries + 2):
            try:
                resp = self._client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.2,
                )
                data = json.loads(resp.choices[0].message.content)
                return Scoring.from_dict(data, backend="groq")
            except json.JSONDecodeError as e:
                log.warning("Groq non-JSON for '%s' (attempt %d): %s", job.title[:60], attempt, e)
            except Exception as e:
                log.warning("Groq error for '%s' (attempt %d): %s", job.title[:60], attempt, e)
            if attempt <= self._max_retries:
                time.sleep(2 * attempt)

        log.error("Groq gave up on: %s", job.title[:80])
        return None


# ---------------------------------------------------------------------------
# Gemini backend  (fallback — gemini-2.0-flash, 1 500 req/day free)
# ---------------------------------------------------------------------------


class GeminiScorer:
    """Google Gemini 2.0 Flash. Fallback when Groq fails or key not set."""

    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash",
        max_retries: int = 2,
    ) -> None:
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model)
        self._max_retries = max_retries

    def score(self, job: Job, cv: str, preferences: str) -> Scoring | None:
        prompt = _build_prompt(job, cv, preferences)

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
                return Scoring.from_dict(data, backend="gemini")
            except json.JSONDecodeError as e:
                log.warning("Gemini non-JSON for '%s' (attempt %d): %s", job.title[:60], attempt, e)
            except Exception as e:
                log.warning("Gemini error for '%s' (attempt %d): %s", job.title[:60], attempt, e)
            if attempt <= self._max_retries:
                time.sleep(2 * attempt)

        log.error("Gemini gave up on: %s", job.title[:80])
        return None


# ---------------------------------------------------------------------------
# Multi-backend scorer  (tries Groq first, falls back to Gemini)
# ---------------------------------------------------------------------------


class MultiBackendScorer:
    """Tries backends in order; falls back automatically on failure.

    Default order: Groq → Gemini.
    Each backend gets its own retry budget before the next is tried.
    """

    def __init__(self, backends: list[ScorerBackend]) -> None:
        if not backends:
            raise ValueError("At least one backend is required.")
        self._backends = backends
        names = " → ".join(b.name for b in backends)
        log.info("MultiBackendScorer initialized: %s", names)

    def score(self, job: Job, cv: str, preferences: str) -> Scoring | None:
        for backend in self._backends:
            result = backend.score(job, cv, preferences)
            if result is not None:
                if backend.name != self._backends[0].name:
                    log.info("  (used fallback backend: %s)", backend.name)
                return result
            log.warning("Backend '%s' failed — trying next.", backend.name)
        log.error("All backends exhausted for: %s", job.title[:80])
        return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_scorer(
    gemini_api_key: str,
    gemini_model: str = "gemini-2.0-flash",
    groq_api_key: str | None = None,
    groq_model: str = "llama-3.1-8b-instant",
) -> MultiBackendScorer:
    """Build a MultiBackendScorer from available API keys.

    If GROQ_API_KEY is provided, Groq is added as primary backend.
    Gemini is always added (required) as the final fallback.
    """
    backends: list[ScorerBackend] = []

    if groq_api_key:
        backends.append(GroqScorer(api_key=groq_api_key, model=groq_model))
        log.info("Groq backend enabled (model: %s)", groq_model)

    backends.append(GeminiScorer(api_key=gemini_api_key, model=gemini_model))
    log.info("Gemini backend enabled (model: %s)", gemini_model)

    return MultiBackendScorer(backends)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_prompt(job: Job, cv: str, preferences: str) -> str:
    return SCORING_PROMPT.format(
        cv=cv.strip(),
        prefs=preferences.strip(),
        job_title=job.title,
        job_source=job.source,
        job_desc=job.description[:2500],
    )
