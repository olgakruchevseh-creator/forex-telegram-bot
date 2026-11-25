"""Stage 27E: opt-in off-volume backup and verified legacy journal compaction.

NEVER deletes a legacy source unless:
  (a) its immutable rotated segment was uploaded via HTTPS,
  (b) an independent HTTPS GET streamed the same bytes back (SHA-256 verified),
  (c) a complete local compressed copy was independently verified, and
  (d) the raw source has not changed since export.

No scheduled work, no initial-deploy migration, no automatic deletion. Railway
backup snapshots alone cannot satisfy (a)-(b). The active journal is never touched.
"""
from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import config as cfg
from journal_storage import archive_files

log = logging.getLogger('fxbot.storage_archive_27e')

# Aliases are fixed; cannot be turned into arbitrary paths by an admin command.
_ALLOWED = {'decision': 'decision_journal.jsonl', 'killer': 'killer_decisions.jsonl'}
_BLOCK = 1024 * 1024


class SafetyAbort(RuntimeError):
    """Expected safe failure. Do not log a signed remote URL or its query."""


def root_dir() -> Path:
    raw = (os.environ.get('STATE_DIR', '').strip()
           or os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '').strip())
    if not raw:
        raise SafetyAbort('STATE_DIR не задан — операции отключены')
    root = Path(raw)
    if not root.is_dir() or root.is_symlink():
        raise SafetyAbort('Корень Volume отсутствует или является ссылкой')
    return root.resolve()


def _legacy(root: Path, alias: str) -> Path:
    if alias not in _ALLOWED:
        raise SafetyAbort('Допустимые журналы: decision, killer')
    base = root / _ALLOWED[alias]
    candidates = [p for p in archive_files(base)
                  if p.suffix == '.jsonl' and p.is_file() and not p.is_symlink()]
    if not candidates:
        raise SafetyAbort('Несжатых завершённых частей этого журнала нет')
    # Largest first: the primary 3.2 GB legacy segment is the highest priority.
    return max(candidates, key=lambda p: (p.stat().st_size, p.name))


def _folder(root: Path, *, create: bool = False) -> Path:
    folder = root / '.stage27e_verified'
    if folder.is_symlink():
        raise SafetyAbort('Папка подтверждений не должна быть ссылкой')
    if create:
        folder.mkdir(mode=0o700, exist_ok=True)
        folder.chmod(0o700)
    return folder


def _mark_path(root: Path, source: Path) -> Path:
    return _folder(root) / (source.name + '.verified.json')


def _stat(path: Path) -> tuple[int, int, int]:
    st = path.stat()
    return st.st_size, st.st_mtime_ns, st.st_ino


def _hash_reader(stream) -> tuple[int, str]:
    h = hashlib.sha256()
    count = 0
    while True:
        buf = stream.read(_BLOCK)
        if not buf:
            break
        count += len(buf)
        h.update(buf)
    return count, h.hexdigest()


def _sha_file(path: Path) -> tuple[int, str]:
    with path.open('rb') as f:
        return _hash_reader(f)


def _external_url(alias: str, verb: str) -> str:
    # Signed URLs are secret: Railway Variables only. Never echo or log them.
    key = 'STAGE27E_' + alias.upper() + '_' + verb + '_URL'
    url = os.environ.get(key, '').strip()
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise SafetyAbort('Нужна подписанная HTTPS-ссылка в Railway Variables: ' + key)
    return url


