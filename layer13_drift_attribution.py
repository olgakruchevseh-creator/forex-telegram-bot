"""Layer 13 — Drift Attribution & Context Transition Brain (OBSERVE_ONLY).

Explains a confirmed Layer-11 change using causal evidence-family deltas and
learns sparse transitions between Layer-12 context prototypes. Diagnostic only:
never creates/vetoes LONG/SHORT and never changes live scores or thresholds.
"""
from __future__ import annotations
import hashlib, json, math, os
from pathlib import Path
from statistics import mean
import config as cfg


def _state_path() -> Path:
    root=os.getenv("STATE_DIR","").strip()
    return (Path(root) if root else Path(__file__).resolve().parent)/"layer13_drift_attribution_state.json"

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

def _f(v,d=0.0):
    try:
        x=float(v); return x if math.isfinite(x) else d
    except (TypeError,ValueError): return d

def _clip(x): return max(0.0,min(1.0,_f(x)))

def _families(ctx: dict) -> dict[str,float]:
    """Normalized, already-causal evidence families available at decision time."""
    u=ctx.get("evidence_uncertainty") or {}; r=ctx.get("evidence_reliability") or {}
    views=ctx.get("views") or {}; direction=1 if str(ctx.get("side"))=="LONG" else -1
    senior=sum(1 for tf in ("D1","H4","H1") if _f(views.get(tf))==direction)/3.0
    junior=sum(1 for tf in ("H1","M15","M5") if _f(views.get(tf))==direction)/3.0
    # These are diagnostic families, not claims that one family causally moved price.
    return {
        "RELIABILITY": _clip(r.get("reliability_index")),
        "CONFLICT": _clip(u.get("conflict_ratio")),
        "DIVERSITY": _clip(_f(u.get("effective_family_count"))/4.0),
        "DIRECTION": _clip((senior+junior)/2.0),
        "STRENGTH": _clip(abs(_f(ctx.get("directed_gap")))),
        "PROGRESS": _clip(_f(ctx.get("progress"))/100.0),
    }

def _mean_maps(rows):
    keys=set().union(*(r.keys() for r in rows)) if rows else set()
    return {k:mean([_f(r.get(k)) for r in rows]) for k in keys}

def _rank_delta(current, baseline):
    rows=[(k,abs(_f(current.get(k))-_f(baseline.get(k)))) for k in current]
    return sorted(rows,key=lambda z:z[1],reverse=True)

def _confidence(count,total,attrib,cp_state):
    # Beta/Laplace smoothing prevents 2/2 => 100%; sample maturity caps confidence.
    smoothed=(count+1.0)/(total+2.0) if total>=0 else 0.5
    min_n=max(1,int(getattr(cfg,"LAYER13_TRANSITION_MIN_SAMPLES",6)))
    maturity=min(1.0,count/min_n)
    cp_weight=1.0 if cp_state in {"CHANGE_POINT","ADAPTATION","NEW_BASELINE"} else 0.55
    raw=0.45*smoothed+0.30*maturity+0.15*_clip(attrib)+0.10*cp_weight
    return min(raw,0.69) if count<min_n else min(0.99,raw)

def assess(pair: str, ctx: dict, change_point: dict|None, regime_memory: dict|None) -> dict:
    cp=change_point or {}; mem=regime_memory or {}; cp_state=str(cp.get("state") or "LEARNING")
    fam=_families(ctx); state=_load(); node=state.setdefault("pairs",{}).setdefault(pair,{"history":[],"last_fp":"","last_context":None,"transitions":{},"run_length":0})
    hist=[r for r in node.get("history",[]) if isinstance(r,dict)]
    base_n=max(2,int(getattr(cfg,"LAYER13_ATTRIBUTION_BASELINE",12))); baseline=_mean_maps(hist[-base_n:]) if hist else fam
    ranked=_rank_delta(fam,baseline); min_delta=float(getattr(cfg,"LAYER13_ATTRIBUTION_MIN_DELTA",0.12))
    meaningful=[x for x in ranked if x[1]>=min_delta]
    primary=meaningful[0][0] if meaningful else None; primary_delta=meaningful[0][1] if meaningful else 0.0
    secondary=[k for k,d in meaningful[1:3] if d>=min_delta]
    # Attribution wording is intentionally conservative unless Layer 11 confirms a change.
    confirmed=cp_state in {"CHANGE_POINT","ADAPTATION","NEW_BASELINE"}
    attribution_state="PRIMARY_DRIVER" if confirmed and primary and primary_delta>=float(getattr(cfg,"LAYER13_PRIMARY_DELTA",0.20)) else ("ASSOCIATED_CHANGE" if meaningful else "NO_MATERIAL_ATTRIBUTION")

    context_id=mem.get("matched_prototype")
    if context_id is None and str(mem.get("state")) in {"NOVEL_CANDIDATE","UNMATCHED_STABLE"}: context_id="NOVEL"
    prev=node.get("last_context"); transition=None; count=0; total=0; confidence=0.0; sample_state="NO_TRANSITION"
    if context_id is not None:
        if prev==context_id: node["run_length"]=int(node.get("run_length",0))+1
        else:
            node["run_length"]=1
            if prev is not None:
                transition=f"{prev}->{context_id}"; trans=node.setdefault("transitions",{})
                rec=trans.setdefault(transition,{"count":0,"drivers":{}}); rec["count"]=int(rec.get("count",0))+1; count=rec["count"]
                if primary:
                    rec["drivers"][primary]=int(rec.get("drivers",{}).get(primary,0))+1
                outgoing=[v for k,v in trans.items() if str(k).startswith(f"{prev}->")]; total=sum(int(v.get("count",0)) for v in outgoing)
                attrib=(rec.get("drivers",{}).get(primary,0)/count) if primary and count else 0.0
                confidence=_confidence(count,total,attrib,cp_state)
                min_n=int(getattr(cfg,"LAYER13_TRANSITION_MIN_SAMPLES",6)); sample_state="LOW_SAMPLE" if count<min_n else ("HIGH" if confidence>=0.75 else "MEDIUM")
        node["last_context"]=context_id

    fp=hashlib.sha256((pair+"|"+"|".join(f"{k}:{v:.3f}" for k,v in sorted(fam.items()))+"|"+cp_state+"|"+str(context_id)).encode()).hexdigest()[:20]
    if fp!=node.get("last_fp"):
        hist.append(fam); keep=int(getattr(cfg,"LAYER13_HISTORY_LIMIT",240)); node["history"]=hist[-keep:]; node["last_fp"]=fp; _save(state)
    return {"layer":13,"observe_only":True,"state":"ATTRIBUTED" if meaningful else "MONITORING","change_point_state":cp_state,"context_id":context_id,"run_length":int(node.get("run_length",0)),"attribution_state":attribution_state,"primary_driver":primary,"primary_delta":round(primary_delta,4),"secondary_drivers":secondary,"transition":transition,"transition_samples":count,"outgoing_samples":total,"transition_confidence":round(confidence,4),"transition_sample_state":sample_state,"trade_effect":False,"direction_claim":False,"causality_claim":False}
