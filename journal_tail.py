"""Memory-bounded tail readers for append-only railway JSONL journals.

Never call Path.read_text() on production journals: decision_journal can be gigabytes.
These helpers read only enough trailing bytes to recover the requested count of
complete lines, regardless of the size of the older data.
"""
from __future__ import annotations

import json
from pathlib import Path


def tail_lines(path, limit=4000):
    path = Path(path)
    limit = max(0, int(limit))
    if not limit or not path.is_file():
        return []
    with path.open("rb") as stream:
        stream.seek(0, 2)
        position = stream.tell()
        blocks = []
        line_breaks = 0
        # Read one additional newline to avoid taking a truncated first row.
        while position > 0 and line_breaks <= limit:
            size = min(65536, position)
            position -= size
            stream.seek(position)
            block = stream.read(size)
            blocks.append(block)
            line_breaks += block.count(b"\n")
    return [part.decode("utf-8", errors="replace") for part in
            b"".join(reversed(blocks)).splitlines()[-limit:]]


def tail_jsonl(path, limit=4000):
    result = []
    for line in tail_lines(path, limit):
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                result.append(obj)
        except (TypeError, ValueError):
            continue
    return result
