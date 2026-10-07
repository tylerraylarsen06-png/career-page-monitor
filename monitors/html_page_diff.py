"""HTML page source type: fetch a page, scope it down to 'the content' via
a CSS selector and/or regex, strip scripts/styles, normalize whitespace,
and hash it.
"""
from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from .common import DEFAULT_MIN_CONTENT_LENGTH, FetchError, fetch, normalize_whitespace


def extract_content(
    raw_html: str,
    selector: Optional[str] = None,
    regex: Optional[str] = None,
    min_content_length: int = DEFAULT_MIN_CONTENT_LENGTH,
) -> str:
    soup = BeautifulSoup(raw_html, "lxml")

    # Strip scripts/styles/etc before doing anything else -- otherwise
    # ad/analytics JS churn or CSS build hashes cause false-positive diffs
    # on every run even though nothing a human would call "content" changed.
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()

    if selector:
        matches = soup.select(selector)
        if not matches:
            raise FetchError(f"CSS selector {selector!r} matched nothing on page")
        text = "\n".join(m.get_text(separator=" ", strip=True) for m in matches)
    else:
        text = soup.get_text(separator=" ", strip=True)

    if regex:
        found = re.findall(regex, text, flags=re.IGNORECASE | re.DOTALL)
        if not found:
            raise FetchError(f"regex {regex!r} matched nothing in scoped content")
        text = " ".join(f if isinstance(f, str) else " ".join(f) for f in found)

    text = normalize_whitespace(text)

    if len(text) < min_content_length:
        raise FetchError(
            f"scoped content only {len(text)} chars (< min {min_content_length}); "
            "treating as soft-404 / bot-block page rather than real content"
        )

    return text


def check(url: str, selector: Optional[str] = None, regex: Optional[str] = None,
          min_content_length: int = DEFAULT_MIN_CONTENT_LENGTH) -> str:
    """Fetch + extract. Returns normalized text or raises FetchError."""
    result = fetch(url)
    return extract_content(
        result.raw_text, selector=selector, regex=regex,
        min_content_length=min_content_length,
    )
