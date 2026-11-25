"""Stage 27D: lossless rotation of *new* JSONL journals; no legacy purge.

An oversized existing Railway journal is moved (rename only) to a timestamped
plain JSONL segment. This DOES NOT reclaim its disk space. Never automatically
compress, rewrite or delete any pre-existing giant legacy segment. New bounded
segments may be gzip-compressed only after independent byte-for-byte verification.
No market or signal verdict is affected by this OBSERVE_ONLY storage helper.
"""
from __future__ import annotations

import gzip
import hashlib
import logging
import os
import re
import shutil
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import config as cfg

log = logging.getLogger('fxbot.journal_storage')
MANAGED_NAMES = frozenset({
    'decision_journal.jsonl', 'decision_sent_history.jsonl',
    'killer_decisions.jsonl', 'killer_diagnostics.jsonl',
    'decision_replay.jsonl',
})
_LOCK = threading.RLock()
_STAMP_RE = re.compile(r'^\d{8}T\d{12}Z$')


def enabled() -> bool:
    return bool(getattr(cfg, 'STAGE27D_JOURNAL_PROTECTION', True))


def archive_files(path: Path) -> list[Path]:
    """Oldest -> newest. Only exact managed basename + timestamp; never glob other files."""
    path = Path(path)
    if path.name not in MANAGED_NAMES:
        return []
    prefix = path.stem + '.'
    by_stamp = {}
    for item in path.parent.glob(prefix + '*'):
        if item.is_symlink() or not item.is_file():
            continue
        name = item.name
        if name.endswith('.jsonl.gz'):
            stamp = name[len(prefix):-len('.jsonl.gz')]
        elif name.endswith('.jsonl'):
            stamp = name[len(prefix):-len('.jsonl')]
        else:
            continue
        if _STAMP_RE.fullmatch(stamp):
            # A crash between gzip publish and raw unlink can leave BOTH
            # copies. Always prefer the untouched raw source, not duplicates.
            old = by_stamp.get(stamp)
            if old is None or (old.suffix == '.gz' and item.suffix == '.jsonl'):
                by_stamp[stamp] = item
    return [by_stamp[stamp] for stamp in sorted(by_stamp)]


def _space_available(path: Path) -> int | None:
    try:
        st = os.statvfs(path)
        return int(st.f_bavail * st.f_frsize)
    except OSError:
        return None


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def _rotate(path: Path) -> Path:
    """Atomic rename; does not copy, truncate or remove original contents."""
    for n in range(100):
        stamp = _stamp() if n == 0 else f'{datetime.now(timezone.utc):%Y%m%dT%H%M%S}{n:06d}Z'
        candidate = path.with_name(f'{path.stem}.{stamp}.jsonl')
        if candidate.exists():
            continue
        path.replace(candidate)
        log.warning('JOURNAL_ROTATED original=%s archived=%s bytes=%d',
                    path.name, candidate.name, candidate.stat().st_size)
        return candidate
    raise OSError('Unable to generate unique archive name')


def _digest(stream):
    hexdigest = hashlib.sha256()
    byte_count = 0
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        hexdigest.update(chunk)
        byte_count += len(chunk)
    return byte_count, hexdigest.hexdigest()


def _compress_verified(source: Path) -> bool:
    """Never unlink a source unless decompression reproduces its exact bytes.

    On any failure the source stays readable, with temporary files discarded.
    """
    target = source.with_suffix(source.suffix + '.gz')
    if target.exists() or source.is_symlink():
        return False
    temp = source.with_name('.' + target.name + f'.{os.getpid()}.tmp')
    try:
        with source.open('rb') as reader, temp.open('xb') as output:
            with gzip.GzipFile(filename='', mode='wb', fileobj=output, compresslevel=6) as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        # Avoid wasting disk on incompressible archive segments.
        if temp.stat().st_size >= source.stat().st_size:
            log.warning('JOURNAL_ARCHIVE_NOT_SMALLER file=%s', source.name)
            return False
        # Check raw source and fully decoded gzip independently; gzip CRC is
        # validated during decoded reading, SHA-256 catches any content change.
        with source.open('rb') as raw, gzip.open(temp, 'rb') as decoded:
            original = _digest(raw)
            restored = _digest(decoded)
        if original != restored:
            log.error('JOURNAL_ARCHIVE_VERIFY_MISMATCH name=%s', source.name)
            return False
        # Do not replace an archive with matching name, even on a clock collision.
        if target.exists():
            return False
        temp.replace(target)
        # A verified compressed copy exists on the SAME disk before unlink.
        source.unlink()
        log.warning('JOURNAL_ARCHIVE_VERIFIED file=%s archive=%s raw_bytes=%d',
                    source.name, target.name, original[0])
        return True
    except (OSError, EOFError, ValueError) as exc:
        log.error('JOURNAL_COMPRESSION_SKIPPED file=%s error=%s', source.name, type(exc).__name__)
        return False
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def append_text(path: Path, line: str, *, critical: bool = False) -> bool:
    """Append one complete JSONL line with bounded new segments.

    At critically low disk space non-critical OBSERVE_ONLY rows are paused;
    delivered records remain eligible. Never blocks a trading decision.
    """
    path = Path(path)
    if not enabled() or path.name not in MANAGED_NAMES:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as stream:
            stream.write(line)
        return True
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        available = _space_available(path.parent)
        reserve = max(0, int(getattr(cfg, 'STAGE27D_DISK_RESERVE_BYTES', 200_000_000)))
        if not critical and available is not None and available < reserve:
            log.error('JOURNAL_WRITE_PAUSED_LOW_SPACE file=%s available=%d reserve=%d',
                      path.name, available, reserve)
            return False
        rotated = None
        limit = max(1024, int(getattr(cfg, 'STAGE27D_JOURNAL_SEGMENT_BYTES', 32_000_000)))
        if path.is_file() and path.stat().st_size + len(line.encode('utf-8')) > limit:
            rotated = _rotate(path)
        with path.open('a', encoding='utf-8') as stream:
            stream.write(line)
        # Legacy file can be several gigabytes. Do not attempt to copy or
        # compress it automatically when the Railway Volume is nearly full.
        if rotated and bool(getattr(cfg, 'STAGE27D_COMPRESS_NEW_SEGMENTS', True)):
            max_size = max(limit, int(getattr(cfg, 'STAGE27D_AUTO_COMPRESS_MAX_BYTES', 128_000_000)))
            size = rotated.stat().st_size
            free = _space_available(path.parent)
            if size <= max_size and (free is None or free > reserve + size):
                _compress_verified(rotated)
            else:
                log.warning('JOURNAL_LEGACY_LEFT_UNCHANGED file=%s bytes=%d free=%s',
                            rotated.name, size, free)
        return True
