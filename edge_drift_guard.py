"""OBSERVE_ONLY edge degradation / structural-break diagnostics."""
from __future__ import annotations
import math
from statistics import mean, pstdev

def _nums(xs):
    out=[]
    for x in xs or []:
        try:
            v=float(x)
            if math.isfinite(v): out.append(v)
        except (TypeError,ValueError): pass
    return out

def lower_cusum(values, *, baseline=None, k=0.5, h=4.0):
    """One-sided lower CUSUM on standardized outcomes; flags persistent degradation."""
    x=_nums(values)
    if len(x)<8: return {'status':'INSUFFICIENT_DATA','n':len(x),'alarm':False}
    base=_nums(baseline) if baseline is not None else x[:max(5,len(x)//2)]
    mu=mean(base); sd=pstdev(base)
    if sd<=1e-12: return {'status':'NO_VARIANCE','n':len(x),'alarm':False}
    s=0.0; peak=0.0; alarm_at=None
    for i,v in enumerate(x):
        z=(v-mu)/sd
        s=max(0.0,s+(-z-float(k))); peak=max(peak,s)
        if alarm_at is None and s>h: alarm_at=i
    return {'status':'OK','n':len(x),'baseline_mean':round(mu,4),'baseline_sd':round(sd,4),
            'cusum':round(s,3),'peak':round(peak,3),'alarm':alarm_at is not None,'alarm_at':alarm_at}

def window_degradation(values, *, recent=10, min_history=30):
    x=_nums(values)
    if len(x)<min_history or len(x)<=recent: return {'status':'INSUFFICIENT_DATA','n':len(x),'degraded':False}
    old=x[:-recent]; new=x[-recent:]
    om,nm=mean(old),mean(new); osd=pstdev(old) or 1e-12
    z=(nm-om)/(osd/math.sqrt(max(1,len(new))))
    return {'status':'OK','n':len(x),'history_mean':round(om,4),'recent_mean':round(nm,4),
            'delta':round(nm-om,4),'z_like':round(z,3),'degraded':bool(nm<om and z<=-2.0)}

def edge_health(values, **kwargs):
    c=lower_cusum(values,k=kwargs.get('k',.5),h=kwargs.get('h',4.0))
    w=window_degradation(values,recent=kwargs.get('recent',10),min_history=kwargs.get('min_history',30))
    alarms=[name for name,obj in [('CUSUM',c),('RECENT_WINDOW',w)] if obj.get('alarm') or obj.get('degraded')]
    return {'status':'ALERT' if alarms else ('OK' if c.get('status')=='OK' or w.get('status')=='OK' else 'INSUFFICIENT_DATA'),
            'alarms':alarms,'cusum':c,'window':w,'live_effect':'NONE'}
