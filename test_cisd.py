from analysis import Candle
from datetime import datetime, timedelta, timezone
import cisd

def _c(o,h,l,cl,i):
    dt=datetime(2026,9,17,tzinfo=timezone.utc)+timedelta(hours=i)
    return Candle(dt.isoformat(),o,h,l,cl)

def test_cisd_never_standalone_and_detects_bullish_sweep_reclaim():
    bars=[]; p=1.1000
    for i in range(30):
        o=p; cl=p+(0.00012 if i%2==0 else -0.00005); bars.append(_c(o,max(o,cl)+.00012,min(o,cl)-.00012,cl,i)); p=cl
    # establish reference then raid below it, bearish delivery anchor, bullish CISD close above anchor open
    bars += [_c(1.1010,1.1012,1.1007,1.1008,30), _c(1.1008,1.1009,1.1002,1.10035,31),
             _c(1.10035,1.1005,1.0995,1.1000,32), _c(1.1000,1.1014,1.0999,1.1013,33)]
    ctx=cisd.analyze_symbol('EUR/USD',{'H1':bars},1)
    assert ctx is not None and ctx.side==1 and ctx.family=='structure_delivery_shift'
    assert not hasattr(cisd,'process_candidates')

def test_cisd_requires_liquidity_raid():
    bars=[]; p=1.1000
    for i in range(35):
        o=p; cl=p+(.00005 if i%2==0 else -.00004); bars.append(_c(o,max(o,cl)+.0001,min(o,cl)-.0001,cl,i)); p=cl
    assert cisd.analyze_symbol('EUR/USD',{'H1':bars},1) is None
