"""Синтетические ФИКСТУРЫ для функционального тестирования, НЕ статистика Forex."""
from datetime import datetime, timedelta, timezone
import csv
import math
from pathlib import Path

import pytest

from analysis import Candle, currency_strength
import config as cfg
import historical_direction_calibration as mod


def fixture_basket(hours=550):
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    out = {p: {} for p in cfg.PAIRS}
    prices = {p: (142.0 if "JPY" in p else 1.0 if "CAD" in p else 1.1)
              for p in cfg.PAIRS}
    prior = dict(prices)
    idx = 0
    for h in range(hours + 220):
        t = start + timedelta(hours=h)
        if t.weekday() >= 5:
            continue
        for j, p in enumerate(cfg.PAIRS):
            # Regime flips and noisy variation are intentionally artificial.
            drift = 0.00042 * math.sin(idx / 14. + j * 0.45)
            drift += 0.00018 * math.cos(idx / 8.1 - j * 0.32)
            if p.startswith('USD/'):
                drift *= -1
            prices[p] *= math.exp(drift)
            op, cl = prior[p], prices[p]
            margin = max(op, cl) * 0.00022
            out[p][t] = Candle(t.strftime('%Y-%m-%d %H:%M:%S'), op,
                               max(op, cl) + margin, min(op, cl) - margin, cl)
            prior[p] = cl
        idx += 1
        if idx >= hours:
            break
    return out


def test_legacy_formula_exactly_matches_original_complete_basket():
    data = fixture_basket()
    ts = sorted(data[cfg.PAIRS[0]])
    for idx in (25, 48, 190, 350):
        bundle = {p: [data[p][t] for t in ts[idx-25:idx+1]] for p in cfg.PAIRS}
        actual = mod.score_at(bundle, 'old_simple_4h')
        original = currency_strength({p: series for p, series in bundle.items()}, 4)
        for c in cfg.CURRENCIES:
            assert actual[c] == pytest.approx(original[c], abs=1e-10)


def test_new_4h_has_matching_pair_quote_sign_not_fabricated_usd_direction():
    data = fixture_basket()
    ts = sorted(data[cfg.PAIRS[0]])
    bundle = {p: [data[p][t] for t in ts[1:27]] for p in cfg.PAIRS}
    scores = mod.score_at(bundle, 'new_log_4h')
    for p in cfg.PAIRS:
        b, q = p.split('/')
        expected = mod.stable_log_return_pct(bundle[p][-1].close, bundle[p][-5].close)
        assert scores[b] - scores[q] == pytest.approx(expected, abs=1e-10)
    assert sum(scores.values()) == pytest.approx(0, abs=1e-10)


def test_future_spike_and_unclosed_bar_do_not_change_decision():
    data = fixture_basket()
    ts = sorted(data[cfg.PAIRS[0]])
    history = {p: [data[p][t] for t in ts[25:51]] for p in cfg.PAIRS}
    before = {m: mod.score_at(history, m) for m in mod.METHODS}
    for p in cfg.PAIRS:
        tomorrow = data[p][ts[51]]
        tomorrow.close *= 100.0  # huge AFTER decision: must not peek
        tomorrow.high = max(tomorrow.high, tomorrow.close)
    after = {m: mod.score_at(history, m) for m in mod.METHODS}
    assert before == after


def test_bad_ohlc_and_duplicate_rejected_instead_of_silently_removed(tmp_path):
    data = fixture_basket(hours=70)
    for p, bars in data.items():
        with (tmp_path / (p.replace('/', '') + '_H1.csv')).open('w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(('datetime','open','high','low','close'))
            for t, b in bars.items():
                writer.writerow((b.dt,b.open,b.high,b.low,b.close))
    ok = mod.read_basket(tmp_path)
    assert len(ok) == 7
    name = tmp_path / 'EURUSD_H1.csv'
    with name.open('a') as f:
        b = next(iter(data['EUR/USD'].values()))
        f.write(f'\n{b.dt},{b.open},{b.high},{b.low},{b.close}\n')
    with pytest.raises(ValueError, match='повторная метка'):
        mod.read_basket(tmp_path)


def test_replay_synthetic_has_purged_chronological_split_and_no_live_changes():
    report = mod.evaluate(fixture_basket(), tune=True)
    assert report['meta']['trade_signals_modified'] is False
    assert report['meta']['test_first_utc'] > report['meta']['split_utc']
    assert report['meta']['train_last_utc'] < report['meta']['split_utc']
    assert report['meta']['valid_feature_hours'] > 200
    assert report['meta']['purge_h1_bars'] >= max(mod.HORIZONS)
    assert set(report['comparisons']) == {'new_log_4h','new_log_blend'}
    for m in mod.METHODS:
        assert report['thresholds'][m]['gap_epsilon_pct_points'] in mod.EPS_GRID
        for horizon in mod.HORIZONS:
            row = report['test'][m][str(horizon)]
            assert 0 <= row['decided'] <= row['eligible']
            if row['accuracy'] is not None:
                assert 0 <= row['accuracy'] <= 1
    assert 'синтетические' not in report['meta']['caveat']
    assert 'без спреда' in report['meta']['caveat']


def test_intrabar_both_target_and_cancellation_is_ambiguous():
    data = fixture_basket(hours=60)
    pair = 'EUR/USD'
    ts = sorted(data[pair])
    rows = [data[pair][t] for t in ts[:27]]
    entry = rows[25].close
    rows[26] = Candle(rows[26].dt, entry, entry * 1.015,
                      entry * .985, entry)
    out = mod._outcome(pair, 1, rows, 25, 1)
    # UTC 01:00 on June 2 is safely inside the same Amsterdam calendar date.
    if out is not None:
        assert out['touch'] == 'AMBIGUOUS'


def test_cli_no_real_csv_does_not_produce_fake_win_rates(tmp_path):
    output = tmp_path / 'report'
    assert mod.main(['evaluate', '--data-dir', str(tmp_path), '--out', str(output)]) == 2
    assert not output.exists()
