"""Where do Twelve Data H4/D1/W1 bars really start? (OBSERVE_ONLY)

Twelve Data ignores ``timezone=UTC`` for 1day/1week and labels those bars
with an exchange-local DATE (Forex default exchange zone: Australia/Sydney).
The live code (analysis.closed_candles, bot cache refresh, patterns.final_by_tf,
ohlc_history_journal) treats a D1 label ``YYYY-MM-DD`` as opening at 00:00 UTC
and a W1 label as opening at 00:00 UTC of that date.

This probe rebuilds every complete H4/D1/W1 bar from the SAME pair's H1 bars
for each candidate start offset and reports which offset reproduces the
vendor High/Low. It answers, from real data and with no API calls:

* offset 0        -> the UTC assumption is exact;
* offset < 0      -> real bar closes EARLIER than assumed: conservative lag
                     (the closed bar is ignored for |offset| hours);
* offset > 0      -> real bar closes LATER than assumed: for ``offset`` hours
                     a still-forming bar is treated as closed (look-ahead risk).

No candles are changed, no thresholds, no trading decisions, no Telegram text
other than the explicit /ohlc_timebase command.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import statistics

from market_data_coherence import opened_utc

SPAN_HOURS = {"H4": 4, "D1": 24, "W1": 168}
# Candidate start offsets (hours) relative to the code's assumed UTC open.
SEARCH_HOURS = {"H4": range(-3, 4), "D1": range(-24, 25), "W1": range(-72, 73, 4)}
MIN_MATCH_RATIO = 0.80
MIN_SAMPLES = 3


def _is_weekend_closed(ts: datetime) -> bool:
    """Saturday or Sunday before 20:00 UTC: never ordinary FX trading hours."""
    return ts.weekday() == 5 or (ts.weekday() == 6 and ts.hour < 20)


def _h1_index(h1: list) -> dict[datetime, object]:
    out = {}
    for bar in h1 or []:
        try:
            ts = opened_utc(bar)
        except (ValueError, TypeError, OverflowError, AttributeError):
            continue
        out[ts] = bar
    return out


def _tolerance(h1: list) -> float:
    """~0.2 pip on majors / 0.02 on JPY: relative 2e-5 of a typical price."""
    closes = [float(b.close) for b in (h1 or []) if math.isfinite(float(b.close))]
    if not closes:
        return 0.0
    return abs(statistics.median(closes)) * 2e-5


def infer_offset(h1: list, htf: list, tf: str, *, as_of: datetime | None = None,
                 lower_hours: int = 1) -> dict:
    """Best start offset (hours) of ``tf`` bars versus the UTC-label assumption.

    ``h1`` is the lower series used to rebuild ``tf`` (H1 by default; for W1
    the caller may pass UTC-aligned H4 with ``lower_hours=4`` because 250 H1
    contain at most one complete week).
    """
    if tf not in SPAN_HOURS:
        raise ValueError(f"unsupported TF {tf}")
    as_of = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    index = _h1_index(h1)
    step = timedelta(hours=lower_hours)
    if not index or not htf:
        return {"tf": tf, "samples": 0, "verdict": "NO_DATA"}
    tol = _tolerance(h1)
    first_h1, last_h1 = min(index), max(index)
    span = timedelta(hours=SPAN_HOURS[tf])
    scores: dict[int, list[int]] = {}
    for offset in SEARCH_HOURS[tf]:
        hit = total = 0
        for bar in htf:
            try:
                base = opened_utc(bar)
            except (ValueError, TypeError, OverflowError, AttributeError):
                continue
            start = base + timedelta(hours=offset)
            end = start + span
            # Whole window must be inside the H1 history and fully closed.
            if start < first_h1 or end > last_h1 + step or end > as_of:
                continue
            inside = [b for ts, b in index.items() if start <= ts < end]
            # Real trading slots only; weekend placeholders may be absent.
            slots = SPAN_HOURS[tf] // lower_hours
            expected = sum(1 for k in range(slots)
                           if not _is_weekend_closed(start + k * step))
            if expected == 0 or len([b for b in inside
                                     if not _is_weekend_closed(opened_utc(b))]) < expected:
                continue
            total += 1
            hi = max(float(b.high) for b in inside)
            lo = min(float(b.low) for b in inside)
            if abs(hi - float(bar.high)) <= tol and abs(lo - float(bar.low)) <= tol:
                hit += 1
        if total:
            scores[offset] = [hit, total]
    if not scores:
        return {"tf": tf, "samples": 0, "verdict": "NO_COMPLETE_WINDOWS"}
    ratio = {o: h / t for o, (h, t) in scores.items() if t >= MIN_SAMPLES}
    if not ratio:
        return {"tf": tf, "samples": max(t for _, t in scores.values()),
                "verdict": "TOO_FEW_WINDOWS"}
    best = max(ratio.values())
    best_offsets = sorted(o for o, r in ratio.items() if abs(r - best) < 1e-12)
    samples = scores[best_offsets[0]][1]
    result = {"tf": tf, "samples": samples, "match_ratio": round(best, 3),
              "best_offsets_h": best_offsets, "tolerance": tol}
    # Equal scores = the data cannot tell those starts apart (typically
    # because the difference lies in closed weekend hours). Claim a risk or a
    # lag only when EVERY equally good start agrees on the sign.
    if best < MIN_MATCH_RATIO:
        result["verdict"] = "UNRESOLVED"
    elif 0 in best_offsets:
        result["verdict"] = "UTC_ALIGNED"
    elif min(best_offsets) > 0:
        result["verdict"] = "LOOKAHEAD_RISK"
        result["forming_treated_closed_h"] = min(best_offsets)
    elif max(best_offsets) < 0:
        result["verdict"] = "CONSERVATIVE_LAG"
        result["closed_bar_ignored_h"] = -max(best_offsets)
    else:
        result["verdict"] = "AMBIGUOUS"
    return result


def weekend_h1(h1: list) -> dict:
    flat = moving = 0
    for bar in h1 or []:
        try:
            ts = opened_utc(bar)
        except (ValueError, TypeError, OverflowError, AttributeError):
            continue
        if not _is_weekend_closed(ts):
            continue
        if max(bar.open, bar.high, bar.low, bar.close) - min(bar.open, bar.high, bar.low, bar.close) <= 1e-12:
            flat += 1
        else:
            moving += 1
    return {"flat": flat, "moving": moving}


def weekend_d1(d1: list) -> dict:
    """D1 bars labelled Saturday/Sunday and their size vs an ordinary day.

    A short Sunday stub as the "previous day" would make PDH/PDL on Monday
    the 1–2 hour Sunday range instead of Friday's.
    """
    ranges, stub = [], []
    for bar in d1 or []:
        try:
            ts = opened_utc(bar)
        except (ValueError, TypeError, OverflowError, AttributeError):
            continue
        size = float(bar.high) - float(bar.low)
        (stub if ts.weekday() >= 5 else ranges).append(size)
    typical = statistics.median(ranges) if ranges else 0.0
    return {"weekend_labels": len(stub),
            "weekend_range_vs_median": (round(statistics.median(stub) / typical, 2)
                                         if stub and typical > 0 else None)}


def probe_pair(by_tf: dict, *, as_of: datetime | None = None) -> dict:
    from analysis import closed_candles
    h1 = closed_candles((by_tf or {}).get("H1") or [], 60)
    out = {"weekend_h1": weekend_h1(h1), "h1_bars": len(h1),
           "weekend_d1": weekend_d1(closed_candles((by_tf or {}).get("D1") or [], 1440))}
    h4 = closed_candles((by_tf or {}).get("H4") or [], 240)
    for tf, minutes in (("H4", 240), ("D1", 1440), ("W1", 10080)):
        bars = closed_candles((by_tf or {}).get(tf) or [], minutes)
        if tf == "W1":
            # Rebuild W1 from H4 only if H4 itself is proven UTC-aligned.
            if out["H4"].get("verdict") == "UTC_ALIGNED":
                out[tf] = infer_offset(h4, bars, tf, as_of=as_of, lower_hours=4)
                out[tf]["lower_tf"] = "H4"
            else:
                out[tf] = {"tf": tf, "samples": 0, "verdict": "H4_NOT_VERIFIED"}
        else:
            out[tf] = infer_offset(h1, bars, tf, as_of=as_of)
    return out


_VERDICT_RU = {
    "UTC_ALIGNED": "совпадает с UTC-разметкой бота",
    "CONSERVATIVE_LAG": "свеча реально закрывается РАНЬШЕ — бот осторожно опаздывает",
    "LOOKAHEAD_RISK": "свеча реально закрывается ПОЗЖЕ — формирующаяся свеча считается закрытой",
    "UNRESOLVED": "не удалось однозначно восстановить из младшего ТФ",
    "AMBIGUOUS": "несколько равных вариантов начала, знак не определён",
    "H4_NOT_VERIFIED": "не проверено: сначала нужна подтверждённая H4",
    "TOO_FEW_WINDOWS": "мало полных окон H1 для проверки",
    "NO_COMPLETE_WINDOWS": "нет полных окон H1",
    "NO_DATA": "нет данных",
}


def format_report(market: dict, pairs: list[str]) -> str:
    lines = ["🕒 OHLC: временная основа H4/D1/W1 (только наблюдение, без API-запросов)",
             "Сравнение свечей поставщика со сборкой из H1 той же пары.", ""]
    for pair in pairs:
        r = probe_pair((market or {}).get(pair) or {})
        lines.append(f"{pair} · H1 в кэше: {r['h1_bars']} · выходные H1: "
                     f"без движения {r['weekend_h1']['flat']}, с движением {r['weekend_h1']['moving']}")
        wd = r["weekend_d1"]
        if wd["weekend_labels"]:
            lines.append(f"  D1 с меткой Сб/Вс: {wd['weekend_labels']} · размер к обычному дню "
                         f"{wd['weekend_range_vs_median']} (короткая воскресная свеча искажает PDH/PDL понедельника)")
        for tf in ("H4", "D1", "W1"):
            x = r[tf]
            verdict = _VERDICT_RU.get(x.get("verdict"), x.get("verdict"))
            if "best_offsets_h" in x:
                offs = ",".join(f"{o:+d}" for o in x["best_offsets_h"])
                lines.append(f"  {tf}: сдвиг начала {offs} ч · совпало {x['match_ratio']*100:.0f}% "
                             f"из {x['samples']} · {verdict}")
            else:
                lines.append(f"  {tf}: {verdict}")
    lines += ["", "Сдвиг 0 — правило «метка = открытие в UTC» верно. Минус — бот видит закрытую "
              "свечу позже (безопасно). Плюс — риск: бот считает свечу закрытой раньше времени."]
    return "\n".join(lines)
