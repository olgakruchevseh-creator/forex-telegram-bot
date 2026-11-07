from datetime import datetime, timedelta
import math

from analysis import Candle
import levels
import session_projection_reports as reports
import briefing_delivery as delivery


def _bars(count=80, base=1.1000, amp=0.0060, step=0.00035, start=None, minutes=60):
    start = start or datetime(2026, 11, 7, 0, 0)
    out = []
    price = base
    for i in range(count):
        wave = math.sin(i / 4.0) * amp
        drift = (i - count / 2) * step / count
        open_ = price
        close = base + wave + drift
        high = max(open_, close) + 0.0012 + abs(math.sin(i / 3.0)) * 0.0004
        low = min(open_, close) - 0.0011 - abs(math.cos(i / 5.0)) * 0.0003
        dt = (start + timedelta(minutes=minutes * i)).strftime('%Y-%m-%d %H:%M:%S')
        out.append(Candle(dt, open_, high, low, close))
        price = close
    return out


def _market():
    h1 = _bars()
    h4 = [h1[i] for i in range(0, len(h1), 4)]
    d1 = [h1[i] for i in range(0, len(h1), 24)]
    return {'H1': h1, 'H4': h4, 'D1': d1}


def test_levels_snapshot_returns_nearest_supports_and_resistances():
    snap = levels.briefing_levels_snapshot('EUR/USD', _market(), max_each=2)
    assert snap['current_price'] is not None
    assert snap['supports']
    assert snap['resistances']
    assert snap['supports'][0]['label'] == 'S1'
    assert snap['resistances'][0]['label'] == 'R1'
    assert snap['supports'][0]['price'] <= snap['current_price']
    assert snap['resistances'][0]['price'] >= snap['current_price']


def test_combined_caption_lists_support_and_resistance_lines():
    bundle = {
        'symbol': 'EUR/USD',
        'period': 'ЕВРОПА → АМЕРИКА · около 6 ч',
        'echo': {'result': {'side': 'LONG', 'direction_probability': 62}},
        'pivot': {'result': {'side': 'LONG', 'kind': 'high', 'zone_low': 1.1040, 'zone_high': 1.1050}},
        'zigzag': {'zigzag_directions': {'H1': 1}, 'duration_low': 3, 'duration_high': 6},
        'levels_brief': {
            'supports': [{'label': 'S1', 'price': 1.0985, 'distance_pips': 18, 'tfs_text': 'H4 · H1'}],
            'resistances': [{'label': 'R1', 'price': 1.1032, 'distance_pips': 29, 'tfs_text': 'D1 · H4'}],
        },
    }
    text = reports.combined_pair_caption(bundle)
    assert 'Уровни поддержки / сопротивления:' in text
    assert 'Поддержка: S1 1.09850' in text
    assert 'Сопротивление: R1 1.10320' in text


def test_compact_caption_preserves_visual_support_and_resistance_lines():
    pair_text = '━━━ 💱 EUR/USD ━━━\n🟢 НАПРАВЛЕНИЕ: ЛОНГ\nСостояние: ТРЕНД'
    visual = '\n'.join([
        'Пара: EUR/USD',
        'Эхо: 🟢 ЛОНГ · доля похожих путей 62%',
        'Next Pivot: 🟢 ЛОНГ · ВЕРШИНЫ 1.10400–1.10500',
        'Adaptive ZigZag: 🟢 ЛОНГ · ≈ 3–6 закрытых H1-свечей до вероятного угла',
        'Поддержка: S1 1.09850 (18 п.) · H4 · H1',
        'Сопротивление: R1 1.10320 (29 п.) · D1 · H4',
    ])
    out = delivery.compact_pair_caption(pair_text, visual, limit=1000)
    assert 'Поддержка: S1 1.09850' in out
    assert 'Сопротивление: R1 1.10320' in out
