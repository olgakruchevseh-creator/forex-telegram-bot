"""Independent W1/D1/H4/H1 FX basket strength snapshot for signal audits.

Observation only: NEVER decide whether an alert may be delivered. All numbers
are centered USD-star *log-returns*, in percentage points, NOT probabilities.
Input is the existing seven USD majors: no extra data/API requests.
All symbols and timeframes must be aligned and fully closed at ``as_of``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

import config as cfg

TF_SPEC = {
    "W1": (10080, 4, 12 * 24 * 60, 0.12),
    "D1": (1440, 5, 4 * 24 * 60, 0.08),
    "H4": (240, 6, 12 * 60, 0.05),
    "H1": (60, 4, 3 * 60, 0.03),
}
TF_ORDER = ("W1", "D1", "H4", "H1")
SAMPLES = 3  # last 3 closed endpoints, not mutable in-progress data


def _utc(value: object) -> datetime:
    """Twelve Data configured timezone UTC; a naive stamp is UTC bar OPEN."""
    if isinstance(value, datetime):
        d = value
    else:
        d = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00").replace(" ", "T"))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _closed(raw: list, minutes: int, as_of: datetime) -> list[tuple[datetime, float]]:
    result = []
    previous = None
    for candle in raw:
        opened = _utc(candle.dt)
        if previous is not None and opened <= previous:
            raise ValueError("дублирующиеся или несортированные свечи")
        previous = opened
        if opened + timedelta(minutes=minutes) <= as_of:
            close = float(candle.close)
            if not math.isfinite(close) or close <= 0:
                raise ValueError("нечисловая или неположительная цена закрытой свечи")
            result.append((opened, close))
    return result


def _unavailable(reason: str) -> dict:
    return {"status": "НЕТ ДАННЫХ", "reason": reason}


def _tf(market: dict, tf: str, as_of: datetime) -> dict:
    minutes, lookback, max_age, epsilon = TF_SPEC[tf]
    count = lookback + SAMPLES  # 4+3 bars means 3 lookback observations
    window = {}
    reference = None
    pairs = tuple(cfg.PAIRS)
    currency_names = tuple(cfg.CURRENCIES)
    if len(pairs) != 7 or len(set(currency_names)) != 8 or "USD" not in currency_names:
        return _unavailable("корзина не содержит семь USD-пар и восемь валют")
    for pair in pairs:
        try:
            base, quote = pair.split("/")
            if (base == "USD") == (quote == "USD"):
                raise ValueError("не USD-пара")
            raw = ((market or {}).get(pair) or {}).get(tf) or []
            if not raw:
                return _unavailable(f"{pair}: нет {tf}")
            bars = _closed(raw, minutes, as_of)
            if len(bars) < count:
                return _unavailable(f"{pair}: недостаточно закрытых {tf} ({len(bars)}/{count})")
            tail = bars[-count:]
            stamps = tuple(x[0] for x in tail)
            if reference is not None and reference != stamps:
                return _unavailable(f"{pair}: несинхронные закрытые {tf}")
            reference = stamps
            window[pair] = tail
        except (AttributeError, ValueError, TypeError, OverflowError, ArithmeticError) as err:
            return _unavailable(f"{pair}: {err}")
    if not reference:
        return _unavailable("нет данных")
    closed_at = reference[-1] + timedelta(minutes=minutes)
    age_min = (as_of - closed_at).total_seconds() / 60
    if age_min < -0.001 or age_min > max_age:
        return _unavailable(f"{tf}: последняя свеча устарела ({age_min:.0f} минут)")
    # Zero-sum solution S[C] - S[USD] = r(C/USD). All seven edges are
    # contemporaneous; this is an exact algebraic identity, not a forecast.
    stages: list[dict[str, float]] = []
    for endpoint_offset in (2, 1, 0):
        rel = {}
        for pair, rows in window.items():
            base, quote = pair.split("/")
            recent = rows[-1 - endpoint_offset][1]
            old = rows[-1 - endpoint_offset - lookback][1]
            rate = 100.0 * math.log(recent / old)
            rel[quote if base == "USD" else base] = -rate if base == "USD" else rate
        if len(rel) != 7:
            return _unavailable("повторяющиеся валюты в USD-корзине")
        usd = -sum(rel.values()) / 8.0
        stages.append({c: (usd if c == "USD" else usd + rel[c]) for c in currency_names})
    if not all(math.isfinite(x) for stage in stages for x in stage.values()):
        return _unavailable("переполнение расчёта")
    latest = stages[-1]
    return {
        "status": "OK", "closed_at_utc": closed_at.isoformat(),
        "age_min": round(age_min, 1),
        "score_log_pp": {c: round(v, 6) for c, v in latest.items()},
        "rank": [c for c in sorted(currency_names, key=lambda c: (-latest[c], c))],
        "_stage_scores": stages,
        "_epsilon": epsilon,
    }


def signal_h1_close(market: dict, symbol: str, now: datetime | None = None) -> datetime | None:
    """Anchor the audit to the current last *closed* H1, not a later scan time.

    The exact signal hour matters for replay: a D1/H4 candle closing after the
    signal must never accidentally be included in an older decision snapshot.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        raw = ((market or {}).get(symbol) or {}).get("H1") or []
        for bar in reversed(raw):
            end = _utc(bar.dt) + timedelta(hours=1)
            if end <= now:
                return end
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None
    return None


