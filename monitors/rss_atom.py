"""RSS/Atom source type: fetch a feed and reduce it to a stable,
whitespace-normalized string of entry titles (+ optional regex scoping),
so a new/changed entry hashes differently but ad-tracking params or
timestamp jitter in the feed XML don't cause false positives.
"""
from __future__ import annotations

import re
from typing import Optional

import feedparser

from .common import DEFAULT_MIN_CONTENT_LENGTH, FetchError, fetch, normalize_whitespace


def extract_content(
    raw_text: str,
    regex: Optional[str] = None,
    min_content_length: int = DEFAULT_MIN_CONTENT_LENGTH,
) -> str:
    parsed = feedparser.parse(raw_text)

    if parsed.bozo and not parsed.entries:
        raise FetchError(f"feed failed to parse: {parsed.bozo_exception}")

    if not parsed.entries:
        raise FetchError("feed parsed but contained zero entries")

    lines = []
    for entry in parsed.entries:
        title = entry.get("title", "")
        link = entry.get("link", "")
        lines.append(f"{title} | {link}")
    text = "\n".join(lines)

    if regex:
        found = re.findall(regex, text, flags=re.IGNORECASE | re.DOTALL)
        if not found:
            raise FetchError(f"regex {regex!r} matched nothing in feed entries")
        text = " ".join(f if isinstance(f, str) else " ".join(f) for f in found)

    text = normalize_whitespace(text)

    if len(text) < min_content_length:
        raise FetchError(
            f"scoped feed content only {len(text)} chars (< min {min_content_length})"
        )

    return text


def check(url: str, regex: Optional[str] = None,
          min_content_length: int = DEFAULT_MIN_CONTENT_LENGTH) -> str:
    result = fetch(url)
    return extract_content(result.raw_text, regex=regex, min_content_length=min_content_length)
