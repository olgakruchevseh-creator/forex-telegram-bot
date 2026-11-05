"""Shared mathematics, time anchoring, and method-drift diagnostics."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from analysis import Candle
import config as cfg
import briefing_strength_verified as verified
import direction_consistency
import market_data_coherence as coherence
from currency_strength_math import usd_star_scores, stable_log_return_pct


def test_USD_star_identity_with_USD_base_and_quote():
    returns = {p: (0.1 + i * 0.01) for i, p in enumerate(cfg.PAIRS)}
    scores = usd_star_scores(returns)
    assert abs(sum(scores.values())) < 1e-10
    for pair, ret in returns.items():
        base, quote = pair.split('/')
        assert scores[base] - scores[quote] == pytest.approx(ret)


def test_shared_math_rejects_missing_pair_and_nonfinite_return():
    with pytest.raises(ValueError):
        usd_star_scores({cfg.PAIRS[0]: .5})
    data = {p: .5 for p in cfg.PAIRS}
    data[cfg.PAIRS[0]] = float('nan')
    with pytest.raises(ValueError):
        usd_star_scores(data)


def test_log_return_does_not_overflow_on_extreme_ratio():
    assert stable_log_return_pct(1e308, 1e-308) > 100000
    with pytest.raises(ValueError):
        stable_log_return_pct(float('nan'), 1.0)
    with pytest.raises(ValueError):
        stable_log_return_pct(1.0, 0.0)


def _market(as_of: datetime, *, n=42):
    end_open = as_of - timedelta(hours=1)
    prices = {}
    for j, pair in enumerate(cfg.PAIRS):
        bars=[]
        for k in range(n):
            opened = end_open - timedelta(hours=n-1-k)
            c = 1.00 * (1.001 + .0001*j) ** k
            bars.append(Candle(dt=opened.strftime('%Y-%m-%d %H:%M:%S'),
                               open=c, high=c*1.001, low=c*.999, close=c))
        prices[pair]={'H1':bars}
    return prices


def test_verified_strength_excludes_bars_after_requested_asof():
    as_of = datetime(2026, 11, 5, 10, tzinfo=timezone.utc)
    market = _market(as_of)
    # Insert another provider bar which is already closed in today's real clock,
    # but was not closed at the historical as-of time.
    for pair in cfg.PAIRS:
        market[pair]['H1'].append(Candle('2026-11-05 10:00:00', 1.2, 1.3, 1.1, 1.2))
    check = verified.calculate(market, as_of=as_of + timedelta(minutes=3))
    assert check.ok, check.reason
    assert check.last_closed_at == as_of


def test_strict_closed_bar_check_rejects_missing_history_and_accepts_shared_weekend_gap():
    as_of = datetime(2026, 11, 5, 10, tzinfo=timezone.utc)
    market = _market(as_of)
    for pair in cfg.PAIRS:
        del market[pair]['H1'][6:8]  # weekend-like common missing hours
    check = coherence.inspect(market, as_of=as_of + timedelta(minutes=5), required_tfs=('H1',))
    assert check['ok'], check['reasons']
    del market[cfg.PAIRS[0]]['H1'][14]
    check = coherence.inspect(market, as_of=as_of + timedelta(minutes=5), required_tfs=('H1',))
    assert not check['ok']
    assert any('история' in x for x in check['reasons'])


def test_direction_consistency_is_observation_only_and_surfaces_gap_disagreement(monkeypatch):
    as_of = datetime(2026, 11, 5, 10, tzinfo=timezone.utc)
    market = _market(as_of)
    check=verified.calculate(market, as_of=as_of + timedelta(minutes=4))
    assert check.ok
    # Force a contradictory *blended* gap, without altering 4h trade method.
    scores = dict(check.scores)
    scores['EUR'],scores['USD'] = -100, +100
    mixed = SimpleNamespace(ok=True,last_closed_at=as_of,scores=scores)
    result=direction_consistency.inspect(market,mixed)
    assert result['decision_authority'] is False
    assert 'EUR/USD' in [x['pair'] for x in result['conflicts']]
    assert 'НЕ отдельный сигнал' in direction_consistency.note(result)


def test_H4_freshness_at_monday_open_uses_trading_bars_not_weekend_calendar():
    from datetime import datetime as DT
    as_of = DT(2026, 11, 9, 1, 5, tzinfo=timezone.utc)
    # Provider last Friday H4 close at 20:00; market reopens Sunday 22:00.
    friday = DT(2026, 11, 6, 20, tzinfo=timezone.utc)
    reopens = [DT(2026, 11, 8, h, tzinfo=timezone.utc) for h in (22, 23)] + [DT(2026, 11, 9, 0, tzinfo=timezone.utc)]
    h1_opens = [friday - timedelta(hours=k+1) for k in range(37)][::-1] + reopens
    h4_opens = [friday - timedelta(hours=4*(k+1)) for k in range(28)][::-1]
    def bars(stamps):
        return [Candle(t.strftime('%Y-%m-%d %H:%M:%S'),1,1.01,.99,1) for t in stamps]
    market = {p: {'H1': bars(h1_opens), 'H4': bars(h4_opens)} for p in cfg.PAIRS}
    result = coherence.inspect(market, as_of=as_of, required_tfs=('H1','H4'))
    assert result['ok'], result['reasons']
    # Five new trading hours without any new H4 bar *must* be rejected.
    late = DT(2026, 11, 9, 7, 5, tzinfo=timezone.utc)
    for pair in cfg.PAIRS:
        market[pair]['H1'].extend(bars(DT(2026,11,9,h,tzinfo=timezone.utc) for h in range(1,7)))
    result = coherence.inspect(market, as_of=late, required_tfs=('H1','H4'))
    assert not result['ok']
    assert any('H4: устаревшая' in r for r in result['reasons'])
