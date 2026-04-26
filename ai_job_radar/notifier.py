"""Telegram notification client."""

from __future__ import annotations

import logging

import requests

from ai_job_radar.scorer import Scoring
from ai_job_radar.sources import Job

log = logging.getLogger(__name__)

VERDICT_EMOJI = {
    "STRONG_FIT": "🔥",
    "GOOD_FIT": "✅",
    "WEAK_FIT": "🟡",
    "NOT_A_FIT": "❌",
}

API_TIMEOUT = 15


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self._token = token
        self._chat_id = chat_id
        self._endpoint = f"https://api.telegram.org/bot{token}"

    def send_match(self, job: Job, scoring: Scoring) -> bool:
        text = self._format_match(job, scoring)
        return self._send(text)

    def send_text(self, text: str) -> bool:
        return self._send(text)

    def _send(self, text: str) -> bool:
        try:
            r = requests.post(
                f"{self._endpoint}/sendMessage",
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
                timeout=API_TIMEOUT,
            )
            if r.status_code != 200:
                log.error("Telegram error %s: %s", r.status_code, r.text[:300])
                return False
            return True
        except requests.RequestException as e:
            log.error("Telegram request failed: %s", e)
            return False

    @staticmethod
    def _format_match(job: Job, s: Scoring) -> str:
        emoji = VERDICT_EMOJI.get(s.verdict, "❔")
        reasons = "\n".join(f"• {_esc(r)}" for r in s.top_reasons_fit[:3]) or "—"
        flags = "\n".join(f"• {_esc(f)}" for f in s.red_flags[:3]) or "—"

        return (
            f"{emoji} <b>[{s.score}/100]</b> {_esc(job.title)}\n"
            f"<i>{_esc(job.source)}</i>\n\n"
            f"💰 {_esc(s.salary_usd_estimate)}\n"
            f"🎯 Seniority: {_esc(s.seniority_match)}\n"
            f"🧠 AI focus: {_esc(s.ai_focus)}\n"
            f"🇬🇧 English: {_esc(s.english_required)}\n\n"
            f"<b>Why it fits:</b>\n{reasons}\n\n"
            f"<b>Red flags:</b>\n{flags}\n\n"
            f"🔗 {_esc(job.url)}"
        )


def _esc(s: object) -> str:
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
