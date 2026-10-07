from quasimodo_engine import _normalize_pivots, _prior_trend


def test_normalize_pivots_keeps_more_extreme_same_side():
    raw=[(1,1,1.10,'a'),(2,1,1.12,'b'),(3,-1,1.05,'c'),(4,-1,1.03,'d'),(5,1,1.11,'e')]
    got=_normalize_pivots(raw)
    assert [(x[0],x[1],x[2]) for x in got] == [(2,1,1.12),(4,-1,1.03),(5,1,1.11)]


def test_prior_trend_requires_rising_swings_for_bearish_qm():
    piv=[(1,-1,1.00,'a'),(2,1,1.10,'b'),(3,-1,1.04,'c'),(4,1,1.14,'d')]
    assert _prior_trend(piv,3,-1)
    flat=[(1,-1,1.04,'a'),(2,1,1.14,'b'),(3,-1,1.00,'c'),(4,1,1.12,'d')]
    assert not _prior_trend(flat,3,-1)


def test_prior_trend_requires_falling_swings_for_bullish_qm():
    piv=[(1,1,1.20,'a'),(2,-1,1.10,'b'),(3,1,1.16,'c'),(4,-1,1.06,'d')]
    assert _prior_trend(piv,3,1)

from types import SimpleNamespace
from quasimodo_engine import _zone_touch_episodes


def _bar(lo,hi):
    return SimpleNamespace(low=lo, high=hi)


def test_qml_consecutive_overlap_is_one_retest_episode():
    z=SimpleNamespace(zone_low=1.10, zone_high=1.11)
    post=[_bar(1.12,1.13),_bar(1.105,1.115),_bar(1.104,1.112),_bar(1.12,1.13)]
    assert _zone_touch_episodes(post,z)==1


def test_qml_reentry_counts_as_second_retest():
    z=SimpleNamespace(zone_low=1.10, zone_high=1.11)
    post=[_bar(1.105,1.115),_bar(1.12,1.13),_bar(1.104,1.108)]
    assert _zone_touch_episodes(post,z)==2
