from datetime import datetime, timedelta, timezone
import bpr
from analysis import Candle


def c(i,o,h,l,cl):
    dt=(datetime(2026,10,1,tzinfo=timezone.utc)+timedelta(hours=i)).isoformat()
    return Candle(dt,o,h,l,cl)


def _clean_bpr():
    bars=[]
    for i in range(24): bars.append(c(i,1.1000,1.1010,1.0990,1.1002))
    bars += [c(24,1.1000,1.1010,1.0995,1.1008), c(25,1.1010,1.1040,1.1008,1.1038), c(26,1.1030,1.1040,1.1020,1.1035)]
    bars += [c(27,1.1040,1.1050,1.1030,1.1045), c(28,1.1040,1.1042,1.1000,1.1002), c(29,1.1010,1.1015,1.1000,1.1005)]
    return bars


def test_bpr_records_newer_displacement_role():
    z=bpr._newest_bpr('EUR/USD','H1',_clean_bpr())
    assert z is not None
    assert z.expected_side == 'SHORT'
    assert z.clean is True


def test_standalone_alert_timeframes_are_h1_only():
    import config as cfg
    assert tuple(cfg.BPR_ALERT_TIMEFRAMES) == ('H1',)


def test_invalidation_is_closed_candle_and_buffered(monkeypatch):
    z=bpr._newest_bpr('EUR/USD','H1',_clean_bpr())
    assert z is not None and z.expected_side == 'SHORT'
    bars=_clean_bpr()+[c(30, z.high, z.high+.0020, z.high-.0001, z.high+.0015)]
    monkeypatch.setattr(bpr.ohlc_movement,'guard_event',lambda *a,**k:{'allow':True,'range_like':False,'details':{},'score':80})
    assert bpr._reaction(z,bars,{'H1':bars}) is None
    assert z.invalid is True
