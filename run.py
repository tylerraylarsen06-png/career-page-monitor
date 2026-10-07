#!/usr/bin/env python3
"""Entry point: load every config/*.yaml watch, check it, diff against
committed state, collect all changes + failures from the run, and send
ONE Discord digest message if there's anything worth reporting. State is
written to state/*.json; the GitHub Actions workflow commits it back to
the repo so everything persists with no database.
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import yaml

import digest
import translate
from monitors import html_page_diff, json_api, rss_atom
from monitors.common import (
    REPO_ROOT,
    STATE_DIR,
    FetchError,
    WatchOutcome,
    cap_text,
    content_hash,
    load_state,
    save_state,
)

CONFIG_DIR = REPO_ROOT / "config"
FAILURE_ALERT_THRESHOLD = int(os.environ.get("FAILURE_ALERT_THRESHOLD", "3"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_one(cfg: dict) -> str:
    """Dispatch to the right source-type module. Returns normalized
    content text, or raises FetchError."""
    source_type = cfg["type"]
    url = cfg["url"]
    min_len = cfg.get("min_content_length", 20)

    if source_type == "html":
        return html_page_diff.check(
            url, selector=cfg.get("selector"), regex=cfg.get("regex"),
            min_content_length=min_len,
        )
    elif source_type == "rss":
        return rss_atom.check(url, regex=cfg.get("regex"), min_content_length=min_len)
    elif source_type == "json":
        return json_api.check(
            url, json_path=cfg.get("json_path"), regex=cfg.get("regex"),
            min_content_length=min_len,
        )
    else:
        raise FetchError(f"unknown source type {source_type!r}")


def process_watch(config_path: Path) -> WatchOutcome:
    slug = config_path.stem
    cfg = yaml.safe_load(config_path.read_text())
    name = cfg.get("name", slug)
    url = cfg["url"]
    goal = cfg.get("goal")  # optional plain-English hint, passed to translate.py

    state = load_state(slug)

    try:
        text = check_one(cfg)
    except FetchError as exc:
        print(f"[FAIL] {name}: {exc}", file=sys.stderr)
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        state["last_checked_at"] = now_iso()
        state["last_error"] = str(exc)
        save_state(slug, state)
        return WatchOutcome(slug=slug, name=name, url=url, status="failed", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - genuinely unexpected, still don't corrupt state
        print(f"[FAIL] {name}: unexpected error: {exc}", file=sys.stderr)
        traceback.print_exc()
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        state["last_checked_at"] = now_iso()
        state["last_error"] = f"unexpected: {exc}"
        save_state(slug, state)
        return WatchOutcome(slug=slug, name=name, url=url, status="failed", error=f"unexpected: {exc}")

    new_hash = content_hash(text)
    capped_text = cap_text(text)
    old_hash = state.get("last_hash")
    # "text" is the current field; "snippet" is read as a fallback so watches
    # that last ran before this change (state committed with only a 400-char
    # snippet) don't blow up -- they just get one run with a thinner diff,
    # then self-heal to the full capped text from then on.
    old_text = state.get("text", state.get("snippet"))

    state["consecutive_failures"] = 0
    state["last_checked_at"] = now_iso()
    state["last_success_at"] = now_iso()
    state.pop("last_error", None)
    state.pop("snippet", None)  # legacy key, fully replaced by "text" below

    if old_hash is None:
        state["last_hash"] = new_hash
        state["text"] = capped_text
        save_state(slug, state)
        print(f"[BASELINE] {name}: baseline established, no report on first run")
        return WatchOutcome(slug=slug, name=name, url=url, status="baseline", goal=goal)

    if new_hash != old_hash:
        state["last_hash"] = new_hash
        state["text"] = capped_text
        state["last_changed_at"] = now_iso()
        save_state(slug, state)
        print(f"[CHANGED] {name}")
        return WatchOutcome(
            slug=slug, name=name, url=url, status="changed",
            old_text=old_text, new_text=capped_text, goal=goal,
        )

    save_state(slug, state)
    print(f"[unchanged] {name}")
    return WatchOutcome(slug=slug, name=name, url=url, status="unchanged", goal=goal)


def write_heartbeat() -> None:
    """Unconditionally touch a timestamp file every run so the workflow's
    commit-back step always has a diff to push. This is the workaround for
    GitHub's 60-day scheduled-workflow auto-disable: it disables schedules
    after 60 days with no repo activity, and content-driven state commits
    alone can't guarantee that (a watch might genuinely not change for
    months). Guaranteeing a daily commit removes the risk entirely."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    heartbeat_path = STATE_DIR / "_heartbeat.json"
    heartbeat_path.write_text(f'{{\n  "last_run_at": "{now_iso()}"\n}}\n')


def main() -> int:
    config_paths = sorted(CONFIG_DIR.glob("*.yaml"))
    if not config_paths:
        print("no config/*.yaml files found", file=sys.stderr)
        return 1

    changes: list[WatchOutcome] = []
    failures: list[WatchOutcome] = []
    alertworthy_failures: list[WatchOutcome] = []

    for config_path in config_paths:
        outcome = process_watch(config_path)
        if outcome.status == "changed":
            changes.append(outcome)
        elif outcome.status == "failed":
            failures.append(outcome)
            state = load_state(outcome.slug)
            if state.get("consecutive_failures", 0) >= FAILURE_ALERT_THRESHOLD:
                alertworthy_failures.append(outcome)

    write_heartbeat()

    if changes or alertworthy_failures:
        # Optional AI interpretation pass: turns raw word-diffs into a
        # judged, plain-English line per watch (same idea as the Firecrawl
        # "monitor translator" digest). Three distinct outcomes, each
        # rendered differently -- collapsing "confirmed noise" and "Gemini
        # unavailable" into the same raw dump (the original design) is
        # exactly the verbosity problem this block now avoids:
        gemini_summary, judged = translate.translate_digest(changes)
        if gemini_summary:
            # Gemini found something real -- use its line(s) verbatim.
            changes_display = gemini_summary
            ping = True
        elif changes and judged:
            # Gemini ran and confirmed none of it matters -- one quiet
            # line, not a wall of before/after text. Don't ping.
            changes_display = (
                f"Checked {len(changes)} changed page"
                f"{'s' if len(changes) != 1 else ''} -- nothing Gemini flagged as meaningful."
            )
            ping = False
        elif changes:
            # Gemini unavailable (no key / call failed) -- unknown, not
            # confirmed-fine, so fall back to the full raw format
            # (changes_display=None signals build_digest to do that) and
            # ping so a real change is never silently missed.
            changes_display = None
            ping = True
        else:
            # No changes at all, only alert-worthy failures.
            changes_display = None
            ping = False

        content = digest.build_digest(changes, failures, translated_summary=changes_display)
        try:
            digest.send(content, ping=ping)
            print(f"[digest] sent ({len(content)} chars, ping={ping})")
        except Exception as exc:  # noqa: BLE001
            print(f"[digest] FAILED TO SEND: {exc}", file=sys.stderr)
            traceback.print_exc()
            return 1
    else:
        print("[digest] nothing to report, no message sent")

    print(
        f"\nsummary: {len(changes)} changed, {len(failures)} failed "
        f"({len(alertworthy_failures)} alert-worthy), "
        f"{len(config_paths) - len(changes) - len(failures)} unchanged/baseline"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
