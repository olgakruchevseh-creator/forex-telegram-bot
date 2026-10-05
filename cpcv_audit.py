"""Small dependency-free CPCV diagnostics for already-recorded outcomes.
OBSERVE_ONLY: validation only; never changes trading decisions.
"""
from __future__ import annotations
import math
from itertools import combinations


def n_paths(n_groups:int, n_test_groups:int)->int:
    if n_groups<2 or n_test_groups<1 or n_test_groups>=n_groups: return 0
    return math.comb(n_groups-1,n_test_groups-1)


def cpcv_splits(n:int, n_groups=6, n_test_groups=2, embargo=1):
    if n < n_groups or n_test_groups>=n_groups: return []
    groups=[]
    base=n//n_groups; rem=n%n_groups; s=0
    for g in range(n_groups):
        size=base+(1 if g<rem else 0); groups.append(list(range(s,s+size))); s+=size
    out=[]
    for test_ids in combinations(range(n_groups),n_test_groups):
        test=sorted(i for g in test_ids for i in groups[g]); blocked=set(test)
        for g in test_ids:
            end=groups[g][-1]
            blocked.update(range(end+1,min(n,end+1+max(0,int(embargo)))))
            # one-observation purge on the left boundary for bar-overlap safety
            start=groups[g][0]
            if start>0: blocked.add(start-1)
        train=[i for i in range(n) if i not in blocked]
        out.append({"test_groups":list(test_ids),"train":train,"test":test})
    return out


def cpcv_distribution(values, n_groups=6, n_test_groups=2, embargo=1):
    xs=[float(x) for x in values if x is not None]
    splits=cpcv_splits(len(xs),n_groups,n_test_groups,embargo)
    if not splits: return {"status":"INSUFFICIENT_DATA","n":len(xs)}
    scores=[]
    for s in splits:
        vals=[xs[i] for i in s["test"]]
        scores.append(sum(vals)/len(vals) if vals else 0.0)
    scores.sort(); m=len(scores)
    return {"status":"OK","n":len(xs),"splits":len(splits),"theoretical_paths":n_paths(n_groups,n_test_groups),
            "positive_split_rate":round(sum(x>0 for x in scores)/m,3),
            "oos_expectancy_p10":round(scores[max(0,int((m-1)*.10))],4),
            "oos_expectancy_median":round(scores[(m-1)//2],4),
            "oos_expectancy_p90":round(scores[min(m-1,int((m-1)*.90))],4)}
