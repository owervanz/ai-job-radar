"""End-to-end orchestrator: fetch -> pre-filter -> score -> notify."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from ai_job_radar.config import Settings
from ai_job_radar.db import SeenJobsDB
from ai_job_radar.notifier import TelegramNotifier
from ai_job_radar.scorer import build_scorer
from ai_job_radar.sources import Job, fetch_all

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pre-filter: instant title-based rejection (no API call needed)
# Eliminates ~70-80% of irrelevant postings before touching the LLM.
# ---------------------------------------------------------------------------

_REJECT_TITLE_RE = re.compile(
    r"\b("
    # Non-tech / business roles
    r"sales\s+(executive|manager|representative|rep|director|lead)"
    r"|business\s+development\s+(manager|representative|rep|director)"
    r"|account\s+(manager|executive|director)"
    r"|marketing\s+(manager|specialist|coordinator|director|lead)"
    r"|paid\s+media\s+(manager|specialist)"
    r"|seo\s+(specialist|manager)"
    r"|customer\s+(support|service|success)\s*(manager|representative|specialist|agent|lead)?"
    r"|content\s+writer|article\s+writer|copywriter|technical\s+writer"
    r"|human\s+resources|hr\s+(manager|generalist|specialist)"
    r"|recruiter|talent\s+acquisition"
    r"|payroll\s+(manager|specialist|lead|analyst)"
    r"|bookkeeper|accountant|financial\s+(analyst|controller|advisor|planner)"
    r"|legal\s+(counsel|advisor|operations)|attorney|lawyer|paralegal"
    r"|compliance\s+(officer|manager|analyst)"
    r"|office\s+manager|administrative\s+(assistant|coordinator)"
    r"|medical\s+coder|behavioral\s+health|clinical\s+(coder|analyst)"
    r"|supplier\s+engineer|supply\s+chain\s+(manager|analyst)"
    # Creative / non-dev roles
    r"|compositor|2d\s+(artist|animator|designer)|3d\s+(artist|animator|modeler)"
    r"|graphic\s+(designer|artist)"
    r"|video\s+(editor|producer)"
    r"|cad\s+(engineer|designer)|mechanical\s+engineer"
    # Wrong seniority (non-AI executive titles)
    r"|vice\s+president\s+of"
    r"|vp\s+(of\s+)?(engineering|product|sales|marketing|operations|finance)"
    r"|chief\s+(marketing|revenue|operating|financial|human\s+resources)\s+officer"
    r"|head\s+of\s+(sales|marketing|hr|finance|legal|operations)"
    r")\b",
    re.IGNORECASE,
)


def _is_obviously_irrelevant(job: Job) -> bool:
    """Return True if the job title is clearly not relevant for the candidate.

    Checked BEFORE any LLM call — zero token cost.
    Only rejects jobs that are unambiguously wrong (non-tech, non-AI, wrong
    seniority level). Borderline cases are left to the LLM scorer.
    """
    return bool(_REJECT_TITLE_RE.search(job.title))


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class RunReport:
    fetched: int = 0
    new: int = 0
    prefiltered: int = 0
    scored: int = 0
    notified: int = 0
    skipped_cap: int = 0
    failed_scoring: int = 0


def _read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(
            f"Required file not found: {path}. Did you create data/cv.md and data/preferences.yml?"
        )
    return path.read_text(encoding="utf-8")


def run_once(settings: Settings, dry_run: bool = False) -> RunReport:
    """Execute one full pipeline run and return a summary report."""
    cv = _read_text(settings.cv_path)
    preferences = _read_text(settings.preferences_path)

    db = SeenJobsDB(settings.db_path)
    scorer = build_scorer(
        gemini_api_key=settings.gemini_api_key,
        gemini_model=settings.gemini_model,
        groq_api_key=settings.groq_api_key,
        groq_model=settings.groq_model,
        cerebras_api_key=settings.cerebras_api_key,
    )
    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)

    report = RunReport()
    all_jobs = fetch_all()
    report.fetched = len(all_jobs)

    new_jobs: list[Job] = [j for j in all_jobs if not db.is_seen(j.url)]
    report.new = len(new_jobs)
    log.info("New jobs (not in DB): %d", report.new)

    # ── Phase 1: fast pre-filter (free, no API call) ──────────────────────
    relevant_jobs: list[Job] = []
    for job in new_jobs:
        if _is_obviously_irrelevant(job):
            log.debug("  [pre-filter] skip: %s", job.title[:80])
            db.mark_seen(job.url, job.title, job.source, score=0, notified=False)
            report.prefiltered += 1
        else:
            relevant_jobs.append(job)

    if report.prefiltered:
        log.info("Pre-filtered (obviously irrelevant): %d", report.prefiltered)

    # ── Phase 2: LLM scoring (expensive) ─────────────────────────────────
    to_score = relevant_jobs[: settings.max_jobs_per_run]
    report.skipped_cap = max(0, len(relevant_jobs) - settings.max_jobs_per_run)
    if report.skipped_cap:
        log.info("Capping at %d; %d relevant jobs will wait for next run",
                 settings.max_jobs_per_run, report.skipped_cap)

    for job in to_score:
        log.info("Scoring: %s", job.title[:80])
        scoring = scorer.score(job, cv, preferences)

        if scoring is None:
            report.failed_scoring += 1
            db.mark_seen(job.url, job.title, job.source, score=0, notified=False)
            continue

        report.scored += 1
        log.info("  -> score=%d verdict=%s ai=%s remote=%s backend=%s",
                 scoring.score, scoring.verdict, scoring.ai_focus,
                 scoring.remote, scoring.backend)

        sent = False
        if scoring.score >= settings.min_score and not dry_run:
            sent = notifier.send_match(job, scoring)
            if sent:
                report.notified += 1
                time.sleep(1)  # avoid Telegram rate-limit
        elif scoring.score >= settings.min_score and dry_run:
            log.info("[dry-run] would notify: %s (score=%d)", job.title[:60], scoring.score)

        db.mark_seen(job.url, job.title, job.source, scoring.score, sent)
        time.sleep(6)  # ~10 req/min — safe for both Groq 70B (6K TPM) and Gemini (15 RPM)

    # Do NOT mark overflow jobs as seen — they'll be retried next run.

    log.info(
        "Run summary: fetched=%d new=%d prefiltered=%d scored=%d "
        "notified=%d failed=%d skipped_cap=%d",
        report.fetched, report.new, report.prefiltered, report.scored,
        report.notified, report.failed_scoring, report.skipped_cap,
    )
    return report
