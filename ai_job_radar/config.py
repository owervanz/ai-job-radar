"""Centralized configuration loaded from environment variables."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

load_dotenv(PROJECT_ROOT / ".env")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    groq_api_key: str | None = None          # optional — enables Groq as primary backend
    gemini_model: str = "gemini-2.0-flash"   # 1 500 RPD / 15 RPM on free tier
    groq_model: str = "llama-3.3-70b-versatile"  # best quality; pre-filter cuts token use ~75%
    min_score: int = 65
    max_jobs_per_run: int = 25
    db_path: Path = PROJECT_ROOT / "seen_jobs.db"
    cv_path: Path = DATA_DIR / "cv.md"
    preferences_path: Path = DATA_DIR / "preferences.yml"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Settings:
        missing = [
            name
            for name in ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
            if not os.getenv(name)
        ]
        if missing:
            raise ConfigError(
                f"Missing required env vars: {', '.join(missing)}. "
                "Copy .env.example to .env and fill them in."
            )

        return cls(
            gemini_api_key=os.environ["GEMINI_API_KEY"],
            telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
            telegram_chat_id=os.environ["TELEGRAM_CHAT_ID"],
            groq_api_key=os.getenv("GROQ_API_KEY") or None,
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
            groq_model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            min_score=int(os.getenv("MIN_SCORE", "70")),
            max_jobs_per_run=int(os.getenv("MAX_JOBS_PER_RUN", "15")),
            db_path=Path(os.getenv("DB_PATH", str(PROJECT_ROOT / "seen_jobs.db"))),
            cv_path=Path(os.getenv("CV_PATH", str(DATA_DIR / "cv.md"))),
            preferences_path=Path(
                os.getenv("PREFERENCES_PATH", str(DATA_DIR / "preferences.yml"))
            ),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )


def configure_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Set up root logger with consistent formatting and optional file output."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
