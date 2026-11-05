"""Regressions for observational FX basket W1/D1/H4/H1 strength."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy

import config as cfg
from analysis import Candle
import multi_tf_currency_strength as m

NOW = datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc)
TF_LAST_OPEN = {
    "W1": datetime(2026, 10, 26, tzinfo=timezone.utc),
    "D1": datetime(2026, 11, 4, tzinfo=timezone.utc),
    "H4": datetime(2026, 11, 5, 4, tzinfo=timezone.utc),
    "H1": datetime(2026, 11, 5, 9, tzinfo=timezone.utc),
}


def market(up=0.001):
    data = {}
    for pair in cfg.PAIRS:
        tfs = {}
        for tf, (minutes, _, _, _) in m.TF_SPEC.items():
            start = TF_LAST_OPEN[tf] - timedelta(minutes=minutes * 35)
            bars = []
            for idx in range(36):
                stamp = start + timedelta(minutes=minutes * idx)
                # The inverse USD pairs should confirm the *same* positive
                # non-USD strength, without falsely counting USD as stronger.
                r = (-up if pair.startswith("USD/") else up)
                close = (1.10 if "JPY" not in pair else 140) * ((1 + r) ** idx)
                bars.append(Candle(stamp.strftime('%Y-%m-%d %H:%M:%S'), close, close, close, close))
            # A provider may include an unclosed, volatile candle: ignored.
            next_stamp = TF_LAST_OPEN[tf] + timedelta(minutes=minutes)
            bars.append(Candle(next_stamp.strftime('%Y-%m-%d %H:%M:%S'), 1., 1., 1., 0.))
            tfs[tf] = bars
        data[pair] = tfs
    return data


def test_four_tfs_closed_and_sign_orientation():
    report = m.inspect(market(), 'EUR/USD', 'LONG', NOW)
    assert report['decision_authority'] is False
    assert report['available_tf'] == 4
    assert report['aligned_tf'] == 4
    assert report['stable_support_tf'] == 4
    assert report['opposed_tf'] == 0
    assert report['status'] == 'ПРЕИМУЩЕСТВО В НАПРАВЛЕНИИ'
    assert all(report['by_tf'][tf]['assessment'] == 'ПОДДЕРЖИВАЕТ' for tf in m.TF_ORDER)
    assert report['by_tf']['H1']['closed_at_utc'] == NOW.isoformat()
    assert report['by_tf']['W1']['closed_at_utc'] == datetime(2026, 11, 2, tzinfo=timezone.utc).isoformat()
    assert 'W1+' in m.short_note(report)
    # USD/JPY is weak when JPY is stronger; the sign must invert.
    jpy = m.inspect(market(), 'USD/JPY', 'SHORT', NOW)
    assert jpy['aligned_tf'] == 4
    # A zero-sum center allows exact relative basket consistency.
    scores = report['by_tf']['H1']['score_log_pp']
    assert abs(sum(scores.values())) < 1e-5
    assert scores['EUR'] > scores['USD']


def test_mismatch_in_any_tf_is_not_silently_used():
    data = market()
    bars = data['USD/CHF']['D1']
    data['USD/CHF']['D1'] = [Candle(
        (datetime.fromisoformat(c.dt.replace(' ', 'T')).replace(tzinfo=timezone.utc) - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S'),
        c.open, c.high, c.low, c.close) for c in bars]
    report = m.inspect(data, 'EUR/USD', 'LONG', NOW)
    assert report['available_tf'] == 3
    assert report['by_tf']['D1']['status'] == 'НЕТ ДАННЫХ'
    assert 'несинхронные' in report['by_tf']['D1']['reason']
    assert report['can_block_signal'] is False


def test_missing_series_neutral_without_fabricating_zeros():
    data = market()
    data['AUD/USD']['W1'] = []
    report = m.inspect(data, 'EUR/USD', 'LONG', NOW)
    assert report['by_tf']['W1']['status'] == 'НЕТ ДАННЫХ'
    assert report['available_tf'] == 3
    assert report['can_block_signal'] is False


def test_replay_never_sees_future_closed_bars_or_bad_unclosed_close():
    data = market()
    report = m.inspect(data, 'GBP/USD', 'LONG', NOW)
    early = m.inspect(data, 'GBP/USD', 'LONG', NOW - timedelta(hours=1))
    assert report['by_tf']['H1']['closed_at_utc'] == NOW.isoformat()
    assert early['by_tf']['H1']['closed_at_utc'] == (NOW - timedelta(hours=1)).isoformat()
    assert all(x['assessment'] == 'ПОДДЕРЖИВАЕТ' for x in early['by_tf'].values() if x['status'] == 'OK')


def test_wrong_direction_and_no_strength_veto():
    report = m.inspect(market(), 'EUR/USD', 'SHORT', NOW)
    assert report['opposed_tf'] == 4
    assert report['can_block_signal'] is False
    assert report['by_tf']['H4']['directed_gap_log_pp'] < 0


def test_bad_closed_close_flags_invalid_h1():
    data = market()
    data['EUR/USD']['H1'][-2].close = -1
    report = m.inspect(data, 'EUR/USD', 'LONG', NOW)
    assert report['by_tf']['H1']['status'] == 'НЕТ ДАННЫХ'
    assert report['available_tf'] == 3


def test_stale_timeframe_is_not_hidden_or_taken_as_confirmation():
    data = market()
    later = NOW + timedelta(hours=5)
    report = m.inspect(data, 'EUR/USD', 'LONG', later)
    assert report['by_tf']['H1']['status'] == 'НЕТ ДАННЫХ'
    assert report['by_tf']['W1']['status'] == 'OK'


def test_invalid_as_of_and_invalid_pair_dont_block():
    assert m.inspect(market(), 'EUR/USD', 'LONG', NOW.replace(tzinfo=None))['decision_authority'] is False
    assert m.inspect(market(), 'EUR/GBP', 'LONG', NOW)['status'] == 'НЕТ ДАННЫХ'


def test_mixed_higher_tf_conflict_is_never_called_full_confirmation():
    data = market()
    # Reverse only EUR/USD daily direction, leaving all TFs aligned in time.
    series = data['EUR/USD']['D1']
    for idx, bar in enumerate(series[:-1]):
        close = 1.1 * (0.997 ** idx)
        bar.open = bar.high = bar.low = bar.close = close
    report = m.inspect(data, 'EUR/USD', 'LONG', NOW)
    assert report['by_tf']['D1']['assessment'] == 'ПРОТИВ'
    assert report['available_tf'] == 4
    assert report['opposed_tf'] == 1
    assert report['status'] == 'СМЕШАННЫЙ ФОН'


def test_h1_anchor_prevents_post_signal_h4_lookahead():
    data = market()
    anchor = m.signal_h1_close(data, 'EUR/USD', NOW)
    assert anchor == NOW
    # The fixture already contains a H4 bar opened at 08:00 that closes at 12:00;
    # the intentionally invalid live close MUST be ignored at the 10:00 H1 anchor.
    report = m.inspect(data, 'EUR/USD', 'LONG', anchor)
    assert report['by_tf']['H4']['closed_at_utc'] == datetime(2026,11,5,8,tzinfo=timezone.utc).isoformat()
