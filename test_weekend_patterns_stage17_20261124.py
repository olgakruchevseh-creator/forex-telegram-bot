"""Stage 17: vendor weekend quotes, double top/bottom search, 1-2-3 window option,
Friday signal journal horizon, Sunday-open week bucketing.

Pattern tests build a market AND its price mirror: correct geometry must give
the mirrored answer (LONG <-> SHORT).
"""
from datetime import datetime, timedelta, timezone

import config as cfg
import market_hours
import market_data_coherence
import patterns
import signal_journal as sj
import weekly_rhythm_context as wr
from analysis import Candle
from test_geometry_patterns_stage16_20261124 import UNIT, _hits, _mirror, _zig

UTC = timezone.utc


# --- A. Weekend calendar ----------------------------------------------------

def test_intraday_weekend_bars_inside_closure_only():
    # Fri 20.11.2026 / Sun 22.11.2026
    assert not market_hours.is_weekend_bar("2026-11-20 21:00:00", "H1")   # last trading hour
    assert market_hours.is_weekend_bar("2026-11-20 22:00:00", "H1")
    assert market_hours.is_weekend_bar("2026-11-21 13:00:00", "H1")
    assert market_hours.is_weekend_bar("2026-11-22 20:00:00", "H1")
    assert not market_hours.is_weekend_bar("2026-11-22 21:00:00", "H1")   # reopening hour
    assert not market_hours.is_weekend_bar("2026-11-20 20:00:00", "H4")   # overlaps 20-22
    assert market_hours.is_weekend_bar("2026-11-21 00:00:00", "H4")
    assert market_hours.is_weekend_bar("2026-11-22 16:00:00", "H4")
    assert not market_hours.is_weekend_bar("2026-11-22 20:00:00", "H4")   # overlaps 21-24
    assert market_hours.is_weekend_bar("2026-11-22 20:55:00", "M5")
    assert not market_hours.is_weekend_bar("2026-11-22 21:00:00", "M5")


def test_d1_saturday_sunday_labels_dropped_w1_kept():
    for label in ("2026-11-21", "2026-11-22"):
        assert market_hours.is_weekend_bar(label, "D1")
    for label in ("2026-11-20", "2026-11-23"):
        assert not market_hours.is_weekend_bar(label, "D1")
    assert not market_hours.is_weekend_bar("2026-11-22", "W1")


def test_moving_weekend_bars_leave_live_view_but_stay_raw(monkeypatch):
    import bot
    raw_h1 = [Candle("2026-11-20 21:00:00", 1, 1.01, .99, 1.0),
              Candle("2026-11-21 10:00:00", 1, 1.02, .98, 1.01),    # moving weekend quote
              Candle("2026-11-22 21:00:00", 1, 1.02, .99, 1.01)]
    raw_d1 = [Candle("2026-11-20", 1, 1.1, .9, 1), Candle("2026-11-21", 1, 1.05, .95, 1),
              Candle("2026-11-22", 1, 1.04, .96, 1), Candle("2026-11-23", 1, 1.1, .9, 1)]
    monkeypatch.setattr(cfg, "PAIRS", ["EUR/USD"])
    monkeypatch.setattr(cfg, "TIMEFRAMES", [{"key": "H1", "ttl_min": 1}, {"key": "D1", "ttl_min": 1}])
    monkeypatch.setattr(cfg, "REQUEST_PAUSE_SEC", 0)
    monkeypatch.setattr(bot, "CACHE", {})
    monkeypatch.setattr(bot, "fetch_tf_batch",
                        lambda tf, key: {"EUR/USD": list(raw_h1 if tf["key"] == "H1" else raw_d1)})
    market = bot.fetch_market("k")
    assert [c.dt for c in market["EUR/USD"]["H1"]] == [raw_h1[0].dt, raw_h1[2].dt]
    assert [c.dt for c in market["EUR/USD"]["D1"]] == ["2026-11-20", "2026-11-23"]
    assert market.dropped_weekend[("EUR/USD", "H1")] == 1
    assert market.dropped_weekend[("EUR/USD", "D1")] == 2
    assert market.raw_by_series[("EUR/USD", "H1")] == raw_h1
    assert bot.CACHE[("EUR/USD", "H1")]["candles"] == raw_h1


