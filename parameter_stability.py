"""Parameter plateau diagnostics; OBSERVE_ONLY and optimizer-agnostic."""
from __future__ import annotations
import math

def plateau_1d(points, *, center, radius=2, max_drop_fraction=.25):
    """points: mapping numeric parameter -> score. Good settings should live on a plateau, not a spike."""
    clean={float(k):float(v) for k,v in (points or {}).items() if math.isfinite(float(v))}
    c=float(center)
    if c not in clean or len(clean)<3: return {'status':'INSUFFICIENT_DATA','stable':False}
    keys=sorted(clean); pos=keys.index(c); neigh=keys[max(0,pos-radius):pos]+keys[pos+1:pos+1+radius]
    if not neigh: return {'status':'INSUFFICIENT_NEIGHBORS','stable':False}
    peak=clean[c]; floor=min(clean[k] for k in neigh); avg=sum(clean[k] for k in neigh)/len(neigh)
    scale=max(abs(peak),1e-9); drop=(peak-avg)/scale
    return {'status':'OK','center':c,'center_score':round(peak,4),'neighbor_n':len(neigh),
            'neighbor_avg':round(avg,4),'neighbor_floor':round(floor,4),'relative_drop':round(drop,4),
            'stable':bool(drop<=max_drop_fraction),'classification':'PLATEAU' if drop<=max_drop_fraction else 'CLIFF'}
