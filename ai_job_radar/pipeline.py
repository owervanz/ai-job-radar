"""End-to-end orchestrator: fetch -> dedupe -> score -> notify."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from ai_job_radar.config import Settings
from ai_job_radar.db import SeenJobsDB
from ai_job_radar.notifier import TelegramNotifier
from ai_job_radar.scorer import GeminiScorer
from ai_job_radar.sources import Job, fetch_all

log = logging.getLogger(__name__)


@dataclass
class RunReport:
    fetched: int = 0
    new: int = 0
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
    scorer = GeminiScorer(api_key=settings.gemini_api_key, model=settings.gemini_model)
    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)

    report = RunReport()
    all_jobs = fetch_all()
    report.fetched = len(all_jobs)

    new_jobs: list[Job] = [j for j in all_jobs if not db.is_seen(j.url)]
    report.new = len(new_jobs)
    log.info("New jobs (not in DB): %d", report.new)

    to_score = new_jobs[: settings.max_jobs_per_run]
    report.skipped_cap = max(0, len(new_jobs) - settings.max_jobs_per_run)
    if report.skipped_cap:
        log.info("Capping at %d; %d will wait for next run",
                 settings.max_jobs_per_run, report.skipped_cap)

    for job in to_score:
        log.info("Scoring: %s", job.title[:80])
        scoring = scorer.score(job, cv, preferences)

        if scoring is None:
            report.failed_scoring += 1
            db.mark_seen(job.url, job.title, job.source, score=0, notified=False)
            continue

        report.scored += 1
        log.info("  -> score=%d verdict=%s ai=%s",
                 scoring.score, scoring.verdict, scoring.ai_focus)

        sent = False
        if scoring.score >= settings.min_score and not dry_run:
            sent = notifier.send_match(job, scoring)
            if sent:
                report.notified += 1
                time.sleep(1)  # avoid Telegram rate-limit
        elif scoring.score >= settings.min_score and dry_run:
            log.info("[dry-run] would notify: %s (score=%d)", job.title[:60], scoring.score)

        db.mark_seen(job.url, job.title, job.source, scoring.score, sent)
        time.sleep(4)  # ~15 RPM safety margin for Gemini free tier

    # Mark the rest as seen so we don't re-process them next run.
    for job in new_jobs[settings.max_jobs_per_run:]:
        db.mark_seen(job.url, job.title, job.source, score=0, notified=False)

    log.info(
        "Run summary: fetched=%d new=%d scored=%d notified=%d failed=%d skipped_cap=%d",
        report.fetched, report.new, report.scored,
        report.notified, report.failed_scoring, report.skipped_cap,
    )
    return report
