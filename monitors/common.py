"""Shared helpers used by every source-type module: fetching, normalization,
hashing, diffing, and state I/O. Keeping this logic in one place is what
lets each source module (html_page_diff, rss_atom, json_api) stay tiny.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = REPO_ROOT / "state"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 20
DEFAULT_MIN_CONTENT_LENGTH = 20
SNIPPET_LENGTH = 400

# Full normalized page text (capped) is what gets committed to state/*.json
# now -- not just a 400-char snippet -- so a real diff can be computed
# against whatever part of the page actually changed, not just its first
# 400 characters. 20k chars comfortably covers a selector-scoped page
# (these watches use `selector`/`json_path` to scope down to "the content"
# already) while still keeping state files small enough for git.
MAX_STORED_TEXT_LENGTH = 20_000


class FetchError(Exception):
    """Raised for any condition that means 'we don't trust this response
    enough to treat it as real content' -- network errors, non-2xx status,
    a selector that matched nothing, or content that's suspiciously short
    (soft-404 / bot-block interstitial page)."""


@dataclass
class FetchResult:
    raw_text: str
    status_code: int


@dataclass
class WatchOutcome:
    """Result of evaluating one watch for one run."""
    slug: str
    name: str
    url: str
    status: str  # "changed" | "unchanged" | "baseline" | "failed"
    old_text: Optional[str] = None  # full (capped) normalized text, previous run
    new_text: Optional[str] = None  # full (capped) normalized text, this run
    goal: Optional[str] = None      # optional per-watch judging hint from config
    error: Optional[str] = None


def fetch(url: str, timeout: int = DEFAULT_TIMEOUT) -> FetchResult:
    """GET a URL with browser-like headers. Raises FetchError on any
    network problem or non-2xx status -- callers should never see a
    requests exception directly."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml,"
                  "application/rss+xml,application/atom+json,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise FetchError(f"network error fetching {url}: {exc}") from exc

    if resp.status_code >= 400:
        raise FetchError(f"HTTP {resp.status_code} fetching {url}")

    return FetchResult(raw_text=resp.text, status_code=resp.status_code)


def normalize_whitespace(text: str) -> str:
    """Collapse all whitespace runs to single spaces and strip. This is
    what keeps a page's hash stable across runs when only insignificant
    formatting/whitespace shifts -- without it, nearly every run would
    report a false change."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_snippet(text: str, length: int = SNIPPET_LENGTH) -> str:
    if len(text) <= length:
        return text
    return text[:length].rstrip() + "…"


def cap_text(text: str, length: int = MAX_STORED_TEXT_LENGTH) -> str:
    """Cap what gets committed to state/*.json per watch. Same idea as
    make_snippet but much longer -- this is the copy used for real diffing,
    not just a human-readable preview."""
    if len(text) <= length:
        return text
    return text[:length].rstrip() + "…"


def word_diff(old_text: str, new_text: str, context: int = 6, max_len: int = 6000) -> str:
    """Compact word-level diff between two normalized page texts.

    normalize_whitespace() collapses every page to a single line, so a
    git-style line diff would just show "the whole line changed" -- useless.
    This treats each word as a unit instead: unchanged runs longer than
    `context` words on each side are elided with '...', and changed runs are
    marked [-removed-] / {+added+}, inline. That's dense enough to feed an
    LLM without dumping the whole page, and it actually shows *what* moved.
    """
    old_words = old_text.split(" ") if old_text else []
    new_words = new_text.split(" ") if new_text else []

    matcher = difflib.SequenceMatcher(None, old_words, new_words, autojunk=False)
    ops = matcher.get_opcodes()
    out: list[str] = []

    for idx, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            words = old_words[i1:i2]
            if len(words) <= context * 2:
                out.append(" ".join(words))
            else:
                out.append(
                    " ".join(words[:context]) + " ... " + " ".join(words[-context:])
                )
        elif tag == "delete":
            out.append("[-" + " ".join(old_words[i1:i2]) + "-]")
        elif tag == "insert":
            out.append("{+" + " ".join(new_words[j1:j2]) + "+}")
        elif tag == "replace":
            out.append("[-" + " ".join(old_words[i1:i2]) + "-]")
            out.append("{+" + " ".join(new_words[j1:j2]) + "+}")

    result = " ".join(out)
    if len(result) > max_len:
        result = result[:max_len].rstrip() + " …(truncated)"
    return result


def slug_for(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "watch"


def state_path(slug: str) -> Path:
    return STATE_DIR / f"{slug}.json"


def load_state(slug: str) -> dict:
    path = state_path(slug)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(slug: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = state_path(slug)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
