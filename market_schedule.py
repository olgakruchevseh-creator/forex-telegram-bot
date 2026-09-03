"""Расписание автоматической работы бота по времени Europe/Amsterdam."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import config as cfg


def local_now(now: datetime | None = None) -> datetime:
    """Возвращает время Амстердама с автоматическим учётом летнего времени."""
    tz = ZoneInfo(getattr(cfg, "LOCAL_TZ_NAME", "Europe/Amsterdam"))
    if now is None:
        return datetime.now(timezone.utc).astimezone(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(tz)


def automatic_jobs_allowed(now: datetime | None = None) -> bool:
    """Пн–Пт включены; Сб и Вс полностью выключены."""
    if not getattr(cfg, "AUTOMATIC_WEEKDAYS_ONLY", True):
        return True
    active = tuple(getattr(cfg, "AUTOMATIC_ACTIVE_WEEKDAYS", (0, 1, 2, 3, 4)))
    return local_now(now).weekday() in active
