from datetime import datetime, timedelta, timezone
from analysis import Candle
import mss


def _bars_bull_mss():
    start = datetime(2026, 9, 10, tzinfo=timezone.utc)
    out=[]
    price=1.1100
    # neutral history
    for i in range(30):
        o=price; c=o + (0.00015 if i%2==0 else -0.00010)
        out.append(Candle((start+timedelta(hours=i)).strftime('%Y-%m-%d %H:%M:%S'), o, max(o,c)+0.00025, min(o,c)-0.00025, c)); price=c
    # previous 5, then recent 5: clear bearish character (LH + LL)
    prev=[(1.1100,1.1105,1.1095,1.1101),(1.1099,1.1104,1.1093,1.1096),(1.1096,1.1102,1.1091,1.1094),(1.1094,1.1100,1.1089,1.1092),(1.1092,1.1098,1.1087,1.1090)]
    recent=[(1.1090,1.1095,1.1084,1.1087),(1.1087,1.1093,1.1082,1.1085),(1.1085,1.1091,1.1080,1.1083),(1.1083,1.1089,1.1078,1.1081),(1.1081,1.1087,1.1076,1.1079)]
    for vals in prev+recent:
        i=len(out); out.append(Candle((start+timedelta(hours=i)).strftime('%Y-%m-%d %H:%M:%S'), *vals))
    # strong closed break above recent protected high 1.1095
    i=len(out); out.append(Candle((start+timedelta(hours=i)).strftime('%Y-%m-%d %H:%M:%S'),1.1080,1.1102,1.1079,1.1100))
    return out


def test_bullish_mss_confirmed_and_aligned():
    ctx=mss.analyze_symbol('EUR/USD', {'H1': _bars_bull_mss()}, 1)
    assert ctx is not None
    assert ctx.alignment == 1
    assert ctx.side == 1
    assert ctx.timeframe == 'H1'
    assert ctx.level == 1.1095


def test_same_mss_conflicts_with_short_candidate():
    ctx=mss.analyze_symbol('EUR/USD', {'H1': _bars_bull_mss()}, -1)
    assert ctx is not None and ctx.alignment == -1
