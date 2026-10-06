"""Layer 16 — Structured Intelligence Bus (OBSERVE_ONLY).

Canonical, machine-readable facts from existing expert modules.  This layer is
not a strategy, vote, veto or Telegram source.  It prevents downstream brains
from having to infer market events by parsing Russian/English card text.

Design rules:
- closed-candle expert modules remain the source of truth;
- one correlated family -> one canonical fact;
- lifecycle states are explicit (forming/confirmed/threatened/shift);
- provenance is retained so later replay/calibration can audit every fact;
- no thresholds/probabilities/trade verdicts are modified.
"""
from __future__ import annotations

import config as cfg
import structure_context
import liquidity_context
import choch
import mss
import cisd
import market_regime
import premium_discount


def _side(v):
    if isinstance(v, str):
        u=v.upper()
        return 1 if u in {"LONG","BUY","ЛОНГ"} else -1 if u in {"SHORT","SELL","ШОРТ"} else 0
    return 1 if v and v > 0 else -1 if v and v < 0 else 0


def _fact(family, event, side=0, tf="", state="", level=None, **extra):
    out={"family":family,"event":event,"side":int(side or 0),"timeframe":tf or "",
         "state":state or "CONFIRMED","source":"DIRECT_MODULE_STATE"}
    if level is not None: out["level"]=float(level)
    out.update({k:v for k,v in extra.items() if v is not None})
    return out


def assess(pair:str, side, by_tf:dict, ctx:dict|None=None)->dict:
    if not getattr(cfg,"LAYER16_STRUCTURED_INTELLIGENCE_ENABLED",True):
        return {"layer":16,"observe_only":True,"state":"DISABLED","trade_effect":False,"facts":[]}
    s=_side(side); facts=[]; errors=[]

    # 1) Controlling swing/internal structure.  One family, never multiple votes.
    try:
        st=structure_context.analyze_symbol(pair,by_tf,s)
        if st:
            facts.append(_fact("STRUCTURE","MARKET_STRUCTURE",st.side,st.timeframe,st.state,
                sequence=st.sequence,hierarchy=st.hierarchy_state,
                swing_side=st.swing_side,swing_timeframe=st.swing_timeframe,
                substructure_side=st.substructure_side,substructure_timeframe=st.substructure_timeframe))
    except Exception as e: errors.append("structure:"+type(e).__name__)

    # 2) Explicit delivery/structure transitions.  Preserve levels and displacement.
    for name,mod,event in (("cisd",cisd,"CISD"),("choch",choch,"CHOCH"),("mss",mss,"MSS")):
        try:
            x=mod.analyze_symbol(pair,by_tf,s)
            if x:
                xs=getattr(x,"side",s if getattr(x,"alignment",0)>=0 else -s)
                facts.append(_fact("STRUCTURE_DELIVERY_SHIFT",event,xs,getattr(x,"timeframe",""),
                    "CONFIRMED" if getattr(x,"alignment",0)!=0 else "CONFLICT",
                    getattr(x,"level",None),displacement_atr=getattr(x,"displacement_atr",None),
                    alignment=getattr(x,"alignment",None)))
        except Exception as e: errors.append(name+":"+type(e).__name__)

    # 3) Liquidity route + sweep/reclaim are direct states, not text matches.
    try:
        liq=liquidity_context.analyze_symbol(pair,by_tf,s,events=None)
        if liq:
            facts.append(_fact("LIQUIDITY","IRL_ERL_ROUTE",s,liq.timeframe,liq.residual_state,
                liq.erl_target,route=liq.route,distance_atr=liq.erl_distance_atr,
                bsl_level=liq.bsl_level,bsl_status=liq.bsl_status,
                ssl_level=liq.ssl_level,ssl_status=liq.ssl_status))
            if liq.sweep_side:
                sweep_side=1 if str(liq.sweep_side).upper() in {"SSL","LOW","BULL","LONG"} else -1
                facts.append(_fact("LIQUIDITY","SWEEP_RECLAIM" if liq.sweep_reclaimed else "SWEEP",
                    sweep_side,liq.timeframe,"RECLAIMED" if liq.sweep_reclaimed else "SWEPT"))
    except Exception as e: errors.append("liquidity:"+type(e).__name__)

    # 4) Premium/discount and regime are context labels only.
    try:
        pd=premium_discount.analyze_symbol(pair,by_tf,s)
        if pd:
            facts.append(_fact("LOCATION","PREMIUM_DISCOUNT",s,getattr(pd,"timeframe",""),
                getattr(pd,"zone",getattr(pd,"state","CONTEXT")),
                position=getattr(pd,"position",None)))
    except Exception as e: errors.append("premium_discount:"+type(e).__name__)
    try:
        rg=market_regime.analyze_symbol(pair,by_tf)
        if rg:
            facts.append(_fact("REGIME","MARKET_REGIME",0,getattr(rg,"timeframe",""),
                getattr(rg,"regime",getattr(rg,"state",str(rg)))))
    except Exception as e: errors.append("regime:"+type(e).__name__)

    # Canonical ordered chain.  A family can contribute several lifecycle stages,
    # but duplicate identical events are collapsed deterministically.
    seen=set(); canonical=[]
    for f in facts:
        key=(f["family"],f["event"],f["side"],f["timeframe"],f["state"],f.get("level"))
        if key not in seen: seen.add(key); canonical.append(f)
    events={f["event"] for f in canonical}
    sequence=[x for x in ("SWEEP","SWEEP_RECLAIM","CISD","CHOCH","MSS","MARKET_STRUCTURE") if x in events]
    return {"layer":16,"observe_only":True,"state":"STRUCTURED" if canonical else "NO_FACTS",
            "pair":pair,"candidate_side":s,"facts":canonical,"event_sequence":sequence,
            "errors":errors,"closed_h1_policy_preserved":True,"m15_m5_confirmation_only":True,
            "trade_effect":False,"direction_claim":False,"threshold_effect":False,
            "basis":"DIRECT_EXPERT_STATES_NOT_TEXT_PARSING"}
