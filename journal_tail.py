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


# Stage 8B: memory-bounded reverse iterator. The legacy tail_lines() returns
# full serialized rows and keeps them all in memory; production journal rows
# may include large context snapshots. Read one complete line at a time instead.
def iter_tail_lines_reverse(path, limit=4000):
    """Yield up to *limit* physical lines, newest first, using 64-KiB blocks.

    A line spanning blocks is reconstructed before it is yielded. No earlier
    lines are read just to parse a bounded trailing sample.
    """
    path = Path(path)
    limit = max(0, int(limit))
    if not limit or not path.is_file():
        return
    with path.open('rb') as stream:
        stream.seek(0, 2)
        position = stream.tell()
        if not position:
            return
        stream.seek(-1, 2)
        trailing_newline = stream.read(1) == b'\n'
        pending = b''
        emitted = 0
        while position > 0 and emitted < limit:
            size = min(65536, position)
            position -= size
            stream.seek(position)
            parts = stream.read(size).split(b'\n')
            for part in reversed(parts[1:]):
                row = part + pending
                pending = b''
                if trailing_newline:
                    trailing_newline = False
                    continue  # The empty segment after the final newline.
                emitted += 1
                yield row.decode('utf-8', errors='replace')
                if emitted >= limit:
                    return
            pending = parts[0] + pending
        if position == 0 and emitted < limit and pending:
            yield pending.decode('utf-8', errors='replace')


def tail_jsonl_fields(path, limit, fields):
    """Read recent JSONL rows but retain only required top-level fields.

    Preserve the legacy chronological order and physical-line limit. Parsing
    still sees complete rows; only small projections survive between rows.
    """
    keep = frozenset(fields)
    result = []
    for line in iter_tail_lines_reverse(path, limit):
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                result.append({key: row[key] for key in keep if key in row})
        except (TypeError, ValueError):
            continue
    result.reverse()
    return result
