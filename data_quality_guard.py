"""Passive market-data quality guard.

Fail-closed diagnostic primitives for OHLC bars. No trading direction is produced.
Designed to be called before analytics; integration into live routing is deliberately
left opt-in so this patch cannot silently change current signal flow.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime
from statistics import median

@dataclass
class QualityReport:
    ok: bool
    n: int
    duplicates: int = 0
    non_monotonic: int = 0
    impossible_ohlc: int = 0
    flat_bars: int = 0
    large_gaps: int = 0
    stale_tail_bars: int = 0
    reasons: tuple[str, ...] = ()
    def to_dict(self): return asdict(self)

def _get(x, name):
    return x.get(name) if isinstance(x, dict) else getattr(x, name, None)

def audit_bars(bars, *, expected_seconds=None, max_gap_bars=3, max_stale_tail=2):
    bars=list(bars or [])
    if len(bars)<2:
        return QualityReport(False,len(bars),reasons=('INSUFFICIENT_BARS',))
    times=[]; impossible=flat=0
    for b in bars:
        o,h,l,c=(_get(b,k) for k in ('open','high','low','close'))
        t=_get(b,'dt') or _get(b,'datetime') or _get(b,'time')
        try: o,h,l,c=map(float,(o,h,l,c))
        except (TypeError,ValueError): impossible+=1; continue
        if min(o,h,l,c)<=0 or h < max(o,c,l) or l > min(o,c,h): impossible+=1
        if o==h==l==c: flat+=1
        if isinstance(t,str):
            try: t=datetime.fromisoformat(t.replace('Z','+00:00'))
            except ValueError: t=None
        times.append(t)
    valid=[t for t in times if isinstance(t,datetime)]
    duplicates=max(0,len(valid)-len(set(valid)))
    nonmono=sum(1 for a,b in zip(valid,valid[1:]) if b<=a)
    gaps=0
    if expected_seconds and expected_seconds>0:
        gaps=sum(1 for a,b in zip(valid,valid[1:]) if (b-a).total_seconds()>expected_seconds*max_gap_bars)
    tail=0
    for b in reversed(bars):
        o,h,l,c=(_get(b,k) for k in ('open','high','low','close'))
        try: is_flat=float(o)==float(h)==float(l)==float(c)
        except (TypeError,ValueError): is_flat=False
        if is_flat: tail+=1
        else: break
    reasons=[]
    if duplicates: reasons.append('DUPLICATE_TIMESTAMPS')
    if nonmono: reasons.append('NON_MONOTONIC_TIME')
    if impossible: reasons.append('INVALID_OHLC')
    if gaps: reasons.append('MISSING_INTERVALS')
    if tail>max_stale_tail: reasons.append('STALE_FLAT_TAIL')
    return QualityReport(not reasons,len(bars),duplicates,nonmono,impossible,flat,gaps,tail,tuple(reasons))
