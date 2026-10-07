"""Adaptive quantitative character profile for each FX pair.

Context only: it never creates LONG/SHORT and never vetoes an existing signal.
All metrics use closed H1 bars and are scale-free where possible so the seven
majors can be compared without hard-coded folklore about a pair.
"""
from __future__ import annotations

import math
from statistics import mean, pstdev, median
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import config as cfg

from analysis import atr, closed_candles


def _clip(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(x)))


def _corr(a, b):
    n=min(len(a),len(b))
    if n < 8: return 0.0
    a,b=a[-n:],b[-n:]; ma,mb=mean(a),mean(b)
    da=[x-ma for x in a]; db=[x-mb for x in b]
    den=math.sqrt(sum(x*x for x in da)*sum(x*x for x in db))
    return sum(x*y for x,y in zip(da,db))/den if den else 0.0


def _efficiency(closes):
    if len(closes)<3: return 0.0
    path=sum(abs(b-a) for a,b in zip(closes,closes[1:]))
    return abs(closes[-1]-closes[0])/path if path else 0.0


def _hurst_proxy(closes, max_lag=12):
    """Small-sample log variogram slope; bounded context proxy, not an estimator claim."""
    if len(closes)<40: return 0.5
    xs=[]; ys=[]
    for lag in range(2,min(max_lag,len(closes)//4)+1):
        diffs=[closes[i]-closes[i-lag] for i in range(lag,len(closes))]
        sd=pstdev(diffs) if len(diffs)>1 else 0.0
        if sd>0: xs.append(math.log(lag)); ys.append(math.log(sd))
    if len(xs)<3: return 0.5
    mx,my=mean(xs),mean(ys); den=sum((x-mx)**2 for x in xs)
    slope=sum((x-mx)*(y-my) for x,y in zip(xs,ys))/den if den else .5
    return max(0.0,min(1.0,slope))


def _er(closes, n):
    if len(closes) <= n: return 0.0
    w=closes[-(n+1):]
    path=sum(abs(b-a) for a,b in zip(w,w[1:]))
    return abs(w[-1]-w[0])/path if path else 0.0

def _parse_dt(raw):
    try:
        d=datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if d.tzinfo is None: d=d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None

def _session_key(dt):
    if not dt: return "UNKNOWN"
    try: local=dt.astimezone(ZoneInfo(getattr(cfg,"LOCAL_TZ_NAME","Europe/Amsterdam")))
    except Exception: local=dt
    h=local.hour
    return "ASIA" if h < 9 else ("EUROPE" if h < 15 else "AMERICA")

def analyze(symbol: str, by_tf: dict, strength: dict | None=None) -> dict:
    bars=closed_candles((by_tf or {}).get('H1') or [],60)[-180:]
    if len(bars)<40:
        return {'symbol':symbol,'ready':False,'samples':len(bars),'label':'НЕДОСТАТОЧНО ДАННЫХ'}
    closes=[float(b.close) for b in bars]
    rets=[(b-a)/a for a,b in zip(closes,closes[1:]) if a]
    av=float(atr(bars,14) or 0.0); px=max(abs(closes[-1]),1e-12)
    atr_pct=100*av/px
    recent=closes[-25:]; eff=_efficiency(recent)
    # Kaufman ER at three horizons: short/session, day, multi-day character.
    er8, er24, er72 = _er(closes,8), _er(closes,24), _er(closes,72)
    er_blend = .25*er8 + .50*er24 + .25*er72
    ac1=_corr(rets[:-1],rets[1:]) if len(rets)>10 else 0.0
    h=_hurst_proxy(closes)
    # persistence blends geometry and return memory; negative autocorrelation
    # naturally shifts the pair toward mean-reversion.
    persistence=_clip(100*(.34*eff+.28*er_blend+.23*h+.15*((ac1+1)/2)))
    mean_reversion=_clip(100-persistence)

    bodies=[abs(float(b.close)-float(b.open)) for b in bars[-48:]]
    ranges=[max(0.0,float(b.high)-float(b.low)) for b in bars[-48:]]
    body_ratio=mean([x/y for x,y in zip(bodies,ranges) if y>0] or [0.0])
    impulse=_clip(100*(.55*body_ratio+.45*eff))

    # Noise is high when travel is inefficient and candle bodies occupy little range.
    noise=_clip(100*(.60*(1-eff)+.40*(1-body_ratio)))
    vol_ref=[abs(x) for x in rets[-120:]]
    vol_now=mean([abs(x) for x in rets[-12:]] or [0.0])
    vol_base=mean(vol_ref) if vol_ref else 0.0
    vol_ratio=vol_now/vol_base if vol_base else 1.0
    volatility=_clip(50+35*math.log(max(.25,min(4.0,vol_ratio)),2))

    # Counter-move depth against the net 24H direction, normalized by ATR.
    net=closes[-1]-closes[-25]
    sign=1 if net>0 else (-1 if net<0 else 0)
    adverse=[]
    if sign and av>0:
        anchor=closes[-25]
        best=anchor
        for c in closes[-24:]:
            best=max(best,c) if sign>0 else min(best,c)
            adverse.append(max(0.0,(best-c)*sign/av))
    pullback_atr=max(adverse or [0.0])
    pullback_depth=_clip(100*pullback_atr/2.0)

    try:
        base,quote=symbol.split('/')
        strength_gap=float((strength or {}).get(base,0))-float((strength or {}).get(quote,0))
    except Exception: strength_gap=0.0

    if persistence>=62: label='ТРЕНДОВЫЙ'
    elif mean_reversion>=62: label='ВОЗВРАТНЫЙ'
    else: label='СМЕШАННЫЙ'
    if volatility>=68: label += ' · БЫСТРЫЙ'
    elif noise>=68: label += ' · ШУМНЫЙ'
    elif volatility<=35: label += ' · СПОКОЙНЫЙ'

    # Character is also session-dependent.  Compare only closed H1 bars that
    # historically belonged to the same Amsterdam session as the latest bar.
    current_session=_session_key(_parse_dt(getattr(bars[-1],"dt",None)))
    session_rows=[]
    for b in bars[-120:]:
        r=max(0.0,float(b.high)-float(b.low))
        if _session_key(_parse_dt(getattr(b,"dt",None))) == current_session:
            session_rows.append((b,r))
    sr=[x[1] for x in session_rows if x[1] > 0]
    session_range_atr=(median(sr)/av) if sr and av>0 else 0.0
    session_body_ratio=mean([abs(x[0].close-x[0].open)/x[1] for x in session_rows if x[1]>0] or [0.0])
    session_activity=_clip(100*(.55*min(1.5,session_range_atr)/1.5+.45*session_body_ratio))

    reliability=_clip(100-(noise*.40)-min(25,abs(vol_ratio-1)*18)+eff*25+er_blend*15)
    return {
        'symbol':symbol,'ready':True,'samples':len(bars),'label':label,
        'trend_persistence':round(persistence,1),'mean_reversion':round(mean_reversion,1),
        'impulse':round(impulse,1),'noise':round(noise,1),'volatility':round(volatility,1),
        'pullback_depth':round(pullback_depth,1),'pullback_atr':round(pullback_atr,2),
        'hurst_proxy':round(h,3),'autocorr_1':round(ac1,3),'efficiency':round(eff,3),
        'efficiency_ratio_8':round(er8,3),'efficiency_ratio_24':round(er24,3),
        'efficiency_ratio_72':round(er72,3),'efficiency_ratio_blend':round(er_blend,3),
        'session':current_session,'session_samples':len(session_rows),
        'session_range_atr':round(session_range_atr,3),'session_activity':round(session_activity,1),
        'atr_pct':round(atr_pct,4),'volatility_ratio':round(vol_ratio,3),
        'strength_gap':round(strength_gap,4),'reliability':round(reliability,1),
    }


def compact_text(p: dict) -> str:
    if not p or not p.get('ready'): return 'Характер пары: статистика ещё накапливается'
    return (f"Характер пары: {p['label']} · тренд {p['trend_persistence']:.0f}/100 · "
            f"импульс {p['impulse']:.0f}/100 · шум {p['noise']:.0f}/100 · "
            f"ER24 {p.get('efficiency_ratio_24',0):.2f} · откат {p['pullback_atr']:.2f} ATR · "
            f"сессия {p.get('session_activity',0):.0f}/100")


def interaction_matrix(profile: dict, regime: str | None = None) -> dict:
    """Direction-neutral pair×session×strength×regime composition.

    This is a context score, not a trade score.  It answers whether the current
    pair personality, current session activity and observed currency-strength
    separation form a coherent environment.  It never creates/flips/vetoes a side.
    """
    if not profile or not profile.get('ready'):
        return {'ready': False, 'score': 50.0, 'band': 'НЕЙТРАЛЬНО'}
    trend=float(profile.get('trend_persistence') or 0)/100.0
    impulse=float(profile.get('impulse') or 0)/100.0
    noise=float(profile.get('noise') or 0)/100.0
    sess=float(profile.get('session_activity') or 0)/100.0
    reliability=float(profile.get('reliability') or 0)/100.0
    # Strength values in the project are centred around zero.  Saturation at
    # 0.12 prevents an exceptional reading from dominating the whole matrix.
    strength=min(1.0, abs(float(profile.get('strength_gap') or 0))/0.12)
    r=str(regime or '').upper()
    if r in ('TREND','EXPANSION','BREAKOUT'):
        regime_fit=.55*trend+.30*impulse+.15*(1-noise)
    elif r in ('RANGE','COMPRESSION','BALANCE','ROTATION'):
        regime_fit=.55*(1-trend)+.30*noise+.15*(1-impulse)
    else:
        regime_fit=.50
    # Fixed transparent weights; sum=1.00.  Reliability receives the largest
    # share because it already penalises unstable volatility/noise.
    score=100*(.28*reliability+.22*sess+.18*regime_fit+.17*(1-noise)+.15*strength)
    score=_clip(score)
    if score >= 72: band='СИЛЬНОЕ СОГЛАСОВАНИЕ'
    elif score >= 58: band='РАБОЧЕЕ СОГЛАСОВАНИЕ'
    elif score >= 43: band='СМЕШАННО'
    else: band='СЛАБОЕ СОГЛАСОВАНИЕ'
    return {'ready':True,'score':round(score,1),'band':band,
            'components':{'pair_reliability':round(100*reliability,1),
                          'session_fit':round(100*sess,1),'regime_fit':round(100*regime_fit,1),
                          'cleanliness':round(100*(1-noise),1),'strength_separation':round(100*strength,1)},
            'weights':{'pair_reliability':.28,'session_fit':.22,'regime_fit':.18,'cleanliness':.17,'strength_separation':.15}}


def attach_interaction(profile: dict, regime: str | None = None) -> dict:
    if not profile: return profile
    out=dict(profile); out['interaction_matrix']=interaction_matrix(out, regime); return out
