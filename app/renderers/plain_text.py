"""Plain-text renderer derived from trusted renderer-owned HTML."""

from __future__ import annotations

import re
from html import unescape

from app.models import DigestReport
from app.renderers.telegram import render_telegram


def render_plain_text(report: DigestReport) -> str:
    return unescape(re.sub(r"</?b>", "", render_telegram(report)))
