"""Layer 11 — Concept Drift / Change-Point Brain (OBSERVE_ONLY).

Causal, dependency-free telemetry over already available market/evidence facts.
It detects persistent distribution changes, distinguishes one-off shocks, and
tracks adaptation to a new baseline. It NEVER creates/vetoes LONG/SHORT and
never changes trading scores or thresholds.
"""
from __future__ import annotations
import hashlib, json, math, os
from pathlib import Path
from statistics import mean, median, pstdev
import config as cfg


def _state_path() -> Path:
    root=os.getenv("STATE_DIR","").strip()
    return (Path(root) if root else Path(__file__).resolve().parent)/"layer11_change_point_state.json"

def _load() -> dict:
    try:
        p=_state_path()
        if p.exists():
            x=json.loads(p.read_text(encoding="utf-8"))
            if isinstance(x,dict): return x
    except Exception: pass
    return {"schema":1,"pairs":{}}

def _save(x: dict) -> None:
    try:
        p=_state_path(); p.parent.mkdir(parents=True,exist_ok=True)
        t=p.with_suffix(".tmp"); t.write_text(json.dumps(x,ensure_ascii=False,separators=(",",":")),encoding="utf-8"); t.replace(p)
    except Exception: pass

def _f(v, default=0.0):
    try:
        x=float(v); return x if math.isfinite(x) else default
    except (TypeError,ValueError): return default

def _vector(ctx: dict, reliability: dict) -> list[float]:
    u=ctx.get("evidence_uncertainty") or {}
    return [
        _f(reliability.get("reliability_index")),
        _f(u.get("conflict_ratio")),
        min(1.0,_f(u.get("directional_entropy"))),
        min(1.0,_f(u.get("effective_family_count"))/4.0),
        min(1.0,abs(_f(ctx.get("directed_gap")))/1.0),
        min(1.0,_f(ctx.get("progress"))/100.0),
        min(1.0,_f(ctx.get("junior_n"))/3.0),
        min(1.0,_f(ctx.get("senior_n"))/3.0),
    ]

def _distance(v: list[float], hist: list[list[float]]) -> float:
    if not hist: return 0.0
    cols=list(zip(*hist)); z=[]
    for x,c in zip(v,cols):
        c=list(map(float,c)); m=median(c); sd=pstdev(c) if len(c)>1 else 0.0
        # floor prevents a constant historical feature from exploding on tiny noise.
        z.append(abs(x-m)/max(0.08,sd))
    return sum(z)/len(z)

def _mean_vec(rows: list[list[float]]) -> list[float]:
    return [mean(c) for c in zip(*rows)] if rows else []

def _vec_gap(a: list[float], b: list[float]) -> float:
    return sum(abs(x-y) for x,y in zip(a,b))/max(1,len(a)) if a and b else 0.0

def assess(pair: str, ctx: dict, reliability: dict | None) -> dict:
    """Observe one causal snapshot and return passive Layer-11 telemetry."""
    reliability=reliability or {}; v=_vector(ctx,reliability)
    fp=hashlib.sha256((pair+"|"+"|".join(f"{x:.3f}" for x in v)+"|"+str(ctx.get("regime"))).encode()).hexdigest()[:20]
    state=_load(); node=state.setdefault("pairs",{}).setdefault(pair,{"history":[],"phase":"STABLE","confirm":0,"adapt":0,"last_fp":""})
    history=[r for r in (node.get("history") or []) if isinstance(r,list) and len(r)==len(v)]
    min_h=int(getattr(cfg,"LAYER11_MIN_HISTORY",24)); base_n=int(getattr(cfg,"LAYER11_BASELINE_WINDOW",24)); recent_n=int(getattr(cfg,"LAYER11_RECENT_WINDOW",6))
    warn=float(getattr(cfg,"LAYER11_WARN_DISTANCE",1.45)); confirm_d=float(getattr(cfg,"LAYER11_CONFIRM_DISTANCE",1.90)); confirm_need=int(getattr(cfg,"LAYER11_CONFIRM_WINDOWS",3)); adapt_need=int(getattr(cfg,"LAYER11_ADAPT_WINDOWS",6))
    phase=str(node.get("phase") or "STABLE"); reasons=[]; dist=0.0; shift=0.0
    if len(history)<min_h:
        phase="LEARNING"; reasons=["INSUFFICIENT_HISTORY"]
    else:
        baseline=history[-(base_n+recent_n):-recent_n] if len(history)>=base_n+recent_n else history[-base_n:]
        recent=(history[-max(1,recent_n-1):]+[v])
        dist=_distance(v,baseline); shift=_vec_gap(_mean_vec(baseline),_mean_vec(recent))
        persistent=shift>=float(getattr(cfg,"LAYER11_MIN_MEAN_SHIFT",0.12))
        if dist>=confirm_d and persistent:
            node["confirm"]=int(node.get("confirm",0))+1
        elif dist>=warn:
            node["confirm"]=max(1,int(node.get("confirm",0)))
        else:
            node["confirm"]=max(0,int(node.get("confirm",0))-1)
        c=int(node.get("confirm",0))
        # One isolated extreme observation is a SHOCK/WATCH, not a change point.
        if dist>=confirm_d and not persistent:
            phase="SHOCK"; reasons.append("ISOLATED_OUTLIER")
        elif c>=confirm_need:
            if phase not in {"CHANGE_POINT","ADAPTATION","NEW_BASELINE"}:
                phase="CHANGE_POINT"; node["adapt"]=0
            else:
                phase="ADAPTATION"; node["adapt"]=int(node.get("adapt",0))+1
            reasons.append("PERSISTENT_MULTIVARIATE_SHIFT")
        elif c>0:
            phase="DRIFT_SUSPECTED"; reasons.append("SHIFT_NEEDS_CONFIRMATION")
        else:
            if phase in {"CHANGE_POINT","ADAPTATION"}:
                node["adapt"]=int(node.get("adapt",0))+1
                if int(node["adapt"])>=adapt_need:
                    phase="NEW_BASELINE"; reasons.append("POST_CHANGE_STABLE")
                else:
                    phase="ADAPTATION"; reasons.append("POST_CHANGE_OBSERVATION")
            else:
                phase="STABLE"; node["adapt"]=0
    node["phase"]=phase
    # Learn after classification. Duplicate snapshots do not inflate persistence.
    if fp!=node.get("last_fp"):
        history.append(v); keep=int(getattr(cfg,"LAYER11_HISTORY_LIMIT",240)); node["history"]=history[-keep:]; node["last_fp"]=fp; _save(state)
    return {"layer":11,"observe_only":True,"state":phase,"history_snapshots":len(history),"distance":round(dist,4),"mean_shift":round(shift,4),"confirm_count":int(node.get("confirm",0)),"adapt_count":int(node.get("adapt",0)),"reasons":reasons,"trade_effect":False,"direction_claim":False}
