"""Market coverage now checks actual closed timestamps, not only array counts."""
from datetime import datetime, timedelta, timezone
from analysis import Candle, market_coverage
import config as cfg


_MINUTES = {'M5': 5, 'M15': 15, 'H1': 60, 'H4': 240}


def _bars(tf='H1', n=35, now=None):
    now = now or datetime.now(timezone.utc)
    mins = _MINUTES[tf]
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    past = int((now - midnight).total_seconds() // 60)
    boundary = midnight + timedelta(minutes=(past // mins) * mins)
    first = boundary - timedelta(minutes=mins * n)
    return [Candle(dt=(first + timedelta(minutes=i * mins)).strftime('%Y-%m-%d %H:%M:%S'),
                   open=1.0, high=1.1, low=0.9, close=1.05) for i in range(n)]


def _market():
    now = datetime.now(timezone.utc)
    return {p: {tf: _bars(tf, now=now) for tf in cfg.MARKET_REQUIRED_TFS} for p in cfg.PAIRS}


def test_complete_basket():
    cov = market_coverage(_market())
    assert cov['complete'], cov['coherence_reasons']
    assert cov['present'] == cov['expected']
    assert cov['missing'] == []
    assert cov['coherence']['per_tf']['H1']['aligned']


def test_missing_pair_is_incomplete():
    market = _market()
    market['EUR/USD']['M15'] = []
    cov = market_coverage(market)
    assert not cov['complete']
    assert 'EUR/USD:M15' in cov['missing']


def test_short_h1_is_incomplete():
    market = _market()
    market['GBP/USD']['H1'] = _bars('H1', 3)
    cov = market_coverage(market)
    assert not cov['complete']
    assert any(x.startswith('GBP/USD:') for x in cov['short_h1'])


def test_h1_unsynced_is_not_complete_even_if_all_series_present():
    market = _market()
    market['USD/JPY']['H1'] = market['USD/JPY']['H1'][:-1]
    cov = market_coverage(market)
    assert cov['present'] == cov['expected']
    assert not cov['complete']
    assert any('H1: несинхронны' in x for x in cov['coherence_reasons'])


def test_mid_window_missing_h1_prevents_silent_4h_strength_mismatch():
    market = _market()
    market['USD/JPY']['H1'] = market['USD/JPY']['H1'][:16] + market['USD/JPY']['H1'][17:]
    cov = market_coverage(market)
    assert not cov['complete']
    assert any('H1: несинхронна история' in x for x in cov['coherence_reasons'])


def test_corrupt_and_duplicate_ohlc_rejected():
    market = _market()
    a = market['EUR/USD']['M5']
    a[-1] = Candle(dt=a[-1].dt, open=1, high=.5, low=.9, close=1)
    assert not market_coverage(market)['complete']
    market = _market()
    a = market['EUR/USD']['M5']
    a[-1] = a[-2]
    assert not market_coverage(market)['complete']


def test_stale_h1_not_made_fresh_by_new_http_cache_timestamp():
    market = _market()
    market['EUR/USD']['H1'] = _bars('H1', now=datetime.now(timezone.utc) - timedelta(hours=4))
    cov = market_coverage(market)
    assert not cov['complete']
    assert any('устаревшая' in r for r in cov['coherence_reasons'])
