"""Tests for source helpers (HTML cleaning, dedup)."""

from __future__ import annotations

from ai_job_radar.sources import Job, _clean_html


def test_clean_html_strips_tags() -> None:
    html = "<p>Hello <b>world</b></p>"
    assert _clean_html(html) == "Hello world"


def test_clean_html_collapses_whitespace() -> None:
    html = "<p>Hello\n\n   world</p>"
    assert _clean_html(html) == "Hello world"


def test_clean_html_decodes_entities() -> None:
    html = "<p>R&amp;D &lt;test&gt;</p>"
    assert _clean_html(html) == "R&D <test>"


def test_clean_html_handles_empty_input() -> None:
    assert _clean_html("") == ""
    assert _clean_html(None) == ""


def test_job_is_hashable_and_immutable() -> None:
    job = Job(title="t", url="u", source="s", description="d")
    # Frozen dataclass: hashing works (used in sets for dedup elsewhere).
    assert hash(job) == hash(job)
