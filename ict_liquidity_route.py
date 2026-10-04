"""Unified ICT liquidity-route context.

Composes already existing PD, IDM, IRL/ERL/liquidity and structure facts into one
ordered narrative. It is context only: no Telegram detector, no independent
strategy and no extra evidence family.
"""
from __future__ import annotations
from dataclasses import dataclass
import config as cfg
import premium_discount, idm, liquidity_context, structure_context

@dataclass(frozen=True)
class ICTRouteContext:
    side:int; state:str; location:str; idm_state:str; liquidity_state:str
    structure_state:str; target:float|None; alignment:int; ready:bool


def analyze_symbol(symbol:str, by_tf:dict, side:int, events=None)->ICTRouteContext|None:
    if not getattr(cfg,"ICT_ROUTE_ENABLED",True) or side not in (-1,1): return None
    pd=premium_discount.analyze_symbol(symbol,by_tf,side)
    inducement=idm.analyze_symbol(symbol,by_tf,side)
    liq=liquidity_context.analyze_symbol(symbol,by_tf,side,events)
    struct=structure_context.analyze_symbol(symbol,by_tf,side)
    location=getattr(pd,"position","UNKNOWN") if pd else "UNKNOWN"
    idm_state=("SWEPT_RECLAIMED" if inducement and inducement.alignment>0 else
               "UNSWEPT_NEAR" if inducement and inducement.alignment<0 else "NEUTRAL")
    liquidity_state=getattr(liq,"residual_state","UNKNOWN") if liq else "UNKNOWN"
    struct_align=structure_context.alignment(struct,side) if struct else 0
    structure_state=getattr(struct,"state","FORMING") if struct else "FORMING"
    location_ok=bool(pd and pd.alignment>=0)  # EQ is neutral, not a rejection.
    idm_ok=bool(inducement and inducement.alignment>0)
    route_open=bool(liq and liq.residual_state=="OPEN")
    structure_ok=bool(struct_align>0 and structure_state in ("CONFIRMED","SHIFT_CONFIRMED"))
    ready=location_ok and idm_ok and route_open and structure_ok
    conflict=bool((pd and pd.alignment<0) or (inducement and inducement.alignment<0) or
                  (liq and liq.residual_state in ("LOW","EXHAUSTED")) or struct_align<0)
    alignment=1 if ready else (-1 if conflict else 0)
    if ready: state="ROUTE_CONFIRMED"
    elif conflict: state="ROUTE_CONFLICT"
    elif idm_ok: state="WAIT_STRUCTURE_OR_TARGET"
    else: state="WAIT_LIQUIDITY_EVENT"
    return ICTRouteContext(side,state,location,idm_state,liquidity_state,structure_state,
                           getattr(liq,"erl_target",None) if liq else None,alignment,ready)


def score_delta(ctx:ICTRouteContext|None)->int:
    if not ctx:return 0
    # This is a bounded coherence adjustment inside the existing liquidity route,
    # never a new family/vote.
    if ctx.ready:return int(getattr(cfg,"ICT_ROUTE_CONFIRM_BONUS",2))
    if ctx.alignment<0:return -int(getattr(cfg,"ICT_ROUTE_CONFLICT_PENALTY",3))
    return 0


def describe(ctx:ICTRouteContext|None)->str:
    if ctx is None:return "ICT-маршрут: данных недостаточно"
    side="ЛОНГ" if ctx.side>0 else "ШОРТ"
    state={"ROUTE_CONFIRMED":"маршрут подтверждён","ROUTE_CONFLICT":"есть конфликт",
           "WAIT_STRUCTURE_OR_TARGET":"ликвидность снята; ждём структуру/открытую цель",
           "WAIT_LIQUIDITY_EVENT":"ждём снятие внутренней ликвидности"}.get(ctx.state,ctx.state)
    target=f" · ERL {ctx.target:.5f}" if ctx.target is not None else ""
    return (f"ICT-маршрут {side}: {state} · зона {ctx.location} · IDM {ctx.idm_state} · "
            f"ERL {ctx.liquidity_state} · структура {ctx.structure_state}{target}")
