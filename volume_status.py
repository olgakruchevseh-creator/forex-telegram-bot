"""Read-only, bounded Railway Volume inventory for a Telegram /storage command.

NEVER deletes, truncates, compresses, reads file contents, or opens SQLite.
The status is approximate; Railway's own accounting may differ from statvfs.
"""
from __future__ import annotations

import heapq
import os
import time
from pathlib import Path


def configured_root() -> Path | None:
    raw = (os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
           or os.getenv("STATE_DIR", "").strip())
    if not raw:
        return None
    return Path(raw).expanduser()


def _readable_size(n: int) -> str:
    x = float(n)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if x < 1024.0 or unit == "ТБ":
            return f"{x:.1f} {unit}" if unit != "Б" else f"{int(x)} Б"
        x /= 1024.0
    return "—"


def inventory(root: Path, *, max_files: int = 60_000, max_seconds: float = 3.0,
              top_n: int = 12) -> dict:
    """Count existing files using metadata only; bounded work on hot workers."""
    base = Path(root)
    if not base.is_dir():
        return {"error": "Папка Volume не найдена", "root": str(base)}
    base = base.resolve()
    began = time.monotonic()
    heap: list[tuple[int, str]] = []
    count = 0
    total_file_bytes = 0
    limited = False
    errors = 0
    for directory, folders, filenames in os.walk(base, followlinks=False):
        # Never traverse symlinked folders outside Railway Volume.
        folders[:] = [name for name in folders if not (Path(directory) / name).is_symlink()]
        for name in filenames:
            if count >= max_files or time.monotonic() - began >= max_seconds:
                limited = True
                break
            target = Path(directory) / name
            try:
                if target.is_symlink():
                    continue
                size = target.stat().st_size
            except (OSError, ValueError):
                errors += 1
                continue
            count += 1
            total_file_bytes += size
            rel = target.relative_to(base).as_posix()
            if len(heap) < top_n:
                heapq.heappush(heap, (size, rel))
            elif (size, rel) > heap[0]:
                heapq.heapreplace(heap, (size, rel))
        if limited:
            break
    try:
        stat = os.statvfs(base)
        capacity = int(stat.f_blocks * stat.f_frsize)
        available = int(stat.f_bavail * stat.f_frsize)
        used = max(0, capacity - int(stat.f_bfree * stat.f_frsize))
    except OSError:
        capacity = available = used = None
    return {"root": str(base), "file_count": count,
            "file_bytes": total_file_bytes, "top": sorted(heap, reverse=True),
            "capacity": capacity, "available": available, "used": used,
            "limited": limited, "errors": errors}


def report_ru(root: Path | None = None) -> str:
    target = root if root is not None else configured_root()
    if target is None:
        return ("📁 ХРАНИЛИЩЕ FOREX · OBSERVE_ONLY\n"
                "STATE_DIR и RAILWAY_VOLUME_MOUNT_PATH не заданы. "
                "Нужно проверить путь Volume в Railway.")
    data = inventory(target)
    if data.get("error"):
        return ("📁 ХРАНИЛИЩЕ FOREX · OBSERVE_ONLY\n"
                f"{data['error']}: {data['root']}")
    lines = ["📁 ХРАНИЛИЩЕ FOREX · OBSERVE_ONLY",
             f"Корень: {data['root']}",
             f"Проверено файлов: {data['file_count']}",
             f"Сумма размеров найденных файлов: {_readable_size(data['file_bytes'])}"]
    if data['capacity'] is not None and data['capacity'] > 0:
        percent = 100 * data['used'] / data['capacity']
        lines.append(f"Файловая система: {_readable_size(data['used'])} / {_readable_size(data['capacity'])} ({percent:.1f}%)")
        lines.append(f"Доступно по statvfs: {_readable_size(data['available'])}")
    if data['capacity'] is not None and data['capacity'] > 0:
        pct = 100 * data['used'] / data['capacity']
        if pct >= 90:
            lines.append("⚠️ КРИТИЧНО: Volume ≥90%; проверить свободное место, важные журналы могут приостановить некритичные записи")
        elif pct >= 80:
            lines.append("⚠️ ВНИМАНИЕ: Volume ≥80%; необходима внешняя резервная копия и архивирование")
        elif pct >= 70:
            lines.append("⚠️ Volume ≥70%; проверить рост журналов и наличие резервных копий")
    # Этап 27D: только метаданные, никакого удаления. Большая старая
    # несжатая история остается на Volume после атомарной ротации.
    from journal_storage import MANAGED_NAMES, archive_files
    archived = [f for name in MANAGED_NAMES for f in archive_files(Path(target) / name)]
    archive_bytes = sum(p.stat().st_size for p in archived)
    legacy = [p for p in archived if p.suffix == '.jsonl' and p.stat().st_size > 128_000_000]
    if archived:
        lines.append(f"Этап 27D · части журналов: {len(archived)} · {_readable_size(archive_bytes)}; "
                     f"несжатых крупных: {len(legacy)}")
    if legacy:
        lines.append("⚠️ Большая старая история сохранена без изменений; для освобождения места нужен отдельный безопасный экспорт/сжатие")
    rotations = list(Path(target).glob("unified_signal_decisions.*.jsonl"))
    rotated_bytes = sum(p.stat().st_size for p in rotations if p.is_file() and not p.is_symlink())
    lines.append(f"Ротации unified_signal_decisions: {len(rotations)} файлов · {_readable_size(rotated_bytes)}")
    lines.append("Самые большие файлы:")
    for size, path in data["top"]:
        lines.append(f"• {_readable_size(size)} — {path}")
    if not data["top"]:
        lines.append("• Файлы не найдены")
    if data["limited"]:
        lines.append("⚠️ Частичный обход: достигнут лимит времени или количества файлов")
    if data["errors"]:
        lines.append(f"⚠️ Ошибок чтения метаданных: {data['errors']}")
    lines.append("Ничего не удалено. Значения могут отличаться от Railway Volume Usage.")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report_ru())
