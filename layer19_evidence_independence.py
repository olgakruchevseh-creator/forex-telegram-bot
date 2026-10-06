"""Layer 19 — Evidence Independence / Correlation Audit (OBSERVE_ONLY).

Measures whether a scenario is supported by genuinely different evidence families or
by many correlated facts from the same family. It never changes trading decisions.
"""
from __future__ import annotations
import config as cfg

_DIRECTIONAL = {"STRUCTURE", "STRUCTURE_DELIVERY_SHIFT", "LIQUIDITY", "LOCATION"}


def _side(v):
    if isinstance(v, str):
        u=v.upper(); return 1 if u in {"LONG","BUY","ЛОНГ"} else -1 if u in {"SHORT","SELL","ШОРТ"} else 0
    return 1 if v and v > 0 else -1 if v and v < 0 else 0


def assess(pair: str, side, structured: dict | None, graph: dict | None,
           integrity: dict | None = None) -> dict:
    if not getattr(cfg, "LAYER19_EVIDENCE_INDEPENDENCE_ENABLED", True):
        return {"layer":19,"observe_only":True,"state":"DISABLED","trade_effect":False}
    structured=structured if isinstance(structured,dict) else {}
    graph=graph if isinstance(graph,dict) else {}
    integrity=integrity if isinstance(integrity,dict) else {}
    candidate=_side(side)
    facts=structured.get("facts") if isinstance(structured.get("facts"),list) else []
    aligned=[]; opposed=[]; neutral=[]
    for f in facts:
        if not isinstance(f,dict): continue
        fam=str(f.get("family","UNKNOWN")); s=_side(f.get("side"))
        rec={"family":fam,"event":f.get("event"),"timeframe":f.get("timeframe","")}
        if s and candidate and s==candidate: aligned.append(rec)
        elif s and candidate and s==-candidate: opposed.append(rec)
        else: neutral.append(rec)
    aligned_families=sorted({x["family"] for x in aligned})
    opposed_families=sorted({x["family"] for x in opposed})
    counts={}
    for x in aligned: counts[x["family"]]=counts.get(x["family"],0)+1
    total=len(aligned)
    max_share=(max(counts.values())/total) if total and counts else 0.0
    if not aligned:
        independence="UNKNOWN"
    elif len(aligned_families)>=3 and max_share <= 0.60:
        independence="DIVERSE"
    elif len(aligned_families)>=2:
        independence="MIXED"
    else:
        independence="CONCENTRATED"
    warnings=[]
    if total >= 2 and max_share >= 0.75:
        warnings.append("CORRELATED_FAMILY_CONCENTRATION")
    if opposed_families:
        warnings.append("OPPOSING_INDEPENDENT_FAMILY")
    if integrity.get("integrity") == "CONFLICTED":
        warnings.append("UPSTREAM_SCENARIO_CONFLICT")
    return {"layer":19,"observe_only":True,"state":"INDEPENDENCE_READY" if facts else "NO_FACTS",
            "pair":pair,"candidate_side":candidate,"independence":independence,
            "aligned_fact_count":total,"aligned_family_count":len(aligned_families),
            "aligned_families":aligned_families,"opposed_families":opposed_families,
            "max_aligned_family_share":round(max_share,4),"warnings":warnings,
            "closed_h1_policy_preserved":True,"m15_m5_confirmation_only":True,
            "trade_effect":False,"direction_claim":False,"threshold_effect":False,
            "probability_effect":False,"veto_effect":False,
            "basis":"CORRELATED_EVIDENCE_IS_NOT_COUNTED_AS_INDEPENDENT_CONFIRMATION"}