def test_weekend_filter_switch_off(monkeypatch):
    import bot
    raw = [Candle("2026-11-21 10:00:00", 1, 1.02, .98, 1.01)]
    monkeypatch.setattr(cfg, "PAIRS", ["EUR/USD"])
    monkeypatch.setattr(cfg, "TIMEFRAMES", [{"key": "H1", "ttl_min": 1}])
    monkeypatch.setattr(cfg, "REQUEST_PAUSE_SEC", 0)
    monkeypatch.setattr(cfg, "LIVE_DROP_WEEKEND_BARS", False)
    monkeypatch.setattr(bot, "CACHE", {})
    monkeypatch.setattr(bot, "fetch_tf_batch", lambda tf, key: {"EUR/USD": list(raw)})
    assert bot.fetch_market("k")["EUR/USD"]["H1"] == raw


def test_closed_market_minutes_do_not_age_friday_bar():
    closed = datetime(2026, 11, 20, 22, tzinfo=UTC)
    assert market_hours.trading_age_minutes(closed, datetime(2026, 11, 21, 12, tzinfo=UTC)) == 0
    assert market_hours.trading_age_minutes(closed, datetime(2026, 11, 22, 22, 30, tzinfo=UTC)) == 90
    assert market_hours.trading_age_minutes(datetime(2026, 11, 23, 9, tzinfo=UTC),
                                            datetime(2026, 11, 23, 10, tzinfo=UTC)) == 60


def _h1_basket(end_open: datetime, n: int = 40) -> dict:
    bars, t = [], end_open
    while len(bars) < n:
        if market_hours.is_trading_h1(t.isoformat()):
            bars.append(Candle(t.strftime("%Y-%m-%d %H:%M:%S"), 1.0, 1.001, .999, 1.0))
        t -= timedelta(hours=1)
    bars.reverse()
    return {pair: {"H1": list(bars)} for pair in cfg.PAIRS}


def test_coherence_keeps_friday_close_fresh_on_saturday(monkeypatch):
    market = _h1_basket(datetime(2026, 11, 20, 21, tzinfo=UTC))
    saturday = datetime(2026, 11, 21, 15, tzinfo=UTC)
    ok = market_data_coherence.inspect(market, as_of=saturday, required_tfs=("H1",))
    assert ok["ok"], ok["reasons"]
    monkeypatch.setattr(cfg, "LIVE_DROP_WEEKEND_BARS", False)
    old = market_data_coherence.inspect(market, as_of=saturday, required_tfs=("H1",))
    assert not old["ok"]   # previous behaviour: calendar age says "stale"
    monkeypatch.setattr(cfg, "LIVE_DROP_WEEKEND_BARS", True)
    monday = datetime(2026, 11, 23, 3, tzinfo=UTC)   # 6 trading hours without new H1
    assert not market_data_coherence.inspect(market, as_of=monday, required_tfs=("H1",))["ok"]


# --- B. Double top / bottom ---------------------------------------------------

def test_double_top_found_when_third_near_equal_top_follows_small_dip():
    # Tops 1 and 2 at +8 with a deep neckline at +2; then a small dip (+7.2)
    # and a third top (+7.9). The (2,3) pair used to be returned first and
    # failed; the real formation was never examined.
    bars = _zig([8, 2, 8, (7.2, 3), (7.9, 3), (-6, 10)])
    top = _hits(patterns.structural_patterns, bars, "Двойная вершина")
    bottom = _hits(patterns.structural_patterns, _mirror(bars), "Двойное дно")
    assert top and top[0][1] == "SHORT"
    assert bottom and bottom[0][1] == "LONG"
    assert top[0][0] == bottom[0][0] and top[0][2] == bottom[0][2]
    assert abs((top[0][3] - 1.1) / UNIT - 2.0) < 0.5     # deep neckline, not the small dip


def test_double_top_clean_shape_still_single_signal():
    bars = _zig([8, 2, 8, (-6, 12)])
    assert len(_hits(patterns.structural_patterns, bars, "Двойная вершина")) == 1
    assert len(_hits(patterns.structural_patterns, _mirror(bars), "Двойное дно")) == 1


# --- C. 1-2-3 window option ---------------------------------------------------

