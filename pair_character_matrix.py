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

def _quantile(values, q):
    """Linear empirical quantile (Hyndman-Fan type 7 / NumPy default style)."""
    vals=sorted(float(v) for v in values if math.isfinite(float(v)))
    if not vals: return 0.0
    q=max(0.0,min(1.0,float(q)))
    pos=(len(vals)-1)*q; lo=int(math.floor(pos)); hi=int(math.ceil(pos))
    if lo==hi: return vals[lo]
    return vals[lo]+(vals[hi]-vals[lo])*(pos-lo)

def _robust_scale(values):
    """Median/MAD robust centre and Gaussian-consistent scale."""
    vals=[float(v) for v in values if math.isfinite(float(v))]
    if not vals: return 0.0, 0.0
    m=median(vals); mad=median([abs(v-m) for v in vals])
    return m, 1.4826*mad

def _adaptive_band(values):
    """Distribution-free lower/upper tertiles plus robust diagnostics."""
    vals=[float(v) for v in values if math.isfinite(float(v))]
    if len(vals)<12:
        return {'low':None,'high':None,'median':None,'mad_sigma':None,'samples':len(vals),'method':'INSUFFICIENT'}
    m,rs=_robust_scale(vals)
    return {'low':_quantile(vals,1/3),'high':_quantile(vals,2/3),'median':m,
            'mad_sigma':rs,'samples':len(vals),'method':'EMPIRICAL_TERTILES_MAD'}



def _redundancy_adjusted_weights(histories: dict) -> tuple[dict, dict]:
    """Correlation-aware family weights; absolute correlation prevents +/- duplicates."""
    names=list(histories)
    corr={a:{} for a in names}
    penalties={}
    for a in names:
        for b in names:
            corr[a][b]=1.0 if a==b else _corr(histories[a], histories[b])
        penalties[a]=sum(abs(corr[a][b]) for b in names if b != a)
    raw={a:1.0/(1.0+penalties[a]) for a in names}
    total=sum(raw.values()) or 1.0
    weights={a:raw[a]/total for a in names}
    return weights,corr

