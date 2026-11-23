"""Patterns 2.0 - conservative closed-H1 source/coverage protections."""
from datetime import datetime, timedelta, timezone

import patterns_history as ph
from patterns_history_integrity import audit_h1, missing_core_hours
from analysis import Candle

UTC = timezone.utc


def candle(t, value=100.0):
    return Candle(t.isoformat(), value, value + .2, value - .2, value)


def hours(start, n):
    return [candle(start + timedelta(hours=i)) for i in range(n)]


def test_consecutive_calendar_hours_across_weekend_are_not_trading_h1():
    start = datetime(2026, 11, 5, 13, tzinfo=UTC)
    data = hours(start, 357)
    report = audit_h1(data, as_of=datetime(2026, 11, 20, 12, tzinfo=UTC))
    assert report.rows == 357
    assert report.weekend >= 48  # at least two complete Saturdays
    assert report.blocks_replay
    assert 'сомнительных выходных' in report.describe()


def test_h1_weekend_source_blocks_historical_claims_in_command():
    start = datetime(2026, 11, 5, 13, tzinfo=UTC)
    data = hours(start, 357)
    report = ph.format_report('EUR/USD', {'H1': data}, use_archive=False,
                              max_steps=5, detectors=())
    assert 'H1_ЦЕЛОСТНОСТЬ_НЕ_ПОДТВЕРЖДЕНА' in report
    assert 'H1: 0 обнаружений' in report
    assert 'сомнительных выходных' in report


def test_h1_duplicate_and_off_hour_grid_fail_closed():
    start = datetime(2026, 11, 16, tzinfo=UTC)
    rows = hours(start, 20)
    rows.append(rows[-1])
    rows.append(candle(start + timedelta(hours=21, minutes=15)))
    result = audit_h1(rows, as_of=datetime(2026, 11, 18, tzinfo=UTC))
    assert result.duplicate == 1
    assert result.off_grid == 1
    assert result.blocks_replay


def test_unclosed_last_live_bar_is_ignored_not_misreported(monkeypatch):
    # A moving 25-hour lookback includes Saturday/Sunday on Mondays. That
    # tests the weekend quarantine instead of the unfinished-bar rule and
    # fails depending on the weekday of GitHub Actions. Freeze a trading-day
    # clock so both protections remain independently testable.
    import analysis
    import patterns_history_integrity as integrity

    clock = datetime(2026, 11, 18, 12, 30, tzinfo=UTC)  # Wednesday, UTC

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.astimezone(tz) if tz is not None else clock.replace(tzinfo=None)

    monkeypatch.setattr(analysis, 'datetime', FrozenDateTime)
    monkeypatch.setattr(integrity, 'datetime', FrozenDateTime)
    latest_open = clock.replace(minute=0, second=0, microsecond=0)
    live = hours(latest_open - timedelta(hours=25), 26)
    assert len(analysis.closed_candles(live, 60)) == 25

    report = ph.format_report('EUR/USD', {'H1': live}, max_steps=5,
                              use_archive=False, detectors=())
    assert 'H1_ЦЕЛОСТНОСТЬ_НЕ_ПОДТВЕРЖДЕНА' not in report
    assert 'Покрытие H1: 25 закрытых свечей' in report


def test_real_weekend_gap_is_not_fabricated_and_not_missing_core_hours():
    fri = datetime(2026, 11, 13, 19, tzinfo=UTC)
    sun = datetime(2026, 11, 15, 22, tzinfo=UTC)
    assert missing_core_hours(fri, sun) == 0
    # Weekend bar must not be created, only the actual stored H1 are counted.
    h1 = [candle(fri-timedelta(hours=1)), candle(fri), candle(sun),
          candle(sun+timedelta(hours=1)), candle(sun+timedelta(hours=2))]
    count, complete = ph._actual_closed_h1_count(h1, fri-timedelta(hours=1),
                                                   sun+timedelta(hours=3))
    assert complete and count == 5


def test_missing_midweek_h1_makes_formation_incomplete():
    monday = datetime(2026, 11, 16, tzinfo=UTC)
    data = hours(monday, 8)
    data.pop(4)
    assert missing_core_hours(monday+timedelta(hours=3), monday+timedelta(hours=5)) == 1
    assert ph._actual_closed_h1_count(data, monday, monday+timedelta(hours=8)) == (None, False)
    report = audit_h1(data, as_of=monday+timedelta(days=3))
    assert report.missing_core == 1
    assert report.gap_intervals == 1
    assert not report.blocks_replay  # affected cases excluded, not all history


def test_a_short_gap_before_new_week_is_not_missing_atr_data():
    # Do not treat a normal FX weekend closure as unexplained missing prices.
    start = datetime(2026, 11, 12, 10, tzinfo=UTC)
    before = hours(start, 35)  # to Friday 20:00
    sun = datetime(2026, 11, 15, 22, tzinfo=UTC)
    after = hours(sun, 10)
    outcome = ph._h1_atr_coverage_reason(before+after, sun+timedelta(hours=2), 100)
    assert outcome == ''


def test_h1_gap_in_atr_is_reported_not_silently_smoothed():
    start = datetime(2026, 11, 16, tzinfo=UTC)
    bars = hours(start, 35)
    bars.pop(27)
    reason = ph._h1_atr_coverage_reason(bars, start+timedelta(hours=35), 100)
    assert reason == 'H1_ПРОБЕЛ_В_ОКНЕ_ATR'


def test_single_missing_hour_after_confirmation_is_not_ignored():
    from patterns import Pattern
    start = datetime(2026, 11, 16, tzinfo=UTC)
    rows = hours(start, 35)
    confirmed = start + timedelta(hours=26)
    # H1 for opening 27:00 is missing; subsequent H1 must NOT be treated as the
    # next uninterrupted candle just because the gap is under 4 hours.
    rows.pop(27)
    p = Pattern('Тест', 'LONG', 'H1', 80, 80, '', 100, confirmed.isoformat())
    result = ph._evaluate(p, confirmed, rows, 100, 1, 1, True)
    assert result.outcome == 'INCOMPLETE'
    assert result.coverage_reason == 'H1_РАЗРЫВ_В_ГОРИЗОНТЕ'


def test_read_only_archive_live_conflict_is_visible_and_blocks_claim(monkeypatch, tmp_path):
    import sqlite3
    import ohlc_history_journal

    filepath = tmp_path/'forex.sqlite'
    start = datetime(2026, 11, 16, tzinfo=UTC)
    with sqlite3.connect(filepath) as db:
        db.execute('CREATE TABLE candles (pair TEXT, tf TEXT, opened_utc TEXT, open REAL, high REAL, low REAL, close REAL)')
        db.executemany('INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?)', [
            ('EUR/USD', 'H1', (start+timedelta(hours=i)).isoformat(), 100, 100.2, 99.8, 100)
            for i in range(30)
        ])
    monkeypatch.setattr(ohlc_history_journal, 'db_default', lambda: filepath)
    live = hours(start+timedelta(hours=23), 7)
    live[2] = candle(start+timedelta(hours=25), 101)
    report = ph.format_report('EUR/USD', {'H1':live}, detectors=(), max_steps=5)
    assert 'H1_КОНФЛИКТ_АРХИВА_И_КЕША: 1' in report
    assert 'H1_ЦЕЛОСТНОСТЬ_НЕ_ПОДТВЕРЖДЕНА' in report
    with sqlite3.connect(filepath) as db:
        assert db.execute('SELECT close FROM candles WHERE opened_utc=?',
                          ((start+timedelta(hours=25)).isoformat(),)).fetchone()[0] == 100
