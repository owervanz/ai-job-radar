"""Tests for the SeenJobsDB deduplication store."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_job_radar.db import SeenJobsDB


@pytest.fixture()
def db(tmp_path: Path) -> SeenJobsDB:
    return SeenJobsDB(tmp_path / "test.db")


def test_unseen_url_returns_false(db: SeenJobsDB) -> None:
    assert db.is_seen("https://example.com/job/1") is False


def test_mark_seen_persists(db: SeenJobsDB) -> None:
    url = "https://example.com/job/1"
    db.mark_seen(url, "Test Job", "Test Source", score=85, notified=True)
    assert db.is_seen(url) is True


def test_mark_seen_is_idempotent(db: SeenJobsDB) -> None:
    url = "https://example.com/job/1"
    db.mark_seen(url, "Test Job", "Test Source", score=85, notified=True)
    db.mark_seen(url, "Test Job", "Test Source", score=90, notified=False)
    assert db.is_seen(url) is True
    stats = db.stats()
    assert stats["total_seen"] == 1
    assert stats["top_score"] == 90


def test_stats_aggregates_correctly(db: SeenJobsDB) -> None:
    db.mark_seen("u1", "t1", "s", 50, False)
    db.mark_seen("u2", "t2", "s", 80, True)
    db.mark_seen("u3", "t3", "s", 30, False)
    stats = db.stats()
    assert stats["total_seen"] == 3
    assert stats["total_notified"] == 1
    assert stats["top_score"] == 80


def test_reset_clears_all_rows(db: SeenJobsDB) -> None:
    db.mark_seen("u1", "t1", "s", 50, False)
    db.reset()
    assert db.stats()["total_seen"] == 0
    assert db.is_seen("u1") is False
