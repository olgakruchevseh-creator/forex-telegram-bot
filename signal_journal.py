"""Журнал фактически отправленных торговых сигналов и сводные отчёты."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import config as cfg
from analysis import Candle, atr, closed_candles

log = logging.getLogger("fxbot.signal_journal")


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "signal_journal_state.json"


def _load() -> dict:
    try:
        value = json.loads(_path().read_text())
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save(value: dict) -> None:
    dest = _path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(dest)


def _h1(by_tf: dict) -> list[Candle]:
    return closed_candles(by_tf.get("H1") or [], 60)


def _pair(text: str) -> str:
    match = re.search(r"(?:Пара:\s*|(?:LONG|SHORT)\s+)([A-Z]{3}/[A-Z]{3})", text or "")
    return match.group(1) if match else ""


def _side(text: str) -> str:
    match = re.search(r"Направление(?: реакции)?:\s*(LONG|SHORT)\b", text or "")
    if not match:
        match = re.search(r"(?:^|\n)[🟢🔴]?\s*(LONG|SHORT)\s+[A-Z]{3}/[A-Z]{3}", text or "")
    return match.group(1) if match else ""


def _number(text: str, label: str) -> int | None:
    match = re.search(rf"{re.escape(label)}:\s*(\d+)(?:/100|%)", text or "")
    return int(match.group(1)) if match else None


def _source(text: str) -> str:
    upper = (text or "").upper()
    match = re.search(r"Источники модулей:\s*([^\n]+)", text or "", re.I)
    if match:
        return match.group(1).split("·", 1)[0].strip()
    if "ПОДТВЕРЖДЁННЫЙ НАВИГАТОР" in upper:
        return "Confirmed Navigator"
    names = (
        ("MASTER DIRECTION", "Master Direction"), ("ПАТТЕРН", "Patterns"),
        ("CHAIN ENTRY", "Chain Entries"), ("СТРУКТУРНЫЙ РЕТЕСТ", "Retest"),
        ("ORDER BLOCK", "Order Block"), ("СНЯТИЕ ЛИКВИДНОСТИ", "Liquidity Sweep"),
        ("ДИСБАЛАНС", "Disbalance"), ("ИМБАЛАНС", "Imbalance/FVG"),
        ("ФИБОНАЧЧИ", "Fibonacci"), ("НАКОПЛЕНИ", "Accumulation/Distribution"),
        ("РАСПРЕДЕЛЕНИ", "Accumulation/Distribution"), ("ДНЕВН", "Daily High/Low"),
        ("ПРОБОЙ УРОВНЯ", "Levels"), ("ОТБОЙ ОТ", "Levels"),
        ("ЛОЖНЫЙ ПРОБОЙ", "Levels"), ("УДЕРЖАНИЕ", "Levels"),
        ("ZIGZAG", "ZigZag"),
    )
    return next((name for marker, name in names if marker in upper), "Другой модуль")


def _parse_dt(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _local_date(raw: str) -> str:
    dt = _parse_dt(raw)
    if not dt:
        return ""
    return dt.astimezone(ZoneInfo(getattr(cfg, "LOCAL_TZ_NAME", "Europe/Amsterdam"))).date().isoformat()


def record_sent(text: str, market: dict, closed_h1_dt: str) -> bool:
    """Записывает только уже успешно отправленное пользователю сообщение."""
    # Это информационное сообщение об удалении старого уровня, а не новая сделка.
    upper = (text or "").upper()
    if "УРОВЕНЬ НЕДЕЙСТВИТЕЛЕН" in upper or (
        "НАВИГАТОР" in upper and "ПОДТВЕРЖДЁННЫЙ НАВИГАТОР" not in upper
    ) or "ПРОГРЕСС ДВИЖЕНИЯ" in upper:
        return False
    symbol, side = _pair(text), _side(text)
    bars = _h1(market.get(symbol) or {}) if symbol else []
    if not symbol or not side or not bars:
        return False
    current = bars[-1]
    entry_dt = closed_h1_dt or current.dt
    av = atr(bars, int(getattr(cfg, "ATR_PERIOD", 14)))
    if av <= 0:
        return False
    digest = hashlib.sha256(f"{symbol}|{side}|{entry_dt}|{_source(text)}|{text}".encode()).hexdigest()[:20]
    state = _load()
    records = state.setdefault("records", {})
    if digest in records:
        return False
    wanted = 1 if side == "LONG" else -1
    target_atr = float(getattr(cfg, "JOURNAL_TARGET_ATR", 1.0))
    invalid_atr = float(getattr(cfg, "JOURNAL_INVALIDATION_ATR", 0.75))
    records[digest] = {
        "id": digest, "symbol": symbol, "side": side, "source": _source(text),
        "entry_dt": entry_dt, "local_date": _local_date(entry_dt),
        "entry": current.close, "atr": av,
        "target": current.close + wanted * av * target_atr,
        "invalidation": current.close - wanted * av * invalid_atr,
        "quality": _number(text, "Качество"), "confidence": _number(text, "Вероятность"),
        "status": "OPEN", "resolved_dt": "", "horizons": {},
        "mfe_atr": 0.0, "mae_atr": 0.0,
    }
    _save(state)
    return True


def _future_bars(record: dict, bars: list[Candle]) -> list[Candle]:
    return [bar for bar in bars if bar.dt > record.get("entry_dt", "")]


def _update_record(record: dict, bars: list[Candle]) -> None:
    future = _future_bars(record, bars)
    if not future:
        return
    wanted = 1 if record["side"] == "LONG" else -1
    entry, av = float(record["entry"]), float(record["atr"])
    favorable = max(((bar.high - entry) * wanted if wanted > 0 else (entry - bar.low)) for bar in future)
    adverse = max(((entry - bar.low) if wanted > 0 else (bar.high - entry)) for bar in future)
    record["mfe_atr"] = round(max(float(record.get("mfe_atr") or 0), favorable / av), 3)
    record["mae_atr"] = round(max(float(record.get("mae_atr") or 0), adverse / av), 3)
    for hours in (1, 4, 8):
        key = str(hours)
        if key not in record.setdefault("horizons", {}) and len(future) >= hours:
            move = (future[hours - 1].close - entry) * wanted / av
            record["horizons"][key] = round(move, 3)

    if record.get("status") == "OPEN":
        for bar in future:
            target_hit = bar.high >= record["target"] if wanted > 0 else bar.low <= record["target"]
            invalid_hit = bar.low <= record["invalidation"] if wanted > 0 else bar.high >= record["invalidation"]
            if target_hit and invalid_hit:
                record["status"], record["resolved_dt"] = "AMBIGUOUS", bar.dt
                break
            if target_hit:
                record["status"], record["resolved_dt"] = "TARGET", bar.dt
                break
            if invalid_hit:
                record["status"], record["resolved_dt"] = "INVALIDATED", bar.dt
                break
        if record.get("status") == "OPEN" and "8" in record.get("horizons", {}):
            record["status"] = "CLOSED_8H"
            record["resolved_dt"] = future[7].dt


def update_market(market: dict) -> None:
    state = _load()
    changed = False
    for record in (state.get("records") or {}).values():
        bars = _h1(market.get(record.get("symbol")) or {})
        before = json.dumps(record, sort_keys=True)
        if bars:
            _update_record(record, bars)
        changed = changed or before != json.dumps(record, sort_keys=True)
    if changed:
        _save(state)


def _summary(records: list[dict], title: str) -> str:
    targets = sum(r.get("status") == "TARGET" for r in records)
    invalid = sum(r.get("status") == "INVALIDATED" for r in records)
    ambiguous = sum(r.get("status") == "AMBIGUOUS" for r in records)
    active = sum(r.get("status") == "OPEN" for r in records)
    timed = sum(r.get("status") == "CLOSED_8H" for r in records)
    resolved = targets + invalid
    success = round(targets / resolved * 100) if resolved else 0
    avg_mfe = sum(float(r.get("mfe_atr") or 0) for r in records) / len(records) if records else 0
    avg_mae = sum(float(r.get("mae_atr") or 0) for r in records) / len(records) if records else 0
    sources: dict[str, list[int]] = {}
    pairs: dict[str, list[int]] = {}
    for r in records:
        score = 1 if r.get("status") == "TARGET" else (-1 if r.get("status") == "INVALIDATED" else 0)
        if score:
            sources.setdefault(r.get("source", "Другой модуль"), []).append(score)
            pairs.setdefault(r.get("symbol", "—"), []).append(score)
    best_source = max(sources, key=lambda key: (sum(sources[key]), len(sources[key]))) if sources else "—"
    best_pair = max(pairs, key=lambda key: (sum(pairs[key]), len(pairs[key]))) if pairs else "—"
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", title, "━━━━━━━━━━━━━━━━━━", "",
        f"Записано сигналов: {len(records)}", f"Цель достигнута: {targets}",
        f"Сценарий отменён: {invalid}", f"Остались в работе: {active}",
        f"Завершены по 8 часам: {timed}", f"Неоднозначные H1: {ambiguous}",
        f"Результативность завершённых: {success}%" if resolved else "Результативность завершённых: пока нет данных",
        f"Среднее движение в плюс: {avg_mfe:.2f} ATR",
        f"Среднее движение против: {avg_mae:.2f} ATR",
        f"Лучшая пара по завершённым: {best_pair}",
        f"Лучший модуль по завершённым: {best_source}", "",
        "Примечание: проценты рассчитаны только по уже завершённым сценариям; открытые и неоднозначные в результативность не включены.",
    ])


def _previous_trading_day(day):
    """Предыдущий будний день: в понедельник возвращает пятницу."""
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def pending_reports(now_utc: datetime | None = None) -> list[tuple[str, str]]:
    state = _load()
    now = (now_utc or datetime.now(timezone.utc)).astimezone(
        ZoneInfo(getattr(cfg, "LOCAL_TZ_NAME", "Europe/Amsterdam"))
    )
    records = list((state.get("records") or {}).values())
    reports = []
    # Утром оцениваем предыдущий торговый день: поздним сигналам уже доступны
    # закрытые H1 для восьмичасового итога. Это окончательный, а не промежуточный отчёт.
    daily_h, daily_m = getattr(cfg, "JOURNAL_DAILY_REPORT_HM", (9, 10))
    day_key = now.date().isoformat()
    report_day = _previous_trading_day(now.date()).isoformat()
    if (now.weekday() < 5 and (now.hour, now.minute) >= (daily_h, daily_m)
            and state.get("last_daily_final_report") != report_day):
        selected = [r for r in records if r.get("local_date") == report_day]
        reports.append((
            f"daily_final:{report_day}",
            _summary(selected, f"📒 ОКОНЧАТЕЛЬНЫЙ ДНЕВНОЙ ЖУРНАЛ — {report_day}"),
        ))
    week_h, week_m = getattr(cfg, "JOURNAL_WEEKLY_REPORT_HM", (22, 30))
    iso = now.isocalendar()
    week_key = f"{iso.year}-W{iso.week:02d}"
    if now.weekday() == 4 and (now.hour, now.minute) >= (week_h, week_m) and state.get("last_weekly_report") != week_key:
        monday = now.date().fromisocalendar(iso.year, iso.week, 1).isoformat()
        selected = [r for r in records if monday <= r.get("local_date", "") <= day_key]
        reports.append((f"weekly:{week_key}", _summary(selected, f"📚 НЕДЕЛЬНЫЙ ЖУРНАЛ — {week_key}")))
    return reports


def mark_report_sent(report_id: str) -> None:
    state = _load()
    kind, key = report_id.split(":", 1)
    if kind in ("daily", "daily_final"):
        state["last_daily_final_report"] = key
    else:
        state["last_weekly_report"] = key
    _save(state)
