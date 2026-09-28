from types import SimpleNamespace
import path_quality_context as pq

def _bars(n=40,start=1.10,step=.0005):
    out=[]
    for i in range(n):
        o=start+i*step; c=o+step*.7
        out.append(SimpleNamespace(open=o,high=max(o,c)+.0004,low=min(o,c)-.0004,close=c,dt=f"2026-09-{1+i//24:02d}T{i%24:02d}:00:00+00:00"))
    return out

def test_clear_path_is_context_not_family(monkeypatch):
    monkeypatch.setattr(pq.liquidity_map,"build_map",lambda *a:[])
    monkeypatch.setattr(pq.liquidity_context,"analyze_symbol",lambda *a,**k:SimpleNamespace(residual_state="OPEN"))
    c=pq.analyze_symbol("EUR/USD",{"H1":_bars()},1,[])
    assert c and c.clear_to_tr1 and c.score>=80 and c.family=="PATH_QUALITY_CONTEXT"

def test_barrier_before_tr1_reduces_quality(monkeypatch):
    bars=_bars(); entry=bars[-1].close
    pool=SimpleNamespace(status="intact",level=entry+.0005,source="Resistance zone",rank=5)
    monkeypatch.setattr(pq.liquidity_map,"build_map",lambda *a:[pool])
    monkeypatch.setattr(pq.liquidity_context,"analyze_symbol",lambda *a,**k:SimpleNamespace(residual_state="LOW"))
    c=pq.analyze_symbol("EUR/USD",{"H1":bars},1,[])
    assert c and not c.clear_to_tr1 and c.delta<0
