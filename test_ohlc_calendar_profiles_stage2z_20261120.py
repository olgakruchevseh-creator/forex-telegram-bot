"""Stage 2Z: supplier 24/7 aggregate versus conservative 24/5 replay view."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analysis import Candle
from ohlc_calendar_profiles import profile_h1, format_calendar_lines, compare_atr_sensitivity
from patterns_history_integrity import definitely_closed_fx, definitely_trading_fx
import ohlc_origin_report as origin
import ohlc_history_journal as journal
import sqlite3

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 13, tzinfo=UTC)


def candle(when, price=1.10, *, flat=False):
    return Candle(when.isoformat(), price,
                  price if flat else price + .0004,
                  price if flat else price - .0003, price)


def test_two_calendars_preserve_weekend_supplier_bars():
    saturday = datetime(2026, 11, 7, 0, tzinfo=UTC)
    monday = datetime(2026, 11, 9, 0, tzinfo=UTC)
    sun_edge = datetime(2026, 11, 8, 21, tzinfo=UTC)
    supplied = [candle(saturday), candle(saturday + timedelta(hours=1), flat=True),
                candle(sun_edge), candle(monday)]
    snapshot = [(c.dt, c.open, c.high, c.low, c.close) for c in supplied]
    report = profile_h1(supplied, as_of=NOW)
    assert report.rows == 4
    assert report.source_weekend == 2
    assert report.nonflat_closed_market == 1
    assert report.flat_closed_market == 1
    assert report.edge_hours == 1
    assert report.core_trade_hours == 1
    assert report.excluded_trade_hours == 2
    assert [(c.dt, c.open, c.high, c.low, c.close) for c in supplied] == snapshot
    assert definitely_closed_fx(saturday)
    assert not definitely_closed_fx(sun_edge)
    assert not definitely_trading_fx(sun_edge)


def test_read_only_report_labels_time_basis_and_no_proof_of_tradeability():
    sat = datetime(2026, 11, 7, tzinfo=UTC)
    lines = '\n'.join(format_calendar_lines([candle(sat)], [candle(sat)]))
    assert 'ПОСТАВЩИК' in lines and 'ТОРГОВЫЙ' in lines
    assert 'наличие котировки не означает возможность сделки' in lines
    assert 'timezone=UTC' in lines
    assert 'D1/W1' in lines
    assert 'не точный календарь AvaTrade' in lines


def test_raw_vs_calendar_filtered_atr_is_observation_only():
    start = datetime(2026, 11, 12, tzinfo=UTC)
    cache = [candle(start + timedelta(hours=i), 1.10 + i*.00001)
             for i in range(8*24)]
    before = [(c.dt, c.close) for c in cache]
    result = compare_atr_sensitivity(cache)
    assert result is not None
    assert all(value > 0 for value in result)
    assert [(c.dt, c.close) for c in cache] == before
    assert 'НЕ утверждение о фактическом ATR' in '\n'.join(format_calendar_lines(cache, []))


def test_origin_report_uses_categorized_disputes_without_writing(tmp_path):
    path = tmp_path / 'history.sqlite'
    monday = datetime(2026, 11, 16, 13, tzinfo=UTC)
    saturday = datetime(2026, 11, 7, tzinfo=UTC)
    with journal.init_db(path) as db:
        for t in (monday, saturday):
            db.execute('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?)',
                       ('EUR/USD', 'H1', journal.iso(t), 1.1, 1.1004, 1.0997, 1.1,
                        'BOT_SCAN_API_RESPONSE', '2026-11-16T14:00:00Z',
                        '2026-11-16T14:00:00Z'))
        db.commit()
    snapshot = path.read_bytes()
    cache = [candle(monday, 1.1001), candle(saturday, 1.1001)]
    result = origin.analyze('EUR/USD', db_path=path, cache=cache, as_of=NOW)
    assert result['conflict_count'] == 2
    assert result['disputed_calendar']['ОСНОВНЫЕ_ТОРГОВЫЕ_ЧАСЫ'] == 1
    assert result['disputed_calendar']['ВНЕ_ОБЫЧНОЙ_СЕССИИ'] == 1
    report = origin.format_report('EUR/USD', db_path=path, cache=cache, as_of=NOW)
    assert 'H1 конфликтные метки по торговому календарю' in report
    assert 'вне обычной торговой сессии Forex' in report
    assert path.read_bytes() == snapshot


def test_live_source_and_historical_policy_are_separate():
    root = Path(__file__).parent
    bot = (root/'bot.py').read_text(encoding='utf-8')
    history = (root/'patterns_history_sources.py').read_text(encoding='utf-8')
    assert '"timezone": "UTC"' in bot
    assert 'CACHE[(s, tf["key"])]' in bot  # direct transport into current memory cache
    assert 'not definitely_closed_fx(ts)' in history  # only historical trade replay
    assert 'ohlc_calendar_profiles' not in bot  # no effect on live entry path
    assert 'from ohlc_calendar_profiles' not in history
