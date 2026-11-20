"""Patterns 2.0: verified H1 coverage, conservative gaps, optional bounded archive."""
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

import patterns_history as ph
from analysis import Candle
from patterns import Pattern


def _bar(t, v=100):
    return Candle(t.isoformat(), v, v + .2, v - .2, v)


def _series(start, count):
    return [_bar(start + timedelta(hours=i)) for i in range(count)]


def _pattern(tf='H1'):
    return Pattern('Тест', 'LONG', tf, 90, 88, '', 100, '2026-11-01T00:00:00+00:00')


def test_weekly_and_daily_without_matching_h1_get_explicit_reason():
    h1 = _series(datetime(2026, 11, 10, tzinfo=timezone.utc), 45)
    w1 = ph._evaluate(_pattern('W1'), datetime(2026, 10, 3, tzinfo=timezone.utc), h1, 100, 2, None, False)
    d1 = ph._evaluate(_pattern('D1'), datetime(2026, 10, 9, tzinfo=timezone.utc), h1, 100, 2, None, False)
    assert (w1.outcome, d1.outcome) == ('NO_H1', 'NO_H1')
    assert w1.coverage_reason == 'H1_ИСТОРИЯ_НАЧИНАЕТСЯ_ПОЗЖЕ'


def test_inadequate_h1_formation_coverage_does_not_claim_partial_count():
    start = datetime(2026, 11, 11, tzinfo=timezone.utc)
    h1 = _series(start, 5)
    assert ph._actual_closed_h1_count(h1, start, start + timedelta(hours=10)) == (None, False)
    assert ph._actual_closed_h1_count(h1, start, start + timedelta(hours=5)) == (5, True)


def test_no_weekend_or_data_gap_jump_after_confirmation():
    start = datetime(2026, 11, 16, tzinfo=timezone.utc)
    h1 = _series(start, 20) + _series(start + timedelta(hours=75), 8)
    confirmed = start + timedelta(hours=20)
    case = ph._evaluate(_pattern(), confirmed, h1, 100, 1, 1, True)
    assert case.outcome == 'NO_H1'
    assert case.coverage_reason == 'H1_ПРОБЕЛ_ПОСЛЕ_ПОДТВЕРЖДЕНИЯ'


def test_h1_gap_inside_horizon_is_incomplete_not_time():
    start = datetime(2026, 11, 16, tzinfo=timezone.utc)
    h1 = _series(start, 20) + _series(start + timedelta(hours=27), 10)
    confirmed = start + timedelta(hours=19)
    # The first H1 is valid but no outcome; gap to next H1 invalidates full 8H1 evaluation.
    case = ph._evaluate(_pattern(), confirmed, h1, 100, 1, 1, True)
    assert case.outcome == 'INCOMPLETE'
    assert case.coverage_reason == 'H1_РАЗРЫВ_В_ГОРИЗОНТЕ'


def test_archive_is_bounded_read_only_and_does_not_override_live(monkeypatch, tmp_path):
    dbpath = tmp_path / 'history.sqlite'
    with sqlite3.connect(dbpath) as db:
        db.execute('CREATE TABLE candles (pair TEXT, tf TEXT, opened_utc TEXT, open REAL, high REAL, low REAL, close REAL)')
        start = datetime(2026, 11, 10, tzinfo=timezone.utc)
        for i in range(4):
            db.execute('INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?)',
                       ('EUR/USD', 'H1', (start + timedelta(hours=i)).isoformat(), 100, 100.2, 99.8, 100))
    import ohlc_history_journal
    monkeypatch.setattr(ohlc_history_journal, 'db_default', lambda: dbpath)
    start = datetime(2026, 11, 10, tzinfo=timezone.utc)
    current = [_bar(start + timedelta(hours=3), 105), _bar(start + timedelta(hours=4), 105)]
    merged, gained = ph._with_optional_h1_archive('EUR/USD', {'H1': current}, use_archive=True)
    assert gained == 3
    assert len(merged['H1']) == 5
    assert merged['H1'][-2].close == 105  # live wins on overlap
    assert sqlite3.connect(dbpath).execute('select count(*) from candles').fetchone()[0] == 4
    assert ph._archive_h1('EUR/USD', limit=2)[0].dt.endswith('02:00:00+00:00')


def test_report_explains_absent_h1_and_no_fabricated_probability():
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    # Only a limited recent H1 history and an older W1 pattern.
    h1 = _series(now - timedelta(hours=35), 35)
    w1 = [_bar(now-timedelta(weeks=i)) for i in reversed(range(45))]
    def detector(tf, bars):
        if tf == 'W1' and len(bars) == 42:
            return [Pattern('Бычий пример', 'LONG', tf, 88, 83, '', 100, bars[-1].dt)]
        return []
    msg = ph.format_report('EUR/USD', {'H1':h1,'W1':w1}, detectors=(detector,),
                           max_steps=16, use_archive=False)
    assert 'Покрытие H1:' in msg
    assert 'H1_ИСТОРИЯ_НАЧИНАЕТСЯ_ПОЗЖЕ' in msg
    assert 'нет H1' in msg
    assert 'НЕ вероятность' in msg
