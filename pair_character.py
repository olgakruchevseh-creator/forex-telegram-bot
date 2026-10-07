"""Pair Character — adaptive, read-only symbol personality context.

The layer learns *how* each FX major is moving now instead of assigning permanent
labels to symbols.  It is deliberately OBSERVE_ONLY: no signal is invented,
blocked or flipped here.  Other engines may consume the returned context as an
extra reliability feature after enough replay samples exist.
"""
from __future__ import annotations
from collections import Counter
from statistics import median
import math

import config as cfg
from analysis import atr, closed_candles


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def _safe_median(xs, default=0.0):
    vals=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return median(vals) if vals else default


def _runs(signs):
    if not signs:return []
    out=[]; n=1
    for a,b in zip(signs, signs[1:]):
        if a==b and a!=0:n+=1
        else:
            if a!=0:out.append(n)
            n=1
    if signs[-1]!=0:out.append(n)
    return out


def analyze_symbol(symbol:str, by_tf:dict) -> dict | None:
    """Build causal H1 character features from closed candles only."""
    bars=closed_candles((by_tf or {}).get('H1') or [], 60)
    lookback=int(getattr(cfg,'PAIR_CHARACTER_LOOKBACK_H1',120))
    if len(bars) < int(getattr(cfg,'PAIR_CHARACTER_MIN_H1',48)):
        return None
    b=bars[-lookback:]
    a=atr(b, int(getattr(cfg,'ATR_PERIOD',14)))
    if a <= 0:return None

    ranges=[max(0.0,x.high-x.low) for x in b]
    bodies=[abs(x.close-x.open) for x in b]
    signs=[1 if x.close>x.open else -1 if x.close<x.open else 0 for x in b]
    body_ratio=[bo/ra if ra>0 else 0 for bo,ra in zip(bodies,ranges)]
    wick_ratio=[]
    for x,ra in zip(b,ranges):
        if ra<=0:continue
        wick=(x.high-max(x.open,x.close))+(min(x.open,x.close)-x.low)
        wick_ratio.append(max(0.0,wick)/ra)

    # Trend efficiency: displacement / travelled path, 0=chop, 1=straight move.
    window=min(24,len(b)-1)
    path=sum(abs(b[i].close-b[i-1].close) for i in range(len(b)-window,len(b)))
    displacement=abs(b[-1].close-b[-1-window].close)
    efficiency=displacement/path if path>0 else 0.0

    # Reversal pressure: how often H1 direction alternates.
    nz=[s for s in signs[-48:] if s]
    flips=sum(a!=c for a,c in zip(nz,nz[1:]))
    flip_rate=flips/max(1,len(nz)-1)
    runs=_runs(nz)
    persistence=_safe_median(runs,1.0)

    # Sweep/wick tendency: large wick relative to body/range, kept descriptive.
    wickiness=_safe_median(wick_ratio[-48:])
    body_eff=_safe_median(body_ratio[-48:])
    range_atr=_safe_median([x/a for x in ranges[-48:]])
    expansion=sum(1 for x in ranges[-24:] if x >= 1.25*a)/max(1,min(24,len(ranges)))

    # Gap between fast and slow realised ranges gives current volatility state.
    fast=_safe_median(ranges[-12:]); slow=_safe_median(ranges[-48:])
    vol_ratio=fast/slow if slow>0 else 1.0

    if efficiency >= .50 and persistence >= 2: movement='IMPULSE_PERSISTENT'
    elif flip_rate >= .58 and efficiency <= .30: movement='ROTATIONAL'
    elif vol_ratio >= 1.25 or expansion >= .30: movement='EXPANSIVE'
    elif vol_ratio <= .78: movement='COMPRESSED'
    else: movement='BALANCED'

    # These affinities are evidence summaries, not trading scores.
    trend_aff=_clamp(.55*efficiency + .25*_clamp(persistence/4) + .20*body_eff)
    reversal_aff=_clamp(.45*flip_rate + .35*wickiness + .20*(1-efficiency))
    breakout_aff=_clamp(.40*body_eff + .35*expansion + .25*_clamp(vol_ratio/1.5))
    sweep_aff=_clamp(.60*wickiness + .25*flip_rate + .15*(1-body_eff))

    return {
        'schema':1,'mode':'OBSERVE_ONLY','symbol':symbol,'samples_h1':len(b),
        'movement_character':movement,'atr_h1':round(a,8),
        'range_atr_median':round(range_atr,3),'trend_efficiency':round(efficiency,3),
        'direction_flip_rate':round(flip_rate,3),'median_direction_run_h1':round(persistence,2),
        'wickiness':round(wickiness,3),'body_efficiency':round(body_eff,3),
        'volatility_ratio_fast_slow':round(vol_ratio,3),'expansion_rate':round(expansion,3),
        'affinity':{
            'trend_continuation':round(trend_aff,3),
            'mean_reversion':round(reversal_aff,3),
            'breakout':round(breakout_aff,3),
            'liquidity_sweep':round(sweep_aff,3),
        },
    }


def module_affinity(ctx:dict|None, family:str) -> float:
    """Return 0..1 descriptive fit for a module family; neutral if unavailable."""
    if not ctx:return .5
    key={
      'trend':'trend_continuation','structure':'trend_continuation','zigzag':'trend_continuation',
      'breakout':'breakout','crt':'breakout','retest':'breakout',
      'reversal':'mean_reversion','quasimodo':'mean_reversion','harmonic':'mean_reversion',
      'liquidity':'liquidity_sweep','sweep':'liquidity_sweep','fvg':'liquidity_sweep',
      'order_block':'liquidity_sweep','bpr':'liquidity_sweep',
    }.get(str(family).lower())
    return float((ctx.get('affinity') or {}).get(key,.5)) if key else .5


def analyze_market(market:dict) -> dict:
    return {p:c for p in getattr(cfg,'PAIRS',()) if (c:=analyze_symbol(p,(market or {}).get(p) or {}))}
