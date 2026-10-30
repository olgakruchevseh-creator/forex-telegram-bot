"""Intentional RED regression tests for forex-telegram-bot-main (4).
Run from the bot source root (copy this file there) with: pytest -q test_forex_audit_red_cases.py
All tests encode behavior expected AFTER repair, not behavior of the original ZIP.
"""
from datetime import datetime, timedelta, timezone
import math
import pytest

from analysis import Candle
import data_quality_guard
import layer21_mathematical_core
import layer26_mathematical_coherence
import layer44_aggregator_agreement
import mathematical_consensus_bridge
import replay_calibration


def test_invalid_ohlc_nan_must_be_rejected():
    a = {'open': 1.1, 'high': 1.2, 'low': 1.0, 'close': 1.1,
         'time': '2026-10-29T12:00:00+00:00'}
    b = {**a, 'open': float('nan'), 'time': '2026-10-29T13:00:00+00:00'}
    report = data_quality_guard.audit_bars([a,b], expected_seconds=3600)
    assert not report.ok and report.impossible_ohlc > 0


def test_replay_flat_atr_does_not_raise():
    base = datetime(2026, 10, 29, tzinfo=timezone.utc)
    bars = [Candle((base + timedelta(hours=i)).isoformat(), 1.1, 1.1, 1.1, 1.1)
            for i in range(24)]
    row = {'event_id': 'flat', 'side': 'LONG', 'pair': 'EUR/USD',
           'observed_h1_dt': bars[15].dt, 'recorded_utc': bars[15].dt,
           'h1_close_price': 1.1, 'targets': {'TR1':1.12}}
    assert replay_calibration._evaluate(row, bars) is None


def test_nan_cannot_become_perfect_coordinate():
    assert layer21_mathematical_core._clip(float('nan')) != 1.0


def test_bridge_refuses_nan_score():
    x = mathematical_consensus_bridge.summarize({
        'final_mathematical_consensus': {'state': 'FINAL_CONSENSUS_STRONG',
                                         'final_consensus': float('nan')}})
    assert x['score_pct'] is None
    assert not x['available']


def test_coherence_without_values_has_no_facts():
    source = {'state': 'READY'}
    x = layer26_mathematical_coherence.assess('EUR/USD', source, source, source, source, source)
    assert x['state'] == 'NO_FACTS'


def test_aggregator_without_values_has_no_facts():
    source = {'state': 'READY'}
    x = layer44_aggregator_agreement.assess('EUR/USD', *([source] * 9))
    assert x['state'] == 'NO_FACTS'


def test_all_downstream_mathematical_layers_refuse_missing_coordinates():
    import importlib
    import inspect
    import pathlib
    folder = pathlib.Path(__file__).parent
    for number in range(26, 46):
        path = next(folder.glob(f'layer{number}_*.py'))
        mod = importlib.import_module(path.stem)
        required = list(inspect.signature(mod.assess).parameters)[1:]
        args = [{'state': 'READY'} for _ in required]
        outcome = mod.assess('EUR/USD', *args)
        assert outcome['state'] == 'NO_FACTS', (number, outcome)
        assert outcome['trade_effect'] is False


def test_percent_helpers_preserve_real_zero_and_normalize_defaults_in_percent_points():
    from mathematical_input_safety import safe_pct, valid_percentages
    assert safe_pct({'uncertainty_budget': 0}, 'uncertainty_budget', 100.0) == 0.0
    assert safe_pct({}, 'uncertainty_budget', 100.0) == 1.0
    assert safe_pct({'uncertainty_budget': float('nan')}, 'uncertainty_budget', 100.0) == 1.0
    assert valid_percentages([({'v': 0.0}, 'v')])
    assert not valid_percentages([({'v': float('inf')}, 'v')])
    assert not valid_percentages([({}, 'v')])


def test_invalid_ohlc_infinities_and_negative_prices_are_rejected():
    normal = {'open': 1.1, 'high': 1.2, 'low': 1.0, 'close': 1.1,
              'time': '2026-10-29T12:00:00+00:00'}
    for broken in ({'close': float('inf')}, {'low': -1.0}, {'high': float('-inf')}):
        report = data_quality_guard.audit_bars([normal, {**normal, **broken,
                'time': '2026-10-29T13:00:00+00:00'}])
        assert not report.ok
        assert report.impossible_ohlc == 1


def _isolated_bot_helper(name, **variables):
    """Run actual bot helper source without importing Telegram in the audit VM."""
    import ast
    from pathlib import Path
    text = (Path(__file__).parent/'bot.py').read_text(encoding='utf-8')
    tree = ast.parse(text)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.Module(body=[function], type_ignores=[])
    import logging
    from analysis import Candle
    namespace = dict(Candle=Candle, math=math, log=logging.getLogger('audit'),
                     list=list, dict=dict, float=float, len=len, str=str, set=set,
                     **variables)
    exec(compile(module, 'bot.py', 'exec'), namespace)
    return namespace[name]


def test_live_market_parser_rejects_corrupt_complete_series():
    p = _isolated_bot_helper('_parse_values')
    good = {'datetime':'2026-10-29 13:00:00', 'open':'1.10', 'high':'1.12',
            'low':'1.08', 'close':'1.11'}
    assert len(p([good])) == 1
    assert p([good, {**good, 'datetime':'2026-10-29 14:00:00', 'close':'NaN'}]) == []
    assert p([good, {**good, 'datetime':'2026-10-29 14:00:00', 'low':'inf'}]) == []
    assert p([good, good]) == []


def test_cache_fallback_expires_without_disabling_recent_recovery():
    from types import SimpleNamespace
    import logging
    cache = {('EUR/USD', 'H1'): {'ts': 1000.0, 'candles': ['valid']}}
    policy = SimpleNamespace(MARKET_CACHE_FALLBACK_MAX_AGE_MINUTES={'H1':90})
    checker = _isolated_bot_helper('_cache_fallback', CACHE=cache, cfg=policy)
    tf={'key':'H1'}
    assert checker('EUR/USD', tf, 1000.0 + 80*60) == ['valid']
    assert checker('EUR/USD', tf, 1000.0 + 91*60) == []
    cache[('EUR/USD', 'H1')]['ts'] = float('nan')
    assert checker('EUR/USD', tf, 1000.0) == []
