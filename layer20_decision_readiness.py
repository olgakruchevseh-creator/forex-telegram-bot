"""Layer 20 — Decision Readiness Synthesis (OBSERVE_ONLY).

Final diagnostic brain: summarizes maturity, integrity and evidence diversity from
Layers 15–19. It deliberately does not emit a signal or alter thresholds/probability.
"""
from __future__ import annotations
import config as cfg


def assess(pair: str, side, event_sequence: dict | None, structured: dict | None,
           graph: dict | None, integrity: dict | None, independence: dict | None) -> dict:
    if not getattr(cfg,"LAYER20_DECISION_READINESS_ENABLED",True):
        return {"layer":20,"observe_only":True,"state":"DISABLED","trade_effect":False}
    es=event_sequence if isinstance(event_sequence,dict) else {}
    st=structured if isinstance(structured,dict) else {}
    gr=graph if isinstance(graph,dict) else {}
    it=integrity if isinstance(integrity,dict) else {}
    ind=independence if isinstance(independence,dict) else {}
    reasons=[]
    integrity_state=it.get("integrity","UNKNOWN")
    diversity=ind.get("independence","UNKNOWN")
    facts=len(st.get("facts",[])) if isinstance(st.get("facts"),list) else 0
    edges=len(gr.get("edges",[])) if isinstance(gr.get("edges"),list) else 0
    if integrity_state == "CONFLICTED": reasons.append("SCENARIO_CONFLICTED")
    elif integrity_state in {"PARTIAL","UNKNOWN"}: reasons.append("SCENARIO_NOT_FULLY_COHERENT")
    if diversity == "CONCENTRATED": reasons.append("EVIDENCE_CONCENTRATED")
    elif diversity == "UNKNOWN": reasons.append("EVIDENCE_DIVERSITY_UNKNOWN")
    if facts < 2: reasons.append("TOO_FEW_CANONICAL_FACTS")
    if not edges and facts >= 2: reasons.append("NO_CAUSAL_LINKS")
    # Diagnostic classification only. It is intentionally not a trading gate.
    if integrity_state == "COHERENT" and diversity in {"DIVERSE","MIXED"} and facts >= 2:
        readiness="MATURE"
    elif integrity_state == "CONFLICTED": readiness="CONFLICTED"
    elif facts:
        readiness="DEVELOPING"
    else:
        readiness="UNKNOWN"
    return {"layer":20,"observe_only":True,"state":"READINESS_READY" if facts else "NO_FACTS",
            "pair":pair,"candidate_side":side,"readiness":readiness,"diagnostics":reasons,
            "canonical_fact_count":facts,"causal_edge_count":edges,
            "scenario_integrity":integrity_state,"evidence_independence":diversity,
            "event_sequence_state":es.get("state"),"timestamp_freshness":it.get("timestamp_freshness","UNAVAILABLE"),
            "closed_h1_policy_preserved":True,"m15_m5_confirmation_only":True,
            "trade_effect":False,"direction_claim":False,"threshold_effect":False,
            "probability_effect":False,"veto_effect":False,"telegram_effect":False,
            "basis":"FINAL_OBSERVE_ONLY_SYNTHESIS_NOT_A_TRADING_GATE"}
