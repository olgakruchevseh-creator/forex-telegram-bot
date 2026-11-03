"""Verified, session-briefing-only FX strength, independent of trading gates.

Source: seven USD majors already retrieved from Twelve Data (H1, UTC).
No new API requests or changes to canonical currency_strength() are made here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
import math
from zoneinfo import ZoneInfo

import config as cfg
from analysis import Candle, closed_candles, rank_currencies


# These are design weights (not empirically optimised or verified probabilities).
HORIZONS = ((1, 0.15), (4, 0.35), (12, 0.30), (24, 0.20))
NEEDED_BARS = max(n for n, _ in HORIZONS) + 2  # current and previous H1, 24-bar horizon
log = logging.getLogger("fxbot.briefing.strength")


@dataclass
class StrengthCheck:
    ok: bool = False
    reason: str = ""
    last_closed_at: datetime | None = None
    age_minutes: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)
    changes: dict[str, float] = field(default_factory=dict)
    arrows: dict[str, str] = field(default_factory=dict)
    source_returns: dict[str, dict[str, float]] = field(default_factory=dict)
    source_log_returns: dict[str, dict[str, float]] = field(default_factory=dict)
    source_prices: dict[str, dict[str, float]] = field(default_factory=dict)


def _utc_opened(value: str) -> datetime | None:
    try:
        return datetime.strptime(str(value).strip().replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _blend(series: dict[str, list[Candle]]) -> dict[str, float]:
    """Exact star-basket solution using invertible log returns.

    For each non-USD currency C, calculate the return of C/USD. An USD/C
    quoted instrument must have its log-return sign reversed. Then solve
    S[C] - S[USD] = weighted_log_return(C/USD) with sum(S) = 0. This is
    the unique zero-centred solution for the 7 edges in this USD star.
    """
    relative: dict[str, float] = {}
    for pair in cfg.PAIRS:
        base, quote = pair.split("/")
        if (base == "USD") == (quote == "USD"):
            raise ValueError(f"not an USD-cross in briefing strength: {pair}")
        other = quote if base == "USD" else base
        bars = series[pair]
        weighted_log_return = sum(
            weight * 100.0 * math.log(float(bars[-1].close) / float(bars[-1-lookback].close))
            for lookback, weight in HORIZONS
        )
        relative[other] = -weighted_log_return if base == "USD" else weighted_log_return
    if len(relative) != len(cfg.CURRENCIES) - 1:
        raise ValueError("USD basket does not contain seven distinct other currencies")
    usd = -sum(relative.values()) / len(cfg.CURRENCIES)
    return {c: (usd if c == "USD" else usd + relative[c]) for c in cfg.CURRENCIES}


def calculate(market: dict, as_of: datetime | None = None) -> StrengthCheck:
    """Fail closed on absent, misaligned, damaged or outdated H1 basket.

    `as_of` is only for reproducible tests; production uses the UTC clock.
    H1 candle timestamps represent BAR OPEN times from Twelve Data (timezone=UTC).
    """
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        return StrengthCheck(reason="внутреннее время без часового пояса")
    as_of = as_of.astimezone(timezone.utc)
    window: dict[str, list[Candle]] = {}
    reference: tuple[str, ...] | None = None
    for pair in cfg.PAIRS:
        raw = ((market or {}).get(pair) or {}).get("H1") or []
        bars = closed_candles(raw)
        if len(bars) < NEEDED_BARS:
            return StrengthCheck(reason=f"{pair}: только {len(bars)} закрытых H1, необходимо {NEEDED_BARS}")
        recent = bars[-NEEDED_BARS:]
        stamps = tuple(b.dt for b in recent)
        if len(set(stamps)) != NEEDED_BARS or any(_utc_opened(s) is None for s in stamps):
            return StrengthCheck(reason=f"{pair}: некорректные или повторяющиеся метки UTC")
        try:
            prices_ok = all(math.isfinite(float(b.close)) and float(b.close) > 0 for b in recent)
        except (TypeError, ValueError, OverflowError):
            prices_ok = False
        if not prices_ok:
            return StrengthCheck(reason=f"{pair}: некорректные цены закрытия H1")
        if reference is None:
            reference = stamps
        elif stamps != reference:
            return StrengthCheck(reason=f"несинхронные H1: {pair} отличается от общей корзины")
        window[pair] = recent

    if not reference:
        return StrengthCheck(reason="нет валютных пар")
    opened = _utc_opened(reference[-1])
    if opened is None:
        return StrengthCheck(reason="нет метки последней H1")
    closed_at = opened + timedelta(hours=1)
    age_minutes = (as_of - closed_at).total_seconds() / 60.0
    max_age = float(getattr(cfg, "BRIEFING_STRENGTH_MAX_CLOSED_H1_AGE_MINUTES", 45))
    if not math.isfinite(age_minutes) or age_minutes < -0.25:
        return StrengthCheck(reason="последняя H1 ещё не закрылась по UTC", last_closed_at=closed_at)
    if age_minutes > max_age:
        return StrengthCheck(
            reason=f"последняя H1 устарела: {age_minutes:.0f} мин после закрытия (лимит {max_age:.0f})",
            last_closed_at=closed_at, age_minutes=age_minutes,
        )

    current = _blend(window)
    previous = _blend({p: bars[:-1] for p, bars in window.items()})
    if not all(math.isfinite(s) for s in [*current.values(), *previous.values()]):
        return StrengthCheck(reason="нечисловые результаты расчёта силы")
    changes = {c: current[c] - previous[c] for c in cfg.CURRENCIES}
    source_returns = {
        pair: {str(n): (bars[-1].close / bars[-1-n].close - 1) * 100 for n, _ in HORIZONS}
        for pair, bars in window.items()
    }
    source_log_returns = {
        pair: {str(n): 100.0 * math.log(bars[-1].close / bars[-1-n].close) for n, _ in HORIZONS}
        for pair, bars in window.items()
    }
    source_prices = {
        pair: {"last": bars[-1].close, **{f"start_{n}": bars[-1-n].close for n, _ in HORIZONS}}
        for pair, bars in window.items()
    }
    eps = float(getattr(cfg, "BRIEFING_STRENGTH_TREND_EPS", 0.025))
    arrows = {c: "↑" if changes[c] >= eps else "↓" if changes[c] <= -eps else "→" for c in cfg.CURRENCIES}
    return StrengthCheck(
        ok=True, last_closed_at=closed_at, age_minutes=max(0.0, age_minutes),
        scores=current, changes=changes, arrows=arrows,
        source_returns=source_returns, source_log_returns=source_log_returns,
        source_prices=source_prices,
    )


def display_index(raw_strength: float) -> float:
    """Fixed-anchor *index*, not a price-change percent or confidence score."""
    scale = float(getattr(cfg, "BRIEFING_STRENGTH_INDEX_SCALE_PCT", 0.50))
    if not math.isfinite(scale) or scale <= 0:
        scale = 0.50
    return 50.0 + 50.0 * math.tanh(raw_strength / scale)


def format_block(report: StrengthCheck) -> list[str]:
    lines = ["💱 СИЛА ВАЛЮТ · ПРОВЕРКА H1", ""]
    if not report.ok:
        lines.append("НЕТ ДОСТОВЕРНЫХ ДАННЫХ — рейтинг скрыт")
        lines.append(f"Причина: {report.reason}")
        return lines
    if report.last_closed_at:
        try:
            local = report.last_closed_at.astimezone(ZoneInfo(cfg.LOCAL_TZ_NAME))
            lines.append(f"Закрытие общей H1: {local:%d.%m.%Y %H:%M} Amsterdam · {report.last_closed_at:%H:%M} UTC")
        except Exception:
            lines.append(f"Закрытие общей H1: {report.last_closed_at:%Y-%m-%d %H:%M} UTC")
    lines.extend([
        "Источник: Twelve Data · 7/7 пар с USD, H1 синхронны",
        "Расчёт: лог-доходности 1/4/12/24 H1 · веса 15/35/30/20",
        "Баллы: разница валют = доходность пары (USD-котировка учитывается по знаку)",
        "Шкала 0–100: условный индекс (50 — нейтрально), НЕ % курса и НЕ вероятность",
        "Стрелка: изменение того же балла относительно предыдущей закрытой H1",
    ])
    # Diagnostic provenance: one line per published briefing in Railway logs.
    # Every score can be rebuilt from these 7 x 4 percentage changes and weights.
    log.info("BRIEFING_STRENGTH_AUDIT %s", json.dumps({
        "closed_h1_utc": report.last_closed_at.isoformat() if report.last_closed_at else "",
        "horizons": HORIZONS, "pair_returns_pct": report.source_returns,
        "pair_log_returns_pct": report.source_log_returns,
        "pair_start_end_prices": report.source_prices,
        "currency_scores_pct_points": report.scores,
        "hourly_score_changes": report.changes,
    }, ensure_ascii=False, separators=(",", ":")))
    ordered = rank_currencies(report.scores)
    for i, (cur, raw) in enumerate(ordered, 1):
        lines.append(f"{i}. {cur} {display_index(raw):.0f}/100 · балл {raw:+.3f} п.п. {report.arrows.get(cur, '→')}")
    strong, weak = ordered[0], ordered[-1]
    lines.append(f"Лидер: {strong[0]} · слабейшая: {weak[0]} · разница баллов {strong[1] - weak[1]:.3f} п.п.")
    return lines
