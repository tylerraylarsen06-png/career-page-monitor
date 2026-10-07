"""Optional AI interpretation step. run.py already knows, via a plain hash
comparison, that a watch's content genuinely changed -- but "changed" isn't
the same as "worth telling Tyler about." This module sends the raw
word-diffs for every changed watch to the Gemini API, along with profile.md
(who Tyler is / what he cares about) and each watch's optional `goal`, and
asks for the same terse "what changed / what to do" line format used
elsewhere (mirrors the Firecrawl "monitor translator" digest pattern).

Fails soft, always. If GEMINI_API_KEY isn't set, the request errors, or
Gemini itself decides nothing crossed the bar, translate_digest() returns
(None, ...) and digest.py falls back to the plain before/after text format
that existed before this module. A bad API call or a missing key should
never be the reason a real change goes unreported.

translate_digest() returns a (summary, judged) pair, not just a summary,
because run.py needs to tell two different kinds of "nothing to show"
apart when deciding whether to @-mention Tyler on Discord:
  - judged=True, summary=None  -> Gemini actually ran and confirmed this
    is noise. Trust it, don't ping.
  - judged=False, summary=None -> Gemini never got a verdict (no key, or
    the call failed). Unknown, not "confirmed fine" -- ping anyway so a
    real change never gets silently missed during an outage.
"""
from __future__ import annotations

import os
import sys

import requests

from monitors.common import REPO_ROOT, WatchOutcome, word_diff

PROFILE_PATH = REPO_ROOT / "profile.md"

# Overridable via GEMINI_MODEL if you want to trade cost/speed for quality
# (e.g. a "-lite" variant is cheaper/faster; a "-pro" variant is slower and
# has a much smaller free-tier daily request limit). Confirmed against
# Google's live API docs on 2026-08-15 -- the gemini-2.5-* line has been
# retired (was 404ing), gemini-3.7-flash is current.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.7-flash")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
REQUEST_TIMEOUT = 60

NOTHING_MARKER = "NOTHING_MEANINGFUL"

RULES = """\
General rules for judging a diff, regardless of what the watch is for:
- Ignore purely cosmetic changes: "opens in new window" labels, tracking
  pixels, ad/analytics churn, copyright years, relative timestamps ("2
  hours ago"), view/like counters, related-content widgets, cookie
  banners, and reordering of items whose actual content didn't change.
- If a diff is mostly noise but has one real, on-topic change buried in
  it, report only the real change -- don't pad the response with noise.
- If NONE of the changed watches below contain anything genuinely worth
  Tyler's attention, respond with exactly the single line NOTHING_MEANINGFUL
  and nothing else -- no explanation, no partial credit."""

OUTPUT_FORMAT = """\
For each watch with a genuinely meaningful change, write exactly one line
in this shape:

**<Watch name> — <what changed, plainly>: <what Tyler should do, or "nothing to do" if it's borderline>.**

No preamble, no headers, no explanation of how this system works. One line
per watch that clears the bar. If none do, reply with exactly:
NOTHING_MEANINGFUL"""


def _load_profile() -> str:
    if PROFILE_PATH.exists():
        text = PROFILE_PATH.read_text().strip()
        if text:
            return text
    return "(no profile.md found in the repo root -- judge generically, favor caution over noise)"


def _build_prompt(changes: list[WatchOutcome]) -> str:
    parts = [
        "You are translating raw website-diff data into a short, plain-English "
        "alert for one person. A hash comparison already confirmed each page "
        "below genuinely changed -- your job is deciding whether the change "
        "matters to this specific person, and saying so in one line each.",
        "PERSON PROFILE:\n" + _load_profile(),
        RULES,
        OUTPUT_FORMAT,
        "CHANGES TO JUDGE:",
    ]
    for c in changes:
        block = [f"### {c.name}", f"URL: {c.url}"]
        if c.goal:
            block.append(f"What matters for this watch specifically: {c.goal}")
        block.append("Diff (word-level; [-removed-] {+added+}, '...' = unchanged run elided):")
        block.append(word_diff(c.old_text or "", c.new_text or ""))
        parts.append("\n".join(block))
    return "\n\n".join(parts)


def translate_digest(changes: list[WatchOutcome]) -> tuple[str | None, bool]:
    """Returns (summary, judged) -- see module docstring for what the two
    None-summary cases (judged=True vs judged=False) mean to the caller."""
    if not changes:
        return None, True

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[translate] GEMINI_API_KEY not set, skipping AI translation", file=sys.stderr)
        return None, False

    prompt = _build_prompt(changes)

    try:
        resp = requests.post(
            GEMINI_URL,
            params={"key": api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as exc:  # noqa: BLE001 - any failure here must fall back, never break the run
        print(f"[translate] Gemini call failed, falling back to raw digest: {exc}", file=sys.stderr)
        return None, False

    if not text or text == NOTHING_MARKER:
        print("[translate] Gemini judged nothing meaningful")
        return None, True

    return text, True
