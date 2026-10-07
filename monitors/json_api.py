"""JSON API source type: fetch JSON, scope it down via a simple dotted
json_path (e.g. "data.jobs" or "results[0].status") and/or a regex applied
to the serialized result, then normalize and hash.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from .common import DEFAULT_MIN_CONTENT_LENGTH, FetchError, fetch, normalize_whitespace

_PATH_SEGMENT = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _walk_json_path(data, path: str):
    node = data
    for name, index in _PATH_SEGMENT.findall(path):
        if name:
            if not isinstance(node, dict) or name not in node:
                raise FetchError(f"json_path segment {name!r} not found (path={path!r})")
            node = node[name]
        elif index != "":
            idx = int(index)
            if not isinstance(node, list) or idx >= len(node):
                raise FetchError(f"json_path index [{idx}] out of range (path={path!r})")
            node = node[idx]
    return node


def extract_content(
    raw_text: str,
    json_path: Optional[str] = None,
    regex: Optional[str] = None,
    min_content_length: int = DEFAULT_MIN_CONTENT_LENGTH,
) -> str:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise FetchError(f"response is not valid JSON: {exc}") from exc

    node = _walk_json_path(data, json_path) if json_path else data
    text = json.dumps(node, sort_keys=True, ensure_ascii=False)

    if regex:
        found = re.findall(regex, text, flags=re.IGNORECASE | re.DOTALL)
        if not found:
            raise FetchError(f"regex {regex!r} matched nothing in scoped JSON")
        text = " ".join(f if isinstance(f, str) else " ".join(f) for f in found)

    text = normalize_whitespace(text)

    if len(text) < min_content_length:
        raise FetchError(
            f"scoped JSON content only {len(text)} chars (< min {min_content_length})"
        )

    return text


def check(url: str, json_path: Optional[str] = None, regex: Optional[str] = None,
          min_content_length: int = DEFAULT_MIN_CONTENT_LENGTH) -> str:
    result = fetch(url)
    return extract_content(
        result.raw_text, json_path=json_path, regex=regex,
        min_content_length=min_content_length,
    )
