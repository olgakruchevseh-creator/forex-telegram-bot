"""Memory-bounded tail readers for append-only railway JSONL journals.

Never call Path.read_text() on production journals: decision_journal can be gigabytes.
These helpers read only enough trailing bytes to recover the requested count of
complete lines, regardless of the size of the older data.
"""
from __future__ import annotations

import json
import gzip
from collections import deque
from pathlib import Path

from journal_storage import archive_files


def tail_lines(path, limit=4000):
    # Stage 27D: recent samples may span a rollover boundary.
    recent = list(iter_tail_lines_reverse(path, limit))
    recent.reverse()
    return recent


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
def _iter_single_file_reverse(path, limit=4000):
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


def iter_tail_lines_reverse(path, limit=4000):
    """Newest-first recent physical lines across active and rotated segments.

    The recent active file is visited first. Older segments are read only when
    the requested line limit is not reached. Never reads an entire 3-GB legacy
    JSONL into RAM. gzip archives are decoded as a bounded deque of lines.
    """
    path = Path(path)
    remaining = max(0, int(limit))
    if remaining == 0:
        return
    sources = ([path] if path.is_file() else []) + list(reversed(archive_files(path)))
    for source in sources:
        if remaining <= 0:
            break
        if source.suffix == '.gz':
            # gzip is forward-only; each new archive is bounded in size.
            try:
                with gzip.open(source, 'rt', encoding='utf-8', errors='replace') as inp:
                    recent = deque(inp, maxlen=remaining)
                for row in reversed(recent):
                    remaining -= 1
                    yield row.rstrip('\r\n')
            except (OSError, EOFError, ValueError):
                continue  # Do not hide newer active events after an old corrupt gzip.
        else:
            for row in _iter_single_file_reverse(source, remaining):
                remaining -= 1
                yield row
                if remaining <= 0:
                    break
