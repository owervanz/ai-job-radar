"""Command-line entry point for AI Job Radar."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ai_job_radar import __version__
from ai_job_radar.config import PROJECT_ROOT, ConfigError, Settings, configure_logging
from ai_job_radar.db import SeenJobsDB
from ai_job_radar.notifier import TelegramNotifier
from ai_job_radar.pipeline import run_once

log = logging.getLogger("ai_job_radar")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ai-job-radar",
        description="Personal job-matching radar: scrape, score with LLM, notify via Telegram.",
    )
    p.add_argument("--version", action="version", version=f"ai-job-radar {__version__}")
    p.add_argument("--dry-run", action="store_true",
                   help="Score jobs but do not send Telegram notifications.")
    p.add_argument("--reset-db", action="store_true",
                   help="Wipe the seen-jobs database before running.")
    p.add_argument("--ping", action="store_true",
                   help="Send a single test message to Telegram and exit.")
    p.add_argument("--stats", action="store_true",
                   help="Print database statistics and exit.")
    p.add_argument("--log-file", type=Path, default=PROJECT_ROOT / "bot.log",
                   help="Path to log file (default: bot.log).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        settings = Settings.from_env()
    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    configure_logging(settings.log_level, args.log_file)

    if args.ping:
        notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        ok = notifier.send_text("🤖 AI Job Radar ping ✅")
        print("Telegram ping:", "OK" if ok else "FAILED")
        return 0 if ok else 1

    if args.stats:
        db = SeenJobsDB(settings.db_path)
        for k, v in db.stats().items():
            print(f"{k}: {v}")
        return 0

    if args.reset_db:
        SeenJobsDB(settings.db_path).reset()
        log.info("Seen-jobs database reset.")

    try:
        report = run_once(settings, dry_run=args.dry_run)
    except FileNotFoundError as e:
        log.error(str(e))
        return 2
    except Exception:
        log.exception("Pipeline crashed")
        return 1

    print(
        f"Done. fetched={report.fetched} new={report.new} "
        f"scored={report.scored} notified={report.notified}"
    )
    return 0
