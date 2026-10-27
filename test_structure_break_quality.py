from datetime import datetime, timedelta, timezone

from analysis import Candle
import structure_break_quality as sbq


def _series(strong=True):
    start = datetime(2026, 10, 20, tzinfo=timezone.utc)
    bars = []
    for i in range(30):
        p = 1.1000 + (i % 3) * 0.00005
        o, c = (p + 0.00015, p - 0.00005) if i == 29 else (p, p + 0.00002)
        bars.append(Candle((start + timedelta(hours=i)).strftime('%Y-%m-%d %H:%M:%S'), o, p + 0.00035, p - 0.00035, c))
    if strong:
        br = Candle((start + timedelta(hours=30)).strftime('%Y-%m-%d %H:%M:%S'), 1.1000, 1.1020, 1.0999, 1.1018)
    else:
        br = Candle((start + timedelta(hours=30)).strftime('%Y-%m-%d %H:%M:%S'), 1.1007, 1.10125, 1.1005, 1.10108)
    rt = Candle((start + timedelta(hours=31)).strftime('%Y-%m-%d %H:%M:%S'), 1.10095, 1.1018, 1.10085, 1.10155)
    return bars + [br, rt], br, rt


def test_real_structure_break_contract_accepts_strong_break_and_retest():
    bars, br, rt = _series(True)
    out = sbq.assess({'H1': bars}, 'LONG', 1.1010, br.dt, break_tf='H1', retest_dt=rt.dt)
    assert out.confirmed
    assert out.displacement and out.structure_break and out.retest_reclaim
    assert out.sign_count >= 3


def test_real_structure_break_contract_rejects_weak_displacement():
    bars, br, rt = _series(False)
    out = sbq.assess({'H1': bars}, 'LONG', 1.1010, br.dt, break_tf='H1', retest_dt=rt.dt)
    assert not out.confirmed
    assert not out.displacement
