"""Экономический календарь для сессионного брифинга.

Источник — недельный JSON Forex Factory, без API-ключа.
Twelve Data здесь не используется: у него нет нормального FX-календаря.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests

import config as cfg

log = logging.getLogger("fxbot.news")
LOCAL_TZ = ZoneInfo("Europe/Amsterdam")

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_CALENDAR_STATUS = "unavailable"  # live / cache / unavailable

COUNTRY_TO_CCY = {
    "US": "USD", "USA": "USD", "UNITED STATES": "USD", "USD": "USD",
    "EU": "EUR", "EMU": "EUR", "EZ": "EUR", "EUR": "EUR",
    "DE": "EUR", "GERMANY": "EUR", "FR": "EUR", "IT": "EUR", "ES": "EUR",
    "GB": "GBP", "UK": "GBP", "UNITED KINGDOM": "GBP", "GBP": "GBP",
    "JP": "JPY", "JAPAN": "JPY", "JPY": "JPY",
    "CH": "CHF", "SWITZERLAND": "CHF", "CHF": "CHF",
    "AU": "AUD", "AUSTRALIA": "AUD", "AUD": "AUD",
    "NZ": "NZD", "NEW ZEALAND": "NZD", "NZD": "NZD",
    "CA": "CAD", "CANADA": "CAD", "CAD": "CAD",
}

# higher_is_positive: Actual выше Forecast обычно плюс для валюты.
# higher_is_negative: Actual выше Forecast обычно минус (безработица, заявки).
HIGHER_NEGATIVE = (
    "unemployment", "jobless", "claims", "безработиц",
)
HIGHER_POSITIVE = (
    "payroll", "nfp", "employment change", "nonfarm", "gdp",
    "cpi", "inflation", "ppi", "retail", "pmi", "ism",
    "confidence", "sentiment", "production", "manufactur",
    "building permit", "housing start", "home sales",
    "trade balance", "wage", "average hourly", "interest rate",
    "cash rate", "bank rate", "refi",
)
CONTEXT_DEPENDENT = (
    "speech", "speaks", "fomc", "minutes", "statement", "testimony",
    "press conference", "decision", "auction",
)
SPEECH_MARKERS = ("speech", "speaks", "testimony", "press conference")

TITLE_RU = (
    ("nonfarm payrolls", "Занятость вне сельского хозяйства (NFP)"),
    ("non-farm payrolls", "Занятость вне сельского хозяйства (NFP)"),
    ("unemployment rate", "Уровень безработицы"),
    ("initial jobless claims", "Первичные заявки на пособие по безработице"),
    ("continuing jobless claims", "Повторные заявки на пособие по безработице"),
    ("average hourly earnings", "Средняя почасовая оплата"),
    ("adp nonfarm", "Занятость ADP"),
    ("adp employment", "Занятость ADP"),
    ("interest rate decision", "Решение по процентной ставке"),
    ("cash rate", "Процентная ставка"),
    ("federal funds rate", "Ставка ФРС"),
    ("fomc statement", "Заявление FOMC"),
    ("fomc minutes", "Протокол FOMC"),
    ("fomc press conference", "Пресс-конференция FOMC"),
    ("cpi", "Индекс потребительских цен (CPI)"),
    ("core cpi", "Базовый CPI"),
    ("ppi", "Индекс цен производителей (PPI)"),
    ("core ppi", "Базовый PPI"),
    ("gdp", "ВВП"),
    ("retail sales", "Розничные продажи"),
    ("core retail sales", "Базовые розничные продажи"),
    ("ism manufacturing", "ISM обрабатывающая промышленность"),
    ("ism services", "ISM сфера услуг"),
    ("pmi", "PMI"),
    ("trade balance", "Торговый баланс"),
    ("building permits", "Разрешения на строительство"),
    ("housing starts", "Закладки домов"),
    ("existing home sales", "Продажи жилья на вторичном рынке"),
    ("new home sales", "Продажи нового жилья"),
    ("consumer confidence", "Потребительская уверенность"),
    ("consumer sentiment", "Потребительские настроения"),
    ("durable goods", "Заказы на товары длительного пользования"),
    ("industrial production", "Промышленное производство"),
    ("crude oil inventories", "Запасы нефти"),
    ("jolt", "Открытые вакансии JOLTS"),
    ("michigan", "Индекс Мичигана"),
)


def translate_title(title: str) -> str:
    raw = (title or "").strip()
    if not raw:
        return raw
    low = raw.lower()
    for en, ru in TITLE_RU:
        if en in low:
            rest = raw
            return ru
    return raw


@dataclass
class NewsEvent:
    event_id: str
    title: str
    currency: str
    impact: str  # HIGH / MEDIUM / LOW
    dt_utc: datetime
    previous: str
    forecast: str
    actual: str
    economic_effect: str  # higher_is_positive / higher_is_negative / context_dependent

    @property
    def local(self) -> datetime:
        return self.dt_utc.astimezone(LOCAL_TZ)

    @property
    def local_hm(self) -> str:
        return self.local.strftime("%H:%M")


def _cache_path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "news_calendar_cache.json"


def _event_to_dict(event: NewsEvent) -> dict:
    return {
        "event_id": event.event_id,
        "title": event.title,
        "currency": event.currency,
        "impact": event.impact,
        "dt_utc": event.dt_utc.astimezone(timezone.utc).isoformat(),
        "previous": event.previous,
        "forecast": event.forecast,
        "actual": event.actual,
        "economic_effect": event.economic_effect,
    }


def _save_cache(events: list[NewsEvent]) -> None:
    if not events:
        return
    dest = _cache_path()
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "saved_ts": time.time(),
            "events": [_event_to_dict(event) for event in events],
        }, ensure_ascii=False, indent=2))
        tmp.replace(dest)
    except OSError:
        log.exception("Не удалось сохранить резервный календарь")


def _load_cache() -> list[NewsEvent]:
    try:
        data = json.loads(_cache_path().read_text())
        age_hours = (time.time() - float(data.get("saved_ts") or 0)) / 3600
        max_age = float(getattr(cfg, "NEWS_CACHE_MAX_AGE_HOURS", 168))
        if age_hours < 0 or age_hours > max_age:
            return []
        events = []
        for raw in data.get("events") or []:
            dt = _parse_dt(raw.get("dt_utc"))
            if not dt:
                continue
            events.append(NewsEvent(
                event_id=str(raw.get("event_id") or event_id_of(raw.get("title", ""), raw.get("currency", ""), dt)),
                title=str(raw.get("title") or ""),
                currency=str(raw.get("currency") or ""),
                impact=str(raw.get("impact") or "LOW"),
                dt_utc=dt,
                previous=str(raw.get("previous") or "—"),
                forecast=str(raw.get("forecast") or "—"),
                actual=str(raw.get("actual") or "—"),
                economic_effect=str(raw.get("economic_effect") or classify_effect(raw.get("title", ""))),
            ))
        return events
    except (FileNotFoundError, ValueError, TypeError, OSError):
        return []


def calendar_status() -> str:
    return _CALENDAR_STATUS


def event_id_of(title: str, currency: str, dt_utc: datetime) -> str:
    raw = f"{dt_utc.strftime('%Y-%m-%dT%H:%M')}|{currency}|{title.strip().lower()}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def map_currency(raw: str) -> str:
    key = (raw or "").strip().upper()
    return COUNTRY_TO_CCY.get(key, key if key in cfg.CURRENCIES else "")


def map_impact(raw: str) -> str:
    t = (raw or "").strip().lower()
    if t in ("high", "3", "red", "holiday"):
        if t == "holiday":
            return "LOW"
        return "HIGH"
    if t in ("medium", "2", "orange"):
        return "MEDIUM"
    return "LOW"


def classify_effect(title: str) -> str:
    t = (title or "").lower()
    if any(k in t for k in CONTEXT_DEPENDENT) and not any(k in t for k in HIGHER_POSITIVE + HIGHER_NEGATIVE):
        return "context_dependent"
    if any(k in t for k in HIGHER_NEGATIVE):
        return "higher_is_negative"
    if any(k in t for k in HIGHER_POSITIVE):
        return "higher_is_positive"
    return "context_dependent"


def is_speech_event(event: NewsEvent) -> bool:
    title = (event.title or "").lower()
    return any(marker in title for marker in SPEECH_MARKERS)


def _parse_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _num(text: str) -> Optional[float]:
    if text is None:
        return None
    s = str(text).strip().replace(",", "").replace("%", "").replace("K", "").replace("M", "")
    s = s.replace("B", "")
    if s in ("", "-", "—", "n/a", "NA", "None"):
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def pairs_touched(currency: str) -> list[str]:
    c = (currency or "").upper()
    out = []
    for p in cfg.PAIRS:
        base, quote = p.split("/")
        if c in (base, quote):
            out.append(p)
    return out


def fetch_ff() -> list[NewsEvent]:
    r = requests.get(FF_URL, timeout=25)
    r.raise_for_status()
    data = r.json()
    events: list[NewsEvent] = []
    if not isinstance(data, list):
        return events
    for row in data:
        if not isinstance(row, dict):
            continue
        ccy = map_currency(row.get("country") or row.get("currency") or "")
        if ccy not in cfg.CURRENCIES:
            continue
        dt = _parse_dt(row.get("date") or row.get("datetime"))
        if not dt:
            continue
        title = str(row.get("title") or row.get("event") or "").strip()
        if not title:
            continue
        events.append(
            NewsEvent(
                event_id=event_id_of(title, ccy, dt),
                title=title,
                currency=ccy,
                impact=map_impact(row.get("impact")),
                dt_utc=dt,
                previous=str(row.get("previous") or "—"),
                forecast=str(row.get("forecast") or "—"),
                actual=str(row.get("actual") or "—"),
                economic_effect=classify_effect(title),
            )
        )
    return events


def load_events() -> list[NewsEvent]:
    global _CALENDAR_STATUS
    try:
        events = fetch_ff()
        if events:
            _save_cache(events)
            _CALENDAR_STATUS = "live"
            return events
        raise RuntimeError("источник вернул пустой календарь")
    except Exception as e:
        log.warning("Forex Factory календарь: %s", e)
        cached = _load_cache()
        if cached:
            _CALENDAR_STATUS = "cache"
            log.info("Использован сохранённый экономический календарь: %s событий", len(cached))
            return cached
        _CALENDAR_STATUS = "unavailable"
        return []


def high_events(events: list[NewsEvent]) -> list[NewsEvent]:
    return [e for e in events if e.impact == "HIGH"]


def events_in_window(events: list[NewsEvent], start: datetime, end: datetime) -> list[NewsEvent]:
    out = []
    for e in events:
        if start <= e.dt_utc < end:
            out.append(e)
    out.sort(key=lambda x: x.dt_utc)
    return out


def minutes_left(event: NewsEvent, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    return int((event.dt_utc - now).total_seconds() // 60)


def has_actual(event: NewsEvent) -> bool:
    a = (event.actual or "").strip()
    return a not in ("", "-", "—", "n/a", "NA", "None")


def interpret_print(event: NewsEvent) -> Optional[str]:
    """
    Возвращает 'positive' / 'negative' / None.
    None = нет цифр, смысл неопределён или отклонение мелкое.
    """
    if event.economic_effect == "context_dependent":
        return None
    act = _num(event.actual)
    fc = _num(event.forecast)
    if act is None or fc is None:
        return None
    if fc == 0:
        diff = abs(act)
        rel = diff
    else:
        diff = act - fc
        rel = abs(diff / fc)
    # Слишком маленькое отклонение не комментируем направленно.
    if abs(diff) < 1e-9 or rel < 0.04:
        return None
    better = diff > 0
    if event.economic_effect == "higher_is_negative":
        better = not better
    return "positive" if better else "negative"


def scenario_before(event: NewsEvent, ccy_score: float) -> str:
    if is_speech_event(event):
        return (
            f"Для выступления нет числового Actual. Жёсткий тон комментариев может усилить {event.currency}; "
            f"мягкий тон — ослабить. До подтверждённой реакции цены направление не утверждаем."
        )
    if event.economic_effect == "context_dependent":
        return (
            f"У события нет однозначного числового сценария. Возможна повышенная волатильность {event.currency}; "
            f"направление определяем только после подтверждённой реакции цены."
        )
    weak = ccy_score <= -0.03
    strong = ccy_score >= 0.03
    current = (
        f"{event.currency} сейчас слабая. " if weak else
        f"{event.currency} сейчас сильная. " if strong else
        f"{event.currency} в середине рейтинга. "
    )
    if event.economic_effect == "higher_is_negative":
        scenario = (
            f"Факт выше прогноза — отрицательно для {event.currency}; "
            f"факт ниже прогноза — положительно для {event.currency}."
        )
    else:
        scenario = (
            f"Факт выше прогноза — положительно для {event.currency}; "
            f"факт ниже прогноза — отрицательно для {event.currency}."
        )
    return current + scenario + " Направление подтверждаем только после публикации и реакции цены."


def pair_pressure(currency: str, usd_positive: bool) -> list[str]:
    """Давление на пары, если валюта усиливается (usd_positive=True) или ослабевает."""
    lines = []
    for pair in pairs_touched(currency):
        base, quote = pair.split("/")
        if usd_positive:
            if base == currency:
                lines.append(f"{pair} ↑")
            elif quote == currency:
                lines.append(f"{pair} ↓")
        else:
            if base == currency:
                lines.append(f"{pair} ↓")
            elif quote == currency:
                lines.append(f"{pair} ↑")
    return lines
