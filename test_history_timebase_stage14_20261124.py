"""Stage 14: history pipeline — flat weekend bars and vendor H4/D1/W1 time basis."""
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import random

import pytest

import config as cfg
from analysis import Candle, atr
import ohlc_timebase_probe as probe


def _trading(ts):
    wd = ts.weekday()
    return not (wd == 5 or (wd == 4 and ts.hour >= 22) or (wd == 6 and ts.hour < 22))


def _h1(end, days, *, weekend_flat=False, seed=7):
    rnd, px, out = random.Random(seed), 1.1, []
    t = (end - timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    while t < end:
        if _trading(t):
            o = px; c = o + rnd.gauss(0, .0012)
            out.append(Candle(t.strftime("%Y-%m-%d %H:%M:%S"), o,
                              max(o, c) + abs(rnd.gauss(0, .0006)), min(o, c) - abs(rnd.gauss(0, .0006)), c))
            px = c
        elif weekend_flat:
            out.append(Candle(t.strftime("%Y-%m-%d %H:%M:%S"), px, px, px, px))
        t += timedelta(hours=1)
    return out


def _agg(h1, span_h, offset_h, fmt):
    groups = OrderedDict()
    for b in h1:
        ts = datetime.fromisoformat(b.dt).replace(tzinfo=timezone.utc) - timedelta(hours=offset_h)
        if span_h == 24:
            base = ts.replace(hour=0)
        elif span_h == 168:
            base = (ts - timedelta(days=ts.weekday())).replace(hour=0)
        else:
            base = ts.replace(hour=ts.hour - ts.hour % span_h)
        groups.setdefault(base, []).append(b)
    return [Candle(k.strftime(fmt), v[0].open, max(x.high for x in v), min(x.low for x in v), v[-1].close)
            for k, v in groups.items()]


END = datetime(2026, 11, 24, 16, 5, tzinfo=timezone.utc)


# ------------------------------------------------------------ time basis
@pytest.mark.parametrize("offset,verdict", [(0, "UTC_ALIGNED"), (-11, "CONSERVATIVE_LAG"),
                                            (-2, "CONSERVATIVE_LAG"), (6, "LOOKAHEAD_RISK")])
def test_d1_start_offset_is_recovered_from_h1(offset, verdict):
    h1 = _h1(END, 45)
    d1 = _agg(h1, 24, offset, "%Y-%m-%d")
    r = probe.infer_offset(h1[-250:], d1, "D1", as_of=END)
    assert r["best_offsets_h"] == [offset] and r["verdict"] == verdict and r["match_ratio"] == 1.0


def test_h4_and_w1_are_checked_and_w1_needs_verified_h4():
    h1 = _h1(END, 45, weekend_flat=True)
    by_tf = {"H1": h1[-250:], "H4": _agg(h1, 4, 0, "%Y-%m-%d %H:%M:%S"),
             "D1": _agg(h1, 24, 0, "%Y-%m-%d"), "W1": _agg(h1, 168, 0, "%Y-%m-%d")}
    r = probe.probe_pair(by_tf, as_of=END)
    assert r["H4"]["verdict"] == "UTC_ALIGNED" and r["W1"]["verdict"] == "UTC_ALIGNED"
    assert r["weekend_h1"]["flat"] > 0 and r["weekend_h1"]["moving"] == 0
    shifted = dict(by_tf, H4=_agg(h1, 4, 1, "%Y-%m-%d %H:%M:%S"))
    assert probe.probe_pair(shifted, as_of=END)["W1"]["verdict"] == "H4_NOT_VERIFIED"


def test_report_needs_no_api_and_handles_missing_pairs():
    h1 = _h1(END, 45)
    market = {"EUR/USD": {"H1": h1[-250:], "D1": _agg(h1, 24, 0, "%Y-%m-%d")}}
    text = probe.format_report(market, ["EUR/USD", "GBP/USD"])
    assert "EUR/USD" in text and "GBP/USD" in text and "D1: сдвиг начала +0 ч" in text


# ---------------------------------------------- flat weekend placeholders
def test_flat_weekend_bars_shrink_monday_atr_and_filter_restores_it():
    import bot
    with_flat = _h1(datetime(2026, 11, 23, 0, tzinfo=timezone.utc), 14, weekend_flat=True)
    without = _h1(datetime(2026, 11, 23, 0, tzinfo=timezone.utc), 14, weekend_flat=False)
    assert atr(with_flat, 14) < atr(without, 14) / 5          # the defect
    assert bot.drop_flat_weekend_bars(with_flat) == without   # exactly the real bars remain


def test_moving_weekend_and_weekday_bars_are_never_dropped():
    import bot
    bars = [Candle("2026-11-21 10:00:00", 1, 1.001, .999, 1.0005),   # Saturday, moving: kept
            Candle("2026-11-22 22:00:00", 1, 1, 1, 1),                # Sunday 22 UTC open, flat: kept
            Candle("2026-11-23 03:00:00", 1, 1, 1, 1),                # Monday flat: kept
            Candle("2026-11-22 10:00:00", 1, 1, 1, 1),                # Sunday 10 UTC flat: dropped
            Candle("2026-11-20 21:00:00", 1, 1, 1, 1)]                # Friday 21 UTC flat: dropped
    assert bot.drop_flat_weekend_bars(bars) == bars[:3]


def test_fetch_market_live_view_filtered_cache_and_archive_raw(monkeypatch, tmp_path):
    import bot
    from ohlc_history_journal import capture_market, init_db
    raw = [Candle("2026-11-20 20:00:00", 1, 1.01, .99, 1.0),
           Candle("2026-11-21 00:00:00", 1, 1, 1, 1),
           Candle("2026-11-23 00:00:00", 1, 1.02, .98, 1.01)]
    monkeypatch.setattr(cfg, "PAIRS", ["EUR/USD"])
    monkeypatch.setattr(cfg, "TIMEFRAMES", [{"key": "H1", "ttl_min": 1}])
    monkeypatch.setattr(cfg, "REQUEST_PAUSE_SEC", 0)
    monkeypatch.setattr(bot, "CACHE", {})
    monkeypatch.setattr(bot, "fetch_tf_batch", lambda tf, key: {"EUR/USD": list(raw)})
    market = bot.fetch_market("k")
    assert [c.dt for c in market["EUR/USD"]["H1"]] == [raw[0].dt, raw[2].dt]
    assert bot.CACHE[("EUR/USD", "H1")]["candles"] == raw
    assert market.dropped_flat_weekend[("EUR/USD", "H1")] == 1
    db = tmp_path / "ohlc.sqlite"
    capture_market(market, db_path=db, as_of=datetime.now(timezone.utc) + timedelta(seconds=1),
                   timeframes=("H1",))
    with init_db(db) as conn:
        stored = [r[0] for r in conn.execute("SELECT opened_utc FROM candles ORDER BY opened_utc")]
    assert "2026-11-21T00:00:00Z" in stored   # archive keeps the vendor placeholder


def test_filter_can_be_switched_off(monkeypatch):
    import bot
    raw = [Candle("2026-11-21 00:00:00", 1, 1, 1, 1)]
    monkeypatch.setattr(cfg, "PAIRS", ["EUR/USD"])
    monkeypatch.setattr(cfg, "TIMEFRAMES", [{"key": "H1", "ttl_min": 1}])
    monkeypatch.setattr(cfg, "REQUEST_PAUSE_SEC", 0)
    monkeypatch.setattr(cfg, "LIVE_DROP_FLAT_WEEKEND_BARS", False)
    monkeypatch.setattr(bot, "CACHE", {})
    monkeypatch.setattr(bot, "fetch_tf_batch", lambda tf, key: {"EUR/USD": list(raw)})
    assert bot.fetch_market("k")["EUR/USD"]["H1"] == raw


def test_ohlc_timebase_command_is_registered():
    import bot
    src = open(bot.__file__, encoding="utf-8").read()
    assert 'CommandHandler("ohlc_timebase", cmd_ohlc_timebase)' in src
