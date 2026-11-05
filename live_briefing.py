"""Сессионный контроль изменения направления: только закрытая синхронная H1.

Наблюдательная рассылка. Не генерирует входы, не переписывает сигналы и не
интерпретирует календарный релиз как доказанную ПРИЧИНУ движения цены.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import math

import briefing
import briefing_strength_verified as verified
import config as cfg

LOCAL_TZ = ZoneInfo('Europe/Amsterdam')
SIDES = {'LONG': 'ЛОНГ', 'SHORT': 'ШОРТ', None: 'НЕ ПОДТВЕРЖДЕНО'}
_TF_MINUTES = {'W1': 10080, 'D1': 1440, 'H4': 240, 'H1': 60, 'M15': 15, 'M5': 5}


def anchored_market(market: dict, h1_closed_at: datetime) -> dict:
    """Freeze *every* indicator input to one H1 close; no later M15/M5 bars.

    Provider timestamps are bar OPEN in UTC. Malformed bars are discarded,
    never treated as an unknown-but-valid current candle.
    """
    if h1_closed_at.tzinfo is None:
        raise ValueError('H1 anchor must be timezone aware')
    cutoff = h1_closed_at.astimezone(timezone.utc)
    out = {}
    for symbol in cfg.PAIRS:
        out[symbol] = {}
        for tf, bars in ((market or {}).get(symbol) or {}).items():
            minutes = _TF_MINUTES.get(tf)
            if minutes is None:
                continue
            keep = []
            for bar in bars or []:
                try:
                    opened = datetime.fromisoformat(str(bar.dt).strip().replace('Z', '+00:00').replace(' ', 'T'))
                    if opened.tzinfo is None:
                        opened = opened.replace(tzinfo=timezone.utc)
                    if opened.astimezone(timezone.utc) + timedelta(minutes=minutes) <= cutoff:
                        keep.append(bar)
                except (AttributeError, ValueError, TypeError, OverflowError):
                    continue
            out[symbol][tf] = keep
    return out


def snapshot(market: dict, events: list | None = None, as_of: datetime | None = None) -> dict | None:
    """Same seven verified USD H1 returns and PairBriefs as full briefing.

    Failure is neutral: NEVER synthesize direction from a missing series.
    """
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        return None
    as_of = as_of.astimezone(timezone.utc)
    check = verified.calculate(market, as_of=as_of)
    if not check.ok or not check.last_closed_at:
        return None
    frozen = anchored_market(market, check.last_closed_at)
    briefs = briefing.build_pair_briefs(frozen, check.scores, events or [], check.last_closed_at)
    if len(briefs) != len(cfg.PAIRS) or any(b.symbol not in cfg.PAIRS for b in briefs):
        return None
    leaders = briefing.pick_leaders(briefs)
    pairs = {}
    for b in briefs:
        direction = b.side if b.side in ('LONG', 'SHORT') else None
        pairs[b.symbol] = {
            'side': direction, 'h1': b.h1, 'h4': b.h4, 'd1': b.d1,
            'state': b.state, 'zigzag_h4': b.zigzag_h4_side,
            'gap': round(b.gap, 6), 'amd': str(getattr(b, 'amd', '') or ''),
            'position': str(getattr(b, 'position', '') or ''),
        }
    return {
        'h1_close': check.last_closed_at.isoformat(),
        'session': briefing.briefing_id(as_of.astimezone(LOCAL_TZ)),
        'currencies': {k: round(v, 6) for k, v in check.scores.items()},
        'pairs': pairs,
        'leader': leaders[0].symbol if leaders else None,
        'leader_side': leaders[0].side if leaders else None,
    }


def _direction(value) -> str:
    return SIDES.get(value, 'НЕ ПОДТВЕРЖДЕНО')


def changes(previous: dict, current: dict) -> dict | None:
    """Material shifts in *confirmed* session directions; no strength-only verdict."""
    if not previous or not current or previous.get('session') != current.get('session'):
        return None
    if not current.get('h1_close') or current['h1_close'] <= previous.get('h1_close', ''):
        return None
    old_pairs, new_pairs = previous.get('pairs') or {}, current.get('pairs') or {}
    if set(old_pairs) != set(cfg.PAIRS) or set(new_pairs) != set(cfg.PAIRS):
        return None
    flips, lost, gained, h1_changes = [], [], [], []
    for pair in cfg.PAIRS:
        old, new = old_pairs[pair], new_pairs[pair]
        a, b = old.get('side'), new.get('side')
        if a in ('LONG', 'SHORT') and b in ('LONG', 'SHORT') and a != b:
            flips.append(pair)
        elif a in ('LONG', 'SHORT') and b is None:
            lost.append(pair)
        elif a is None and b in ('LONG', 'SHORT'):
            gained.append(pair)
        if old.get('h1') in ('LONG', 'SHORT') and new.get('h1') in ('LONG', 'SHORT') and old['h1'] != new['h1']:
            h1_changes.append(pair)
    p_leader = previous.get('leader')
    leader_invalidated = bool(p_leader and p_leader in lost + flips)
    # New pair signal alone is too noisy. An invalidated *leader*, confirmed
    # side reversal, or changes in two or more pairs are briefing-worthy.
    leader_changed = (previous.get('leader'), previous.get('leader_side')) != (current.get('leader'), current.get('leader_side'))
    # Two independent pair-level H1 changes (even without a final session side)
    # merit a *context* revision, never a fabricated reversal.
    material = bool(flips or lost or len(gained) >= 2 or len(h1_changes) >= 2
                    or (leader_changed and current.get('leader')))
    if not material:
        return None
    all_changed = list(dict.fromkeys(flips + lost + gained + (
        h1_changes if len(h1_changes) >= 2 else [])))
    if not all_changed:
        return None
    return {
        'flips': flips, 'lost': lost, 'gained': gained, 'h1_changes': h1_changes,
        'leader_invalidated': leader_invalidated,
        'pairs': all_changed,
        'severity': ('REVERSAL' if flips else 'CONTEXT' if not flips and not lost and not gained
                     else 'REVISION'),
    }


def recently_scheduled_events(events: list | None, current: dict, *, minutes: int = 120) -> list[str]:
    """Proximity only; calendar presence != verified causal explanation."""
    end = datetime.fromisoformat(current['h1_close'])
    begin = end - timedelta(minutes=minutes)
    affected = set()
    for pair in cfg.PAIRS:
        affected.update(pair.split('/'))
    found = []
    for e in events or []:
        try:
            ts = e.dt_utc.astimezone(timezone.utc)
            if begin <= ts <= end and str(e.currency) in affected and (
                str(e.impact).upper() == 'HIGH' or str(e.impact).upper() == 'MEDIUM'
            ):
                name = str(e.title).strip()[:90]
                if name:
                    found.append(f'{e.currency}: {name}')
        except (AttributeError, TypeError, ValueError, OverflowError):
            continue
    return list(dict.fromkeys(found))[:3]


def report(previous: dict, current: dict, delta: dict, events: list | None = None) -> str:
    close = datetime.fromisoformat(current['h1_close']).astimezone(LOCAL_TZ)
    older = datetime.fromisoformat(previous['h1_close']).astimezone(LOCAL_TZ)
    lines = [
        '━━━━━━━━━━━━━━━━━━', '🔄 FOREX · ПЕРЕСМОТР СЕССИОННОГО БРИФИНГА',
        '━━━━━━━━━━━━━━━━━━',
        f'Закрытие H1: {close:%d.%m.%Y %H:%M} Amsterdam',
        f'Сравнение с брифингом / обновлением: {older:%H:%M}',
        'Основание: новые закрытые H1 и повторный расчёт 7 синхронных USD-пар.',
        '', '💱 ИЗМЕНЕНИЕ СИЛЫ ВАЛЮТ (индекс 0–100 не вероятность)',
    ]
    strengths = []
    for c in cfg.CURRENCIES:
        prev = float(previous['currencies'][c])
        curr = float(current['currencies'][c])
        if not math.isfinite(prev) or not math.isfinite(curr):
            continue
        strengths.append((abs(curr-prev), c, prev, curr))
    for _, c, prev, curr in sorted(strengths, reverse=True)[:4]:
        lines.append(f'{c}: {verified.display_index(prev):.0f} → {verified.display_index(curr):.0f}/100 · балл {prev:+.3f} → {curr:+.3f} п.п.')
    lines.extend(['', '🧭 НАПРАВЛЕНИЕ ПАР · БЫЛО → СТАЛО'])
    for pair in delta['pairs']:
        p = previous['pairs'][pair]
        n = current['pairs'][pair]
        label = ('ПОДТВЕРЖДЁННЫЙ РАЗВОРОТ' if pair in delta['flips'] else
                 'СЦЕНАРИЙ УТРАТИЛ ПОДТВЕРЖДЕНИЕ' if pair in delta['lost'] else
                 'НОВЫЙ ПОДТВЕРЖДЁННЫЙ СЦЕНАРИЙ' if pair in delta['gained'] else
                 'ИЗМЕНЕНИЕ H1; ОБЩЕЕ НАПРАВЛЕНИЕ НЕ ПОДТВЕРЖДЕНО')
        lines.append(f'{pair}: {_direction(p.get("side"))} → {_direction(n.get("side"))}')
        lines.append(f'  {label} · H1 {briefing._ru_display(p.get("h1", "нет данных"))} → {briefing._ru_display(n.get("h1", "нет данных"))} · H4 {briefing._ru_display(n.get("h4", "нет данных"))}')
        if p.get('amd') and n.get('amd') and p['amd'] != n['amd']:
            lines.append('  AMD: фаза изменилась; подробности в обновлённой карточке')
        if p.get('zigzag_h4') != n.get('zigzag_h4'):
            lines.append('  Зигзаг H4: изменилось структурное направление')
    last = previous.get('leader')
    new = current.get('leader')
    if last != new or previous.get('leader_side') != current.get('leader_side'):
        lines.extend(['', f'🏆 Приоритет: {last or "НЕТ"} {_direction(previous.get("leader_side"))} → {new or "НЕТ"} {_direction(current.get("leader_side"))}'])
    context_events = recently_scheduled_events(events, current)
    if context_events:
        lines.extend(['', '📰 События рядом по времени (причинная связь НЕ подтверждена):'])
        lines.extend(f'• {e}' for e in context_events)
    else:
        lines.extend(['', '📰 По календарю причина изменения не установлена; внеплановые новости могут отсутствовать в источнике.'])
    lines.extend([
        '', 'Пересчитаны структура, ZigZag, приоритет и сила валют по закрытым свечам.',
        'Echo / Next Pivot и уровни — смотри ниже в обновлённых карточках.',
        'Это пересмотр сценария, НЕ новый торговый сигнал.',
        'Точки входа и TR требуют отдельной финальной проверки.',
    ])
    return '\n'.join(lines)
