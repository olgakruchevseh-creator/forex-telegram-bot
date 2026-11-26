"""Этап 28F: проверка реальной W1-финальности без влияния на торговлю.

Важное ограничение: W1 OHLC Twelve Data может быть помечена временем
начала агрегата по собственному календарю провайдера. Граница NY Friday
17:00 - оценка завершения торговой недели, НЕ доказательство того, что
конкретный W1-агрегат провайдера финализирован. Ни один W1 бар не изменяется.
"""
from __future__ import annotations

from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo

from market_data_coherence import opened_utc

NY = ZoneInfo('America/New_York')
SETTLE = timedelta(minutes=15)


def expected_fx_friday_close(opened: datetime) -> datetime:
    """Friday 17:00 New York following the assumed FX weekly start; DST-aware."""
    if opened.tzinfo is None:
        raise ValueError('Время W1 должно содержать часовой пояс')
    local = opened.astimezone(NY)
    friday = local.date() + timedelta(days=(4 - local.weekday()) % 7)
    result = datetime.combine(friday, time(17, 0), tzinfo=NY)
    if result <= local:
        result += timedelta(days=7)
    return result.astimezone(timezone.utc)


def inspect_weekly(raw: list, *, as_of: datetime | None = None) -> dict:
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        raise ValueError('as_of должен содержать часовой пояс')
    as_of = as_of.astimezone(timezone.utc)
    if not raw:
        return {'status': 'NO_DATA', 'note': 'W1 нет в кэше; API не вызывается'}
    stamps = []
    try:
        for c in raw:
            opened = opened_utc(c)
            values = [float(getattr(c, v)) for v in ('open', 'high', 'low', 'close')]
            import math
            o, h, l, close = values
            if (not all(math.isfinite(v) and v > 0 for v in values)
                    or h < max(o, l, close) or l > min(o, h, close)):
                return {'status': 'INVALID_OHLC', 'note': 'Некорректные цены W1'}
            if stamps and opened <= stamps[-1]:
                return {'status': 'INVALID_ORDER', 'note': 'Повтор или неправильный порядок W1'}
            stamps.append(opened)
    except (TypeError, ValueError, AttributeError, OverflowError):
        return {'status': 'INVALID_TIME', 'note': 'Некорректная UTC-метка W1'}
    last = stamps[-1]
    if last > as_of:
        return {'status': 'INVALID_FUTURE', 'note': 'Последняя W1 имеет метку открытия в будущем'}
    estimate = expected_fx_friday_close(last)
    nominal = last + timedelta(days=7)
    previous = stamps[-2] if len(stamps) >= 2 else None
    # A later vendor W1 OPEN provides a boundary for the preceding bar,
    # not proof of immutable final OHLC (vendor may revise past candles).
    gap_hours = round((last - previous).total_seconds()/3600, 1) if previous else None
    plausible_successor = bool(previous and 5*24 <= (last-previous).total_seconds()/3600 <= 9*24)
    previous_proven = plausible_successor
    calendar_due = bool(as_of >= estimate + SETTLE)
    nominal_due = bool(as_of >= nominal)
    # Never promote a current W1 based on the Friday estimate alone.
    if nominal_due:
        status = 'LEGACY_7D_COMPLETE'
    elif calendar_due:
        status = 'FRIDAY_ESTIMATE_UNVERIFIED'
    else:
        status = 'FORMING_OR_UNVERIFIED'
    return {
        'status': status,
        'last_open_utc': last.isoformat(),
        'friday_fx_close_utc': estimate.isoformat(),
        'legacy_7d_close_utc': nominal.isoformat(),
        'calendar_due': calendar_due,
        'legacy_due': nominal_due,
        'previous_open_utc': previous.isoformat() if previous else None,
        'previous_proven_by_successor': previous_proven,
        'previous_to_last_w1_hours': gap_hours,
        'previous_boundary_evidence': 'NEW_W1_OPEN_SEEN' if previous_proven else 'UNVERIFIED',
        'friday_to_nominal_gap_hours': round((nominal-estimate).total_seconds()/3600,1),
        'last_current_bar_immutable_proven': False,
        'provider_label_local_weekday': last.astimezone(NY).strftime('%a'),
        'note': ('Граница пятницы — календарная оценка, не подтверждение финальности W1 у поставщика. '
                 'Торговые расчёты и OHLC не изменены.'),
    }


def format_report(market: dict, *, pairs: tuple | list, as_of: datetime | None = None) -> str:
    as_of = as_of or datetime.now(timezone.utc)
    lines = [
        'W1 · ПРОВЕРКА ЗАКРЫТИЯ · ЭТАП 28G · OBSERVE_ONLY',
        f'Снимок: {as_of.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}',
        'Сравнение: +7 суток от метки W1 / пятница 17:00 New York (летнее время учтено).',
        'Пятничная граница НЕ доказывает закрытие агрегата Twelve Data.',
    ]
    mismatches = missing = 0
    for pair in pairs:
        entry = inspect_weekly(((market or {}).get(pair) or {}).get('W1') or [], as_of=as_of)
        state = entry['status']
        if state == 'NO_DATA':
            missing += 1
            lines.append(f'{pair}: нет W1 в кэше')
            continue
        if state.startswith('INVALID'):
            lines.append(f'{pair}: {state} · {entry.get("note", "")}')
            continue
        if entry['calendar_due'] and not entry['legacy_due']:
            mismatches += 1
        lines.append(f'{pair}: {state} · открытие {entry["last_open_utc"][:16]}; '
                     f'NY-пятница {entry["friday_fx_close_utc"][:16]}; '
                     f'+7д {entry["legacy_7d_close_utc"][:16]}; '
                     f'разница {entry["friday_to_nominal_gap_hours"]}ч; '
                     f'предыдущая W1: {entry["previous_boundary_evidence"]} '
                     f'(интервал {entry["previous_to_last_w1_hours"]}ч)')
    lines.extend([
        f'Промежуточное окно (после пятницы, до +7д): {mismatches}/{len(pairs)}; нет W1: {missing}.',
        'Появление новой метки W1 подтверждает границу предыдущей, но НЕ неизменность предыдущей OHLC. '
        'Для окончательного решения нужна сверка последовательных W1 и H4/H1 по поставщику, '
        'а также подтверждение задержки финализации после пятничного закрытия.',
        'Никаких изменений сигналов, Master/Killer, истории и порогов не сделано.',
    ])
    return '\n'.join(lines)
