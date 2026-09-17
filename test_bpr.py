from datetime import datetime, timedelta, timezone
import bpr
from analysis import Candle


def c(i,o,h,l,cl):
    dt=(datetime(2026,9,17,tzinfo=timezone.utc)+timedelta(minutes=15*i)).isoformat()
    return Candle(dt,o,h,l,cl)


def test_opposite_fvg_overlap_builds_internal_zone():
    bars=[]; p=1.1000
    for i in range(24):
        bars.append(c(i,p,p+.0010,p-.0010,p+.0002)); p+=.00005
    # Explicit bullish FVG then bearish FVG whose gap overlaps it.
    bars += [c(24,1.1000,1.1010,1.0995,1.1008), c(25,1.1010,1.1040,1.1008,1.1038), c(26,1.1030,1.1040,1.1020,1.1035)]
    bars += [c(27,1.1040,1.1050,1.1030,1.1045), c(28,1.1040,1.1042,1.1000,1.1002), c(29,1.1010,1.1015,1.1000,1.1005)]
    z=bpr._newest_bpr('EUR/USD','M15',bars)
    assert z is not None and z.low < z.high


def test_formation_message_is_not_a_trade_event():
    z=bpr.BPRZone('x','EUR/USD','M15',1.1,1.101,'2026','a','b',.2)
    assert z.delivered_side == '' and z.status == 'ЗОНА СФОРМИРОВАНА'
