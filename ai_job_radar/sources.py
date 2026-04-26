"""Job sources: RSS feeds and JSON APIs."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from html import unescape

import feedparser
import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; AI-Job-Radar/0.2; +personal-use)"
HTTP_TIMEOUT = 20


@dataclass(frozen=True)
class Job:
    title: str
    url: str
    source: str
    description: str
    published: str = ""


@dataclass(frozen=True)
class RSSSource:
    name: str
    url: str

    def fetch(self, limit: int = 30) -> list[Job]:
        log.info("Fetching RSS: %s", self.name)
        try:
            feed = feedparser.parse(self.url)
        except Exception as e:
            log.error("RSS parse failed for %s: %s", self.name, e)
            return []

        jobs: list[Job] = []
        for entry in feed.entries[:limit]:
            url = entry.get("link", "").strip()
            if not url:
                continue
            jobs.append(
                Job(
                    title=entry.get("title", "Untitled").strip(),
                    url=url,
                    source=self.name,
                    description=_clean_html(
                        entry.get("summary", "") or entry.get("description", "")
                    )[:6000],
                    published=entry.get("published", ""),
                )
            )
        log.info("  %s -> %d entries", self.name, len(jobs))
        return jobs


@dataclass(frozen=True)
class RemoteOKSource:
    name: str = "RemoteOK"
    url: str = "https://remoteok.com/api"
    relevant_tags: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {"ai", "ml", "machine learning", "python", "llm", "genai", "langchain"}
        )
    )

    def fetch(self, limit: int = 50) -> list[Job]:
        log.info("Fetching API: %s", self.name)
        try:
            r = requests.get(
                self.url,
                headers={"User-Agent": USER_AGENT},
                timeout=HTTP_TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.error("RemoteOK fetch failed: %s", e)
            return []

        # First element is API metadata, skip it.
        items = data[1:] if data and isinstance(data[0], dict) and "legal" in data[0] else data

        jobs: list[Job] = []
        for item in items[:limit]:
            tags = {str(t).lower() for t in item.get("tags", [])}
            position = (item.get("position") or "").lower()
            if not (tags & self.relevant_tags or any(k in position for k in self.relevant_tags)):
                continue

            url = item.get("url") or item.get("apply_url") or ""
            if not url:
                continue

            company = item.get("company", "").strip()
            title = f"{item.get('position', 'Untitled').strip()}{' @ ' + company if company else ''}"

            jobs.append(
                Job(
                    title=title,
                    url=url,
                    source=self.name,
                    description=_clean_html(item.get("description", ""))[:6000],
                    published=str(item.get("date", "")),
                )
            )
        log.info("  %s -> %d entries", self.name, len(jobs))
        return jobs


DEFAULT_SOURCES: list[RSSSource | RemoteOKSource] = [
    RSSSource(
        "Getonbrd · Machine Learning & AI",
        "https://www.getonbrd.com/jobs/category/machine-learning-ai.rss",
    ),
    RSSSource(
        "Getonbrd · Programming",
        "https://www.getonbrd.com/jobs/category/programming.rss",
    ),
    RSSSource(
        "Getonbrd · DevOps & SysAdmin",
        "https://www.getonbrd.com/jobs/category/devops-sysadmin.rss",
    ),
    RSSSource(
        "WeWorkRemotely · Programming",
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    ),
    RSSSource(
        "WeWorkRemotely · Full-Stack",
        "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    ),
    RemoteOKSource(),
]


def fetch_all(sources: Iterable[RSSSource | RemoteOKSource] | None = None) -> list[Job]:
    """Fetch jobs from every configured source, deduplicated by URL."""
    sources = list(sources) if sources is not None else DEFAULT_SOURCES
    seen: set[str] = set()
    out: list[Job] = []
    for src in sources:
        for job in src.fetch():
            normalized = job.url.split("?")[0].rstrip("/")
            if normalized in seen:
                continue
            seen.add(normalized)
            out.append(job)
    log.info("Total unique jobs across sources: %d", len(out))
    return out


_WS_RE = re.compile(r"\s+")


def _clean_html(raw: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    if not raw:
        return ""
    text = BeautifulSoup(raw, "lxml").get_text(separator=" ", strip=True)
    return unescape(_WS_RE.sub(" ", text)).strip()