def test_123_window_from_last_inner_option(monkeypatch):
    bars = _zig([-6, 2, (-2, 4), (0, 4), (-1, 4), (5, 4)])
    monkeypatch.setattr(cfg, "PATTERN_123_WINDOW_FROM_LAST_INNER", False)
    assert not _hits(patterns.pattern_123, bars, "Паттерн 1-2-3")       # default unchanged
    monkeypatch.setattr(cfg, "PATTERN_123_WINDOW_FROM_LAST_INNER", True)
    long_hits = _hits(patterns.pattern_123, bars, "Паттерн 1-2-3")
    short_hits = _hits(patterns.pattern_123, _mirror(bars), "Паттерн 1-2-3")
    assert long_hits and long_hits[0][1] == "LONG"
    assert short_hits and short_hits[0][1] == "SHORT"
    assert long_hits[0][0] == short_hits[0][0]
    assert abs((long_hits[0][3] - 1.1) / UNIT - 2.0) < 0.5   # real P2


# --- D. Signal journal across the weekend ---------------------------------------

def _record(entry_dt):
    return {"side": "LONG", "entry_dt": entry_dt, "entry": 100.0, "atr": 1.0,
            "target": 105.0, "invalidation": 95.0, "status": "OPEN", "resolved_dt": "", "horizons": {}}


def test_friday_evening_record_matures_after_reopening():
    rec = _record("2026-11-20 19:00:00")
    t = datetime(2026, 11, 20, 20, tzinfo=UTC)
    bars = []
    while len(bars) < 8:
        if market_hours.is_trading_h1(t.isoformat()):
            bars.append(Candle(t.strftime("%Y-%m-%d %H:%M:%S"), 100, 100.5, 99.5, 100.1))
        t += timedelta(hours=1)
    sj._update_record(rec, bars)
    assert rec["status"] == "CLOSED_8H"
    assert rec["resolved_dt"] == "2026-11-23 02:00:00"   # Fri 20,21 + Sun 21,22,23 + Mon 00,01,02


def test_vendor_weekend_bars_are_not_outcome_bars():
    rec = _record("2026-11-20 21:00:00")
    bars = [Candle("2026-11-21 03:00:00", 100, 120, 99, 110)]   # weekend quote would hit target
    bars += [Candle(f"2026-11-22 {h}:00:00", 100, 100.5, 99.5, 100) for h in (21, 22, 23)]
    sj._update_record(rec, bars)
    assert rec["status"] == "OPEN"


def test_missing_trading_hour_still_stops_the_horizon():
    rec = _record("2026-11-23 10:00:00")
    bars = [Candle(f"2026-11-23 {h}:00:00", 100, 100.5, 99.5, 100) for h in (11, 12, 14, 15, 16, 17, 18, 19)]
    sj._update_record(rec, bars)
    assert rec["status"] == "OPEN" and rec.get("horizons", {}).get("1") is not None
    assert "4" not in rec["horizons"]


# --- E. Sunday open belongs to the new week -----------------------------------

def test_sunday_open_h4_is_monday_of_new_week():
    sunday = datetime(2026, 11, 22, 20, tzinfo=UTC)
    assert market_hours.market_day(sunday).isocalendar()[:3] == (2026, 48, 1)
    assert market_hours.market_day(datetime(2026, 11, 22, 16, tzinfo=UTC)).isocalendar()[1] == 47
    assert wr._week_key(Candle("2026-11-22 20:00:00", 1, 1, 1, 1)) == (2026, 48)
    assert wr._week_key(Candle("2026-11-20 20:00:00", 1, 1, 1, 1)) == (2026, 47)


def test_weekly_rhythm_week_open_is_sunday_bar():
    bars = []
    t = datetime(2026, 11, 16, 0, tzinfo=UTC)
    price = 1.10
    while t <= datetime(2026, 11, 23, 8, tzinfo=UTC):
        if not market_hours.is_weekend_bar(t.isoformat(), "H4"):
            bars.append(Candle(t.strftime("%Y-%m-%d %H:%M:%S"), price, price + .001, price - .001, price + .0002))
            price += .0002
        t += timedelta(hours=4)
    sunday_open = next(b for b in bars if b.dt.startswith("2026-11-22 20"))
    ctx = wr.analyze_symbol("EUR/USD", {"H4": bars}, now_utc=datetime(2026, 11, 23, 12, 1, tzinfo=UTC))
    assert ctx is not None
    assert abs(ctx.weekly_open - sunday_open.open) < 1e-12
    assert ctx.weekday == 1