def inspect(market: dict, symbol: str, side: str, as_of: datetime | None = None) -> dict:
    """Four independently measured TFs, observation-only, never an entry veto."""
    if side not in ("LONG", "SHORT") or symbol not in cfg.PAIRS:
        return {"status": "НЕТ ДАННЫХ", "reason": "неизвестная пара или направление", "decision_authority": False}
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        return {"status": "НЕТ ДАННЫХ", "reason": "as_of без UTC", "decision_authority": False}
    as_of = as_of.astimezone(timezone.utc)
    base, quote = symbol.split("/")
    sign = 1 if side == "LONG" else -1
    results = {}
    aligned = opposed = valid = stable = 0
    for tf in TF_ORDER:
        r = _tf(market, tf, as_of)
        if r["status"] != "OK":
            results[tf] = r
            continue
        gaps = [stage[base] - stage[quote] for stage in r.pop("_stage_scores")]
        epsilon = r.pop("_epsilon")
        directed = gaps[-1] * sign
        if directed > epsilon:
            assessment = "ПОДДЕРЖИВАЕТ"
            aligned += 1
        elif directed < -epsilon:
            assessment = "ПРОТИВ"
            opposed += 1
        else:
            assessment = "НЕЙТРАЛЬНО"
        persistence = sum(1 for g in gaps if g * sign > epsilon)
        if persistence == len(gaps) and assessment == "ПОДДЕРЖИВАЕТ":
            stable += 1
        valid += 1
        r.update({
            "pair_gap_log_pp": round(gaps[-1], 6),
            "directed_gap_log_pp": round(directed, 6),
            "last_three_pair_gaps_log_pp": [round(g, 6) for g in gaps],
            "persistence_support_count": persistence,
            "assessment": assessment,
        })
        results[tf] = r
    overall = ("НЕТ ДАННЫХ" if not valid else
               "ПРЕИМУЩЕСТВО В НАПРАВЛЕНИИ" if aligned >= 3 and opposed == 0 else
               "КОНФЛИКТ" if opposed >= 2 else "СМЕШАННЫЙ ФОН")
    return {
        "status": overall, "symbol": symbol, "side": side,
        "snapshot_utc": as_of.isoformat(),
        "source": "Twelve Data / 7 USD-мажоров, закрытые свечи",
        "method": "лог-доходность; zero-sum USD-star; без прогнозной вероятности",
        "decision_authority": False, "can_block_signal": False,
        "available_tf": valid, "aligned_tf": aligned, "opposed_tf": opposed,
        "stable_support_tf": stable, "by_tf": results,
    }


def short_note(report: dict) -> str:
    """Single compact line in a delivered card; neutral on missing data."""
    parts = []
    for tf in TF_ORDER:
        r = (report.get("by_tf") or {}).get(tf) or {}
        symbol = {"ПОДДЕРЖИВАЕТ": "+", "ПРОТИВ": "−", "НЕЙТРАЛЬНО": "="}.get(r.get("assessment"), "?")
        parts.append(f"{tf}{symbol}")
    return "Сила валют W1/D1/H4/H1: " + " · ".join(parts) + " (наблюдение, не запрет)"
