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


def _percentile_rank(values, x):
    vals=[float(v) for v in values if math.isfinite(float(v))]
    if not vals: return 50.0
    return 100.0*sum(v <= float(x) for v in vals)/len(vals)

def _zscore(values, x):
    vals=[float(v) for v in values if math.isfinite(float(v))]
    if len(vals)<8: return 0.0
    sd=pstdev(vals)
    return (float(x)-mean(vals))/sd if sd>1e-15 else 0.0

def _realized_vol(returns):
    # Root-sum-square realized volatility on log returns; deliberately not annualised.
    return math.sqrt(sum(float(r)*float(r) for r in returns)) if returns else 0.0

def _directional_persistence(returns):
    signs=[1 if r>0 else (-1 if r<0 else 0) for r in returns]
    pairs=[(a,b) for a,b in zip(signs,signs[1:]) if a and b]
    return sum(a==b for a,b in pairs)/len(pairs) if pairs else 0.5

def _sign_entropy(returns):
    # Normalised Shannon entropy of up/down log-return signs: 0=one-sided, 1=maximally mixed.
    signs=[1 if r>0 else -1 for r in returns if r != 0]
    if not signs: return 1.0
    p=sum(s>0 for s in signs)/len(signs)
    if p<=0 or p>=1: return 0.0
    return -(p*math.log(p)+(1-p)*math.log(1-p))/math.log(2.0)

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
    simple_rets=[(b-a)/a for a,b in zip(closes,closes[1:]) if a]
    rets=[math.log(b/a) for a,b in zip(closes,closes[1:]) if a>0 and b>0]
    av=float(atr(bars,14) or 0.0); px=max(abs(closes[-1]),1e-12)
    atr_pct=100*av/px
    recent=closes[-25:]; eff=_efficiency(recent)
    # Kaufman ER at three horizons: short/session, day, multi-day character.
    er8, er24, er72 = _er(closes,8), _er(closes,24), _er(closes,72)
    er_blend = .25*er8 + .50*er24 + .25*er72
    ac1=_corr(rets[:-1],rets[1:]) if len(rets)>10 else 0.0
    dir_persistence=_directional_persistence(rets[-48:])
    return_entropy=_sign_entropy(rets[-48:])
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
    rv12=_realized_vol(rets[-12:])
    rv24=_realized_vol(rets[-24:])
    # Causal historical 12H RV distribution for pair-relative percentile/z-score.
    rv_hist=[]
    for i in range(12,len(rets)+1):
        rv_hist.append(_realized_vol(rets[max(0,i-12):i]))
    rv_ref=rv_hist[:-1] if len(rv_hist)>1 else rv_hist
    rv_percentile=_percentile_rank(rv_ref,rv12)
    rv_z=_zscore(rv_ref,rv12)
    eff_hist=[]
    for i in range(25,len(closes)+1):
        eff_hist.append(_efficiency(closes[i-25:i]))
    eff_ref=eff_hist[:-1] if len(eff_hist)>1 else eff_hist
    eff_percentile=_percentile_rank(eff_ref,eff)
    eff_z=_zscore(eff_ref,eff)
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
    # Andersen-Bollerslev style intraday-periodicity adjustment: compare the
    # current session's typical H1 range with the pair's own unconditional H1
    # range.  This makes 'session character' relative to EUR/USD, USD/JPY, etc.
    # rather than imposing a universal London/New-York stereotype.
    all_ranges=[max(0.0,float(b.high)-float(b.low)) for b in bars[-120:]]
    all_pos=[x for x in all_ranges if x>0]
    base_range=median(all_pos) if all_pos else 0.0
    periodic_factor=(median(sr)/base_range) if sr and base_range>0 else 1.0
    # 1.0 = normal activity for this pair; 2.0 or more saturates the scale.
    session_activity=_clip(50.0*min(2.0,max(0.0,periodic_factor)))

    reliability=_clip(100-(noise*.40)-min(25,abs(vol_ratio-1)*18)+eff*25+er_blend*15)
    return {
        'symbol':symbol,'ready':True,'samples':len(bars),'label':label,
        'trend_persistence':round(persistence,1),'mean_reversion':round(mean_reversion,1),
        'impulse':round(impulse,1),'noise':round(noise,1),'volatility':round(volatility,1),
        'pullback_depth':round(pullback_depth,1),'pullback_atr':round(pullback_atr,2),
        'hurst_proxy':round(h,3),'autocorr_1':round(ac1,3),'efficiency':round(eff,3),
        'log_return_last':round(rets[-1],8) if rets else 0.0,
        'realized_vol_12h':round(rv12,8),'realized_vol_24h':round(rv24,8),
        'realized_vol_percentile':round(rv_percentile,1),'realized_vol_zscore':round(rv_z,3),
        'efficiency_percentile':round(eff_percentile,1),'efficiency_zscore':round(eff_z,3),
        'directional_persistence':round(100*dir_persistence,1),'return_sign_entropy':round(return_entropy,3),
        'efficiency_ratio_8':round(er8,3),'efficiency_ratio_24':round(er24,3),
        'efficiency_ratio_72':round(er72,3),'efficiency_ratio_blend':round(er_blend,3),
        'session':current_session,'session_samples':len(session_rows),
        'session_range_atr':round(session_range_atr,3),'session_periodic_factor':round(periodic_factor,3),
        'session_activity':round(session_activity,1),
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
    # No hand-tuned importance coefficients.  Combine the five independent
    # context dimensions with an equal-weight geometric mean.  A weak dimension
    # therefore cannot be hidden by one very strong reading, while no professor-
    # looking coefficient is invented where the literature does not prescribe one.
    components01=[reliability,sess,regime_fit,(1-noise),strength]
    floor=0.01
    score=100*math.prod(max(floor,min(1.0,x)) for x in components01)**(1.0/len(components01))
    score=_clip(score)
    if score >= 72: band='СИЛЬНОЕ СОГЛАСОВАНИЕ'
    elif score >= 58: band='РАБОЧЕЕ СОГЛАСОВАНИЕ'
    elif score >= 43: band='СМЕШАННО'
    else: band='СЛАБОЕ СОГЛАСОВАНИЕ'
    return {'ready':True,'score':round(score,1),'band':band,
            'components':{'pair_reliability':round(100*reliability,1),
                          'session_fit':round(100*sess,1),'regime_fit':round(100*regime_fit,1),
                          'cleanliness':round(100*(1-noise),1),'strength_separation':round(100*strength,1)},
            'weights':{'pair_reliability':.20,'session_fit':.20,'regime_fit':.20,'cleanliness':.20,'strength_separation':.20},
            'combiner':'EQUAL_WEIGHT_GEOMETRIC_MEAN'}


def attach_interaction(profile: dict, regime: str | None = None) -> dict:
    if not profile: return profile
    out=dict(profile); out['interaction_matrix']=interaction_matrix(out, regime); return out