def _joint_character_score(current: dict, histories: dict) -> dict:
    """Joint normalization of four families without counting correlated evidence twice.

    Each family first becomes its own causal empirical percentile. Noise is converted
    to cleanliness only at the final context-quality composition. Family weights are
    then reduced when that family is strongly correlated (positively or negatively)
    with the other families. This is descriptive/context-only, never a direction vote.
    """
    names=('trend_persistence','impulse','noise','volatility')
    normalized={n:_percentile_rank(histories.get(n,[]), current.get(n,50.0)) for n in names}
    weights,corr=_redundancy_adjusted_weights({n:histories.get(n,[]) for n in names})
    quality=dict(normalized); quality['noise']=100.0-normalized['noise']

    # Final bounded-influence guard. Empirical percentiles can legitimately reach
    # 0/100 on a fresh extreme; allowing that single observation to enter the joint
    # score unchanged makes a short volatility burst look more important than the
    # remaining character families. Winsorisation at 10/90 keeps ordering intact
    # while bounding any one family's instantaneous leverage. The score is then
    # shrunk toward neutral when the causal history is still short (full trust at
    # 120 observations). This is context stabilisation only; raw diagnostics remain.
    influence_floor, influence_ceiling = 10.0, 90.0
    bounded_quality={n:max(influence_floor,min(influence_ceiling,quality[n])) for n in names}
    raw_score=sum(weights[n]*quality[n] for n in names)
    bounded_score=sum(weights[n]*bounded_quality[n] for n in names)
    min_history=min((len(histories.get(n,[])) for n in names), default=0)
    history_confidence=max(0.0,min(1.0,min_history/120.0))
    score=50.0 + history_confidence*(bounded_score-50.0)
    # Effective independent-family count (Kish ESS): 1..4, transparent diagnostic.
    ess=1.0/sum(w*w for w in weights.values()) if weights else 0.0
    return {
        'score':round(_clip(score),1),
        'raw_score':round(_clip(raw_score),1),
        'bounded_score':round(_clip(bounded_score),1),
        'normalized':{k:round(v,1) for k,v in normalized.items()},
        'quality_components':{k:round(v,1) for k,v in quality.items()},
        'bounded_quality_components':{k:round(v,1) for k,v in bounded_quality.items()},
        'stability_guard':{'method':'WINSORIZED_PERCENTILE_PLUS_HISTORY_SHRINKAGE',
                           'winsor_limits':[influence_floor,influence_ceiling],
                           'history_samples':min_history,
                           'history_confidence':round(history_confidence,4),
                           'full_confidence_samples':120},
        'weights':{k:round(v,4) for k,v in weights.items()},
        'correlation':{a:{b:round(v,3) for b,v in row.items()} for a,row in corr.items()},
        'effective_families':round(ess,2),
        'normalization':'CAUSAL_EMPIRICAL_PERCENTILE',
        'redundancy_control':'INVERSE_ABSOLUTE_CORRELATION_PENALTY',
        'observe_only':True,
    }

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

    # Adaptive thresholds are estimated from this pair's own causal H1 history.
    # Tertiles are distribution-free: no universal EUR/USD=GBP/USD cut-off is assumed.
    # MAD is retained as a robust scale diagnostic and for later calibration.
    hist_persistence=[]; hist_impulse=[]; hist_noise=[]; hist_volratio=[]
    abs_rets=[abs(x) for x in rets]
    for end in range(40,len(closes)):
        cw=closes[:end+1]
        e=_efficiency(cw[-25:])
        bb=bars[max(0,end-47):end+1]
        br=[abs(float(x.close)-float(x.open)) for x in bb]
        rr=[max(0.0,float(x.high)-float(x.low)) for x in bb]
        bdy=mean([x/y for x,y in zip(br,rr) if y>0] or [0.0])
        rr_log=[math.log(b/a) for a,b in zip(cw,cw[1:]) if a>0 and b>0]
        e8,e24,e72=_er(cw,8),_er(cw,24),_er(cw,72)
        eb=.25*e8+.50*e24+.25*e72
        ac=_corr(rr_log[:-1],rr_log[1:]) if len(rr_log)>10 else 0.0
        hh=_hurst_proxy(cw)
        hist_persistence.append(_clip(100*(.34*e+.28*eb+.23*hh+.15*((ac+1)/2))))
        hist_impulse.append(_clip(100*(.55*bdy+.45*e)))
        hist_noise.append(_clip(100*(.60*(1-e)+.40*(1-bdy))))
        idx=max(1,end)
        recent_abs=abs_rets[max(0,idx-12):idx]
        base_abs=abs_rets[max(0,idx-120):idx]
        vb=mean(base_abs) if base_abs else 0.0
        vr=(mean(recent_abs)/vb) if recent_abs and vb else 1.0
        hist_volratio.append(_clip(50+35*math.log(max(.25,min(4.0,vr)),2)))
    joint_character=_joint_character_score(
        {'trend_persistence':persistence,'impulse':impulse,'noise':noise,'volatility':volatility},
        {'trend_persistence':hist_persistence,'impulse':hist_impulse,'noise':hist_noise,'volatility':hist_volratio},
    )
    trend_band=_adaptive_band(hist_persistence)
    impulse_band=_adaptive_band(hist_impulse)
    noise_band=_adaptive_band(hist_noise)
    volatility_band=_adaptive_band(hist_volratio)
    trend_lo=trend_band.get('low') if trend_band.get('low') is not None else 38.0
    trend_hi=trend_band.get('high') if trend_band.get('high') is not None else 62.0
    if persistence>=trend_hi: label='ТРЕНДОВЫЙ'
    elif persistence<=trend_lo: label='ВОЗВРАТНЫЙ'
    else: label='СМЕШАННЫЙ'
    vol_lo=volatility_band.get('low') if volatility_band.get('low') is not None else 35.0
    vol_hi=volatility_band.get('high') if volatility_band.get('high') is not None else 68.0
    noise_hi=noise_band.get('high') if noise_band.get('high') is not None else 68.0
    if volatility>=vol_hi: label += ' · БЫСТРЫЙ'
    elif noise>=noise_hi: label += ' · ШУМНЫЙ'
    elif volatility<=vol_lo: label += ' · СПОКОЙНЫЙ'

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
        'adaptive_thresholds':{
            'trend_persistence':{k:(round(v,3) if isinstance(v,float) else v) for k,v in trend_band.items()},
            'impulse':{k:(round(v,3) if isinstance(v,float) else v) for k,v in impulse_band.items()},
            'noise':{k:(round(v,3) if isinstance(v,float) else v) for k,v in noise_band.items()},
            'volatility':{k:(round(v,3) if isinstance(v,float) else v) for k,v in volatility_band.items()},
            'quantiles':[round(1/3,6),round(2/3,6)],'causal':True,'observe_only':True,
        },
        'character_matrix':joint_character,
        'character_matrix_score':joint_character['score'],
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
