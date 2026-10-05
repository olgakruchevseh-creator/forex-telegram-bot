"""Layer 12 — Recurring Context / Regime Memory (OBSERVE_ONLY).

Remembers compact prototypes of stable post-change evidence contexts and detects
when a later context resembles a previously observed one. This is diagnostic
memory only: no LONG/SHORT, veto, score or threshold effect.
"""
from __future__ import annotations
import hashlib, json, math, os
from pathlib import Path
from statistics import mean
import config as cfg


def _state_path() -> Path:
    root=os.getenv("STATE_DIR","").strip()
    return (Path(root) if root else Path(__file__).resolve().parent)/"layer12_regime_memory_state.json"

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

def _vector(ctx: dict) -> list[float]:
    u=ctx.get("evidence_uncertainty") or {}; r=ctx.get("evidence_reliability") or {}
    return [
        _f(r.get("reliability_index")), _f(u.get("conflict_ratio")),
        min(1.0,_f(u.get("directional_entropy"))),
        min(1.0,_f(u.get("effective_family_count"))/4.0),
        min(1.0,abs(_f(ctx.get("directed_gap")))),
        min(1.0,_f(ctx.get("progress"))/100.0),
        min(1.0,_f(ctx.get("junior_n"))/3.0), min(1.0,_f(ctx.get("senior_n"))/3.0),
    ]

def _similarity(a,b):
    if not a or len(a)!=len(b): return 0.0
    # bounded L1 similarity is stable for this normalized fingerprint and has no
    # zero-vector pathology of cosine similarity.
    return max(0.0,1.0-sum(abs(x-y) for x,y in zip(a,b))/len(a))

def _mean(rows): return [mean(c) for c in zip(*rows)] if rows else []

def assess(pair: str, ctx: dict, change_point: dict | None) -> dict:
    cp=change_point or {}; v=_vector(ctx); cp_state=str(cp.get("state") or "LEARNING")
    state=_load(); node=state.setdefault("pairs",{}).setdefault(pair,{"prototypes":[],"bootstrap":[],"candidate":[],"last_fp":"","active_id":None,"next_id":1})
    protos=[p for p in node.get("prototypes",[]) if isinstance(p,dict) and isinstance(p.get("center"),list)]
    sims=[(_similarity(v,p["center"]),p) for p in protos]; sims.sort(key=lambda z:z[0],reverse=True)
    best_sim,best=(sims[0] if sims else (0.0,None)); match=float(getattr(cfg,"LAYER12_RECUR_SIMILARITY",0.88))
    state_name="LEARNING"; reasons=[]; recurrent=False; matched_id=None
    mature=cp_state in {"STABLE","NEW_BASELINE"}
    if not protos:
        state_name="LEARNING"; reasons.append("NO_CONTEXT_PROTOTYPE")
    elif best_sim>=match:
        matched_id=best.get("id"); recurrent=node.get("active_id") not in {None,matched_id}
        state_name="RECURRENT_CONTEXT" if recurrent else "KNOWN_CONTEXT"
        reasons.append("PAST_CONTEXT_MATCH")
    elif cp_state in {"CHANGE_POINT","ADAPTATION","NEW_BASELINE"}:
        state_name="NOVEL_CANDIDATE"; reasons.append("NO_PAST_CONTEXT_MATCH")
    else:
        state_name="UNMATCHED_STABLE"; reasons.append("OUTSIDE_KNOWN_PROTOTYPES")

    fp=hashlib.sha256((pair+"|"+"|".join(f"{x:.3f}" for x in v)+"|"+cp_state).encode()).hexdigest()[:20]
    if fp!=node.get("last_fp"):
        boot_need=int(getattr(cfg,"LAYER12_BOOTSTRAP_SNAPSHOTS",12)); novel_need=int(getattr(cfg,"LAYER12_NOVEL_CONFIRM_SNAPSHOTS",4)); max_p=int(getattr(cfg,"LAYER12_MAX_PROTOTYPES",8))
        if not protos and mature:
            node["bootstrap"]=(node.get("bootstrap",[])+[v])[-boot_need:]
            if len(node["bootstrap"])>=boot_need:
                pid=int(node.get("next_id",1)); protos.append({"id":pid,"center":_mean(node["bootstrap"]),"samples":len(node["bootstrap"])}); node["next_id"]=pid+1; node["active_id"]=pid; node["bootstrap"]=[]
        elif best is not None and best_sim>=match and mature:
            # Slow prototype update prevents one new sample from rewriting memory.
            n=min(50,int(best.get("samples",1))); best["center"]=[(x*n+y)/(n+1) for x,y in zip(best["center"],v)]; best["samples"]=n+1; node["active_id"]=best.get("id"); node["candidate"]=[]
        elif cp_state in {"ADAPTATION","NEW_BASELINE"}:
            node["candidate"]=(node.get("candidate",[])+[v])[-novel_need:]
            if len(node["candidate"])>=novel_need:
                center=_mean(node["candidate"]); nearest=max((_similarity(center,p["center"]) for p in protos),default=0.0)
                if nearest<match:
                    pid=int(node.get("next_id",1)); protos.append({"id":pid,"center":center,"samples":len(node["candidate"])}); protos=protos[-max_p:]; node["next_id"]=pid+1; node["active_id"]=pid
                node["candidate"]=[]
        node["prototypes"]=protos; node["last_fp"]=fp; _save(state)
    return {"layer":12,"observe_only":True,"state":state_name,"change_point_state":cp_state,"prototype_count":len(protos),"matched_prototype":matched_id,"similarity":round(best_sim,4),"recurrent":recurrent,"reasons":reasons,"trade_effect":False,"direction_claim":False,"memory_claim":True}