@contextmanager
def _exclusive(root: Path):
    folder = _folder(root, create=True)
    lockfile = folder / 'operation.lock'
    with lockfile.open('a+b') as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SafetyAbort('Уже выполняется операция 27E; повторный запуск отклонён') from None
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _save_mark(root: Path, source: Path, info: dict) -> None:
    folder = _folder(root, create=True)
    dest = _mark_path(root, source)
    # Create private temp and replace atomically. No signed URLs in metadata.
    fd, temporary = tempfile.mkstemp(prefix='.verification-', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(info, f, ensure_ascii=False, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, dest)
        _fsync_dir(folder)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _verified_manifest(root: Path, source: Path) -> dict:
    path = _mark_path(root, source)
    if path.is_symlink() or not path.is_file():
        raise SafetyAbort('Нет проверенной внешней копии; удаление запрещено')
    try:
        info = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise SafetyAbort('Повреждена запись проверки внешней копии') from exc
    if (info.get('name') != source.name
            or info.get('size') != _stat(source)[0]
            or info.get('mtime_ns') != _stat(source)[1]
            or info.get('verified') is not True
            or not isinstance(info.get('sha256'), str)
            or len(info.get('sha256', '')) != 64):
        raise SafetyAbort('Файл изменился после внешней проверки; удаление запрещено')
    return info


def _external_export(root: Path, source: Path, alias: str) -> dict:
    # requests is already a pinned application dependency.
    import requests

    put = _external_url(alias, 'PUT')
    get = _external_url(alias, 'GET')
    before = _stat(source)
    if before[0] < 1:
        raise SafetyAbort('Пустой архив не отправляем')
    raw_size, raw_sha = _sha_file(source)
    if raw_size != before[0] or _stat(source) != before:
        raise SafetyAbort('Исходный файл изменился при расчёте SHA-256')
    # Single-object PUT with a fixed Content-Length; 3.2 GB is below S3's
    # standard 5 GiB single PUT limit. Provider must support large single PUT.
    try:
        with source.open('rb') as stream:
            response = requests.put(
                put, data=stream,
                headers={'Content-Type': 'application/octet-stream',
                         'Content-Length': str(raw_size)},
                timeout=(20, 3600), allow_redirects=False)
        try:
            if response.status_code not in (200, 201, 204):
                raise SafetyAbort('Внешний PUT не подтвердил запись (HTTP %d)' % response.status_code)
        finally:
            response.close()
        if _stat(source) != before:
            raise SafetyAbort('Исходный файл изменился во время загрузки')
        # A HEAD, ETag or success from PUT alone is NOT independent verification.
        # Full streamed GET hashes actual remote bytes without staging to Volume.
        response = requests.get(get, headers={'Accept-Encoding': 'identity'},
                                stream=True, timeout=(20, 3600), allow_redirects=False)
        try:
            if response.status_code != 200:
                raise SafetyAbort('Контрольный GET не подтвердил доступность (HTTP %d)' % response.status_code)
            h = hashlib.sha256()
            count = 0
            for chunk in response.iter_content(chunk_size=_BLOCK):
                if not chunk:
                    continue
                count += len(chunk)
                if count > raw_size:
                    raise SafetyAbort('Удалённая копия длиннее оригинала')
                h.update(chunk)
            if count != raw_size or h.hexdigest() != raw_sha:
                raise SafetyAbort('Внешняя копия не совпала по SHA-256 и размеру')
        finally:
            response.close()
    except requests.RequestException as exc:
        # Requests exception strings may contain a secret signed URL; suppress.
        raise SafetyAbort('Ошибка сетевой передачи/проверки: ' + type(exc).__name__) from None
    if _stat(source) != before:
        raise SafetyAbort('Файл изменился после внешней проверки')
    info = {'schema': 1, 'name': source.name, 'size': raw_size,
            'mtime_ns': before[1], 'sha256': raw_sha,
            'verified': True, 'method': 'HTTPS_PUT_PLUS_FULL_GET_SHA256',
            'verified_at_utc': datetime.now(timezone.utc).isoformat()}
    _save_mark(root, source, info)
    log.warning('STAGE27E_REMOTE_VERIFIED file=%s bytes=%s sha256_prefix=%s',
                source.name, raw_size, raw_sha[:12])
    return info


def export(alias: str) -> str:
    root = root_dir()
    with _exclusive(root):
        source = _legacy(root, alias)
        info = _external_export(root, source, alias)
    return ('Внешняя копия проверена полным GET + SHA-256. '
            f'Файл: {info["name"]}; размер: {info["size"]} байт; '
            'локальный оригинал пока сохранён. Теперь допустима отдельная команда compact.')


class _LimitedWriter:
    def __init__(self, file, cap: int):
        self.file = file
        self.cap = cap
        self.count = 0

    def write(self, value: bytes) -> int:
        if self.count + len(value) > self.cap:
            raise SafetyAbort('Недостаточный свободный резерв: сжатие прервано, исходник сохранён')
        n = self.file.write(value)
        self.count += n
        return n

    def flush(self):
        return self.file.flush()


def _disk_free(root: Path) -> int:
    st = os.statvfs(root)
    return int(st.f_bavail * st.f_frsize)


def _verified_gzip(path: Path, size: int, digest: str) -> bool:
    try:
        with gzip.open(path, 'rb') as reader:
            return _hash_reader(reader) == (size, digest)
    except (EOFError, OSError, ValueError):
        return False


def _capture_hot_tail(source: Path, *, max_rows: int = 20_000,
                      max_bytes: int = 96_000_000) -> list[str]:
    """Read only the last capped bytes of a raw legacy file before unlink.

    The sidecar is a performance cache, not the authoritative history. When it
    lacks enough rows the reader falls back to full archive decoding.
    """
    from journal_tail import _iter_single_file_reverse
    rows: list[str] = []
    used = 0
    for row in _iter_single_file_reverse(source, max_rows):
        size = len(row.encode('utf-8', errors='replace')) + 1
        if used + size > max_bytes:
            break
        rows.append(row)
        used += size
    return rows


def _publish_hot_tail(gz_path: Path, newest_first: list[str]) -> bool:
    """Optional derived index of recent full JSONL rows; never alters archive."""
    if not newest_first:
        return False
    dest = gz_path.with_name(gz_path.name + '.tail.jsonl')
    meta = gz_path.with_name(gz_path.name + '.tail.meta.json')
    if dest.exists() or meta.exists():
        return False
    temp = gz_path.with_name('.' + gz_path.name + '.tail.' + str(os.getpid()) + '.tmp')
    try:
        with temp.open('xb') as output:
            os.chmod(temp, 0o600)
            checksum = hashlib.sha256()
            written = 0
            for row in reversed(newest_first):
                payload = (row + '\n').encode('utf-8')
                written += len(payload)
                checksum.update(payload)
                output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        size, mtime_ns, _ = _stat(gz_path)
        # Cache publication is atomic, and journal_tail validates metadata.
        temp.replace(dest)
        _save_json_atomic(meta, {'schema': 1, 'archive_size': size,
                                 'archive_mtime_ns': mtime_ns,
                                 'tail_count': len(newest_first),
                                 'tail_bytes': written,
                                 'tail_sha256': checksum.hexdigest()})
        _fsync_dir(gz_path.parent)
        return True
    except OSError:
        log.warning('STAGE27E_TAIL_CACHE_UNAVAILABLE file=%s', gz_path.name)
        return False
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _save_json_atomic(path: Path, data: dict) -> None:
    temp = path.with_name('.' + path.name + '.' + str(os.getpid()) + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as output:
            json.dump(data, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _compact(root: Path, source: Path) -> dict:
    info = _verified_manifest(root, source)
    before = _stat(source)
    # Validate local source against remote verification record immediately
    # before compaction, not just by mtime and size.
    if _sha_file(source) != (info['size'], info['sha256']):
        raise SafetyAbort('Локальный SHA-256 отличается от проверенной удалённой копии')
    reserve = max(200_000_000, int(getattr(cfg, 'STAGE27E_MIN_FREE_RESERVE_BYTES', 300_000_000)))
    free = _disk_free(root)
    # A smaller, bounded temporary gzip can be created even with only ~1GB
    # free. A large incompressible source is left intact on any budget failure.
    max_output = free - reserve - 20_000_000
    if max_output < 32_000_000:
        raise SafetyAbort('Недостаточно места для безопасного локального сжатия')
    target = source.with_suffix('.jsonl.gz')
    if target.is_symlink():
        raise SafetyAbort('Выходной архив не может быть ссылкой')
    temp = source.with_name('.' + source.name + '.stage27e.' + str(os.getpid()) + '.tmp')
    if temp.exists():
        raise SafetyAbort('Незавершённый временный архив уже существует')
    try:
        if not target.exists():
            with source.open('rb') as inp, temp.open('xb') as rawout:
                os.chmod(temp, 0o600)
                limited = _LimitedWriter(rawout, max_output)
                with gzip.GzipFile(fileobj=limited, mode='wb', filename='',
                                   compresslevel=5, mtime=0) as compressed:
                    shutil.copyfileobj(inp, compressed, _BLOCK)
                rawout.flush()
                os.fsync(rawout.fileno())
            if temp.stat().st_size >= info['size']:
                raise SafetyAbort('Сжатие не экономит место — оригинал сохранён')
            if not _verified_gzip(temp, info['size'], info['sha256']):
                raise SafetyAbort('SHA-256 локального gzip не совпал, оригинал сохранён')
            if _stat(source) != before:
                raise SafetyAbort('Исходник изменился во время сжатия')
            os.replace(temp, target)
            _fsync_dir(root)
        # Resume safely after crash between gzip publication and unlink.
        if not _verified_gzip(target, info['size'], info['sha256']):
            raise SafetyAbort('Готовый gzip не прошёл проверку, оригинал сохранён')
        if _stat(source) != before:
            raise SafetyAbort('Исходник изменился перед освобождением места')
        target_size = target.stat().st_size
        # Capture a small, exact recent suffix before deleting the large raw
        # segment. This prevents repeated full 3.2 GB gzip scans for hot tails.
        hot_tail = _capture_hot_tail(source)
        # Only now is the raw source redundant: remote full-byte verified
        # backup plus locally verified gzip both exist.
        source.unlink()
        _fsync_dir(root)
        try:
            _publish_hot_tail(target, hot_tail)
        except Exception:
            # A missing performance cache never invalidates the already
            # verified gzip + external backup or interrupts ongoing trading.
            log.warning('STAGE27E_TAIL_CACHE_FAILED file=%s', target.name)
        log.warning('STAGE27E_COMPACT_VERIFIED file=%s gz_bytes=%d saved_bytes=%d',
                    source.name, target_size, info['size'] - target_size)
        return {'source': source.name, 'gz': target.name,
                'saved': info['size'] - target_size, 'size': target_size}
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def compact(alias: str) -> str:
    root = root_dir()
    with _exclusive(root):
        source = _legacy(root, alias)
        result = _compact(root, source)
    return (f'Проверенное сжатие: {result["source"]} → {result["gz"]}. '
            f'Освобождено приблизительно {result["saved"] / (1024**3):.2f} ГБ. '
            'Внешняя копия сохранена; история доступна через journal_tail.')


def status() -> str:
    try:
        root = root_dir()
    except SafetyAbort as exc:
        return str(exc)
    lines = ['27E · БЕЗОПАСНОЕ АРХИВИРОВАНИЕ · без автоматического удаления']
    for alias in _ALLOWED:
        try:
            source = _legacy(root, alias)
            try:
                _verified_manifest(root, source)
                verified = 'внешняя копия подтверждена'
            except SafetyAbort:
                verified = 'внешняя копия НЕ подтверждена'
            lines.append(f'{alias}: {source.name} · {source.stat().st_size/(1024**3):.2f} ГБ · {verified}')
        except SafetyAbort:
            lines.append(f'{alias}: больших несжатых сегментов нет')
    lines.append('Экспорт/сжатие запускаются только отдельной командой владельца.')
    return '\n'.join(lines)


def run(action: str, alias: str | None = None) -> str:
    if action == 'status':
        return status()
    if action not in ('export', 'compact'):
        raise SafetyAbort('Команды: status, export decision|killer, compact decision|killer')
    if not getattr(cfg, 'STAGE27E_ENABLE_MANUAL_OPERATIONS', True):
        raise SafetyAbort('27E отключён настройкой')
    if alias not in _ALLOWED:
        raise SafetyAbort('Журнал должен быть decision или killer')
    if action == 'export':
        return export(alias)
    return compact(alias)
