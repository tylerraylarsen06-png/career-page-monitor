"""Builds and sends exactly one digest message per run to a Discord webhook,
summarizing every change and every failure collected by run.py. If there's
nothing to report, build_digest() returns None and send() is never called --
see run.py.
"""
from __future__ import annotations

import os

import requests

from monitors.common import WatchOutcome, make_snippet

DISCORD_LIMIT = 2000
CHANGES_HEADING = "\U0001f504 **CHANGES**"
FAILURES_HEADING = "⚠️ **FAILURES**"
SECTION_SEP = "\n\n---\n\n"


def _format_change(c: WatchOutcome) -> str:
    """Raw before/after fallback, used only when translate.py didn't
    produce a Gemini-judged summary (no API key, call failed, etc). Text is
    now stored in full (capped) rather than pre-truncated, so make_snippet()
    is applied here at render time instead."""
    lines = [f"**{c.name}**", f"<{c.url}>"]
    if c.old_text is not None:
        lines.append(f"before: {make_snippet(c.old_text)}")
    lines.append(f"after: {make_snippet(c.new_text or '')}")
    return "\n".join(lines)


def _format_failure(f: WatchOutcome) -> str:
    return f"**{f.name}** — <{f.url}>\nerror: {f.error}"


def _render(header: str, change_blocks: list[str], changes_cut: int,
            failure_blocks: list[str], failures_cut: int,
            translated_summary: str | None = None) -> str:
    sections = [header]

    if change_blocks or changes_cut or translated_summary:
        block = CHANGES_HEADING
        if translated_summary:
            # Gemini already decided what's worth saying and in what words --
            # use it verbatim instead of the raw per-watch blocks.
            block += "\n\n" + translated_summary
        elif change_blocks:
            block += "\n\n" + "\n\n".join(change_blocks)
        if changes_cut:
            block += f"\n\n_…{changes_cut} more change{'s' if changes_cut != 1 else ''} omitted (2000-char limit)_"
        sections.append(block)

    if failure_blocks or failures_cut:
        block = FAILURES_HEADING
        if failure_blocks:
            block += "\n\n" + "\n\n".join(failure_blocks)
        if failures_cut:
            block += f"\n\n_…{failures_cut} more failure{'s' if failures_cut != 1 else ''} omitted (2000-char limit)_"
        sections.append(block)

    return SECTION_SEP.join(sections)


def build_digest(
    changes: list[WatchOutcome],
    failures: list[WatchOutcome],
    translated_summary: str | None = None,
) -> str | None:
    """Returns the full Discord message content, or None if there's nothing
    to report. When the message doesn't fit in Discord's 2000-char limit,
    failures are trimmed before changes -- a broken fetch is operational
    noise, an actual page change is the whole point of this system -- and
    the message says how many of each were cut rather than erroring.

    `translated_summary` is the optional Gemini-judged digest from
    translate.py. When present it replaces the raw per-watch before/after
    blocks in the CHANGES section; when None (no API key, call failed, or
    nothing was judged meaningful but there were alert-worthy failures),
    the original raw format is used."""
    if not changes and not failures:
        return None

    header_bits = []
    if changes:
        header_bits.append(f"{len(changes)} change{'s' if len(changes) != 1 else ''}")
    if failures:
        header_bits.append(f"{len(failures)} failure{'s' if len(failures) != 1 else ''}")
    header = "**career-page-monitor** — " + ", ".join(header_bits)

    change_blocks = [] if translated_summary else [_format_change(c) for c in changes]
    failure_blocks = [_format_failure(f) for f in failures]

    changes_cut = 0
    failures_cut = 0
    text = _render(header, change_blocks, changes_cut, failure_blocks, failures_cut, translated_summary)

    while len(text) > DISCORD_LIMIT and (failure_blocks or change_blocks):
        if failure_blocks:
            failure_blocks.pop()
            failures_cut += 1
        else:
            change_blocks.pop()
            changes_cut += 1
        text = _render(header, change_blocks, changes_cut, failure_blocks, failures_cut, translated_summary)

    if len(text) > DISCORD_LIMIT:
        # Degenerate case: even the header + omission notes alone don't fit
        # (e.g. one watch with an absurdly long name/URL). Hard-truncate as
        # a last resort rather than failing to send anything.
        text = text[: DISCORD_LIMIT - 15].rstrip() + "\n…(truncated)"

    return text


def send(content: str, ping: bool = False) -> None:
    """`ping` controls whether the message @-mentions DISCORD_USER_ID.
    False by design for failures-only messages and for changes Gemini
    explicitly judged as noise -- see translate.py's module docstring for
    the full ping/no-ping decision. If DISCORD_USER_ID isn't set, ping is
    a no-op regardless of the flag (fails soft, same as everything else)."""
    webhook_url = os.environ["DISCORD_WEBHOOK_URL"]
    user_id = os.environ.get("DISCORD_USER_ID")

    if ping and user_id:
        content = f"<@{user_id}> {content}"
        if len(content) > DISCORD_LIMIT:
            content = content[: DISCORD_LIMIT - 1].rstrip() + "…"

    resp = requests.post(webhook_url, json={"content": content}, timeout=30)
    resp.raise_for_status()
