"""Unified ICT/SMC liquidity-route context.

Composes existing PD, external liquidity, IDM and structure facts into one ordered
lifecycle. Context only: no Telegram detector, no independent strategy and no
extra evidence family.

Route used for the *confirmation bonus*:
external source liquidity -> sweep + reclaim -> IDM/internal liquidity resolved
-> structure confirmed -> residual ERL target remains open.
A touch/pierce by itself is never a confirmation.
"""
from __future__ import annotations
from dataclasses import dataclass
import config as cfg
import premium_discount, idm, liquidity_context, structure_context

@dataclass(frozen=True)
class ICTRouteContext:
    side:int; state:str; location:str; idm_state:str; liquidity_state:str
    structure_state:str; target:float|None; alignment:int; ready:bool
    source_liquidity_state:str="UNKNOWN"; source_sweep_reclaimed:bool=False
    sequence_step:str="WAIT_SOURCE_LIQUIDITY"


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

    # For LONG the source-side pool is SSL; for SHORT it is BSL.  The shared
    # liquidity map already classifies the actual sweep and reclaim, so this
    # route does not create another liquidity detector/vote.
    source_side="SSL" if side>0 else "BSL"
    swept_side=getattr(liq,"sweep_side","") if liq else ""
    source_reclaimed=bool(liq and swept_side==source_side and getattr(liq,"sweep_reclaimed",False))
    source_state=("SWEPT_RECLAIMED" if source_reclaimed else
                  "SWEPT_NOT_RECLAIMED" if liq and swept_side==source_side else "UNSWEPT_OR_UNKNOWN")

    location_ok=bool(pd and pd.alignment>=0)  # EQ is neutral, not a rejection.
    idm_ok=bool(inducement and inducement.alignment>0)
    route_open=bool(liq and liq.residual_state=="OPEN")
    structure_ok=bool(struct_align>0 and structure_state in ("CONFIRMED","SHIFT_CONFIRMED"))

    require_source=bool(getattr(cfg,"ICT_ROUTE_REQUIRE_SOURCE_SWEEP_RECLAIM",True))
    source_ok=source_reclaimed or not require_source
    ready=location_ok and source_ok and idm_ok and route_open and structure_ok

    conflict=bool((pd and pd.alignment<0) or (inducement and inducement.alignment<0) or
                  (liq and liq.residual_state in ("LOW","EXHAUSTED")) or struct_align<0)
    # An actual source sweep without reclaim is an incomplete lifecycle, not a
    # reversal signal. Penalise only when configured; otherwise keep neutral.
    if liq and swept_side==source_side and not source_reclaimed and getattr(cfg,"ICT_ROUTE_UNRECLAIMED_SOURCE_IS_CONFLICT",True):
        conflict=True

    alignment=1 if ready else (-1 if conflict else 0)
    if ready:
        state="ROUTE_CONFIRMED"; step="READY_FOR_RETEST_CONFLUENCE"
    elif conflict:
        state="ROUTE_CONFLICT"; step="CONFLICT"
    elif not source_ok:
        state="WAIT_SOURCE_LIQUIDITY"; step="WAIT_SOURCE_SWEEP_RECLAIM"
    elif not idm_ok:
        state="WAIT_INTERNAL_LIQUIDITY"; step="WAIT_IDM"
    elif not structure_ok:
        state="WAIT_STRUCTURE"; step="WAIT_MSS_BOS"
    elif not route_open:
        state="WAIT_TARGET_CAPACITY"; step="WAIT_OPEN_ERL"
    else:
        state="WAIT_CONFLUENCE"; step="WAIT_CONFLUENCE"

    return ICTRouteContext(side,state,location,idm_state,liquidity_state,structure_state,
                           getattr(liq,"erl_target",None) if liq else None,alignment,ready,
                           source_state,source_reclaimed,step)


def score_delta(ctx:ICTRouteContext|None)->int:
    if not ctx:return 0
    # Bounded coherence adjustment inside the existing liquidity-route family.
    if ctx.ready:return int(getattr(cfg,"ICT_ROUTE_CONFIRM_BONUS",2))
    if ctx.alignment<0:return -int(getattr(cfg,"ICT_ROUTE_CONFLICT_PENALTY",3))
    return 0


def describe(ctx:ICTRouteContext|None)->str:
    if ctx is None:return "ICT-маршрут: данных недостаточно"
    side="ЛОНГ" if ctx.side>0 else "ШОРТ"
    state={"ROUTE_CONFIRMED":"маршрут подтверждён",
           "ROUTE_CONFLICT":"есть конфликт",
           "WAIT_SOURCE_LIQUIDITY":"ждём снятие и возврат внешней ликвидности",
           "WAIT_INTERNAL_LIQUIDITY":"ждём снятие внутренней ликвидности IDM",
           "WAIT_STRUCTURE":"ликвидность снята; ждём MSS/BOS",
           "WAIT_TARGET_CAPACITY":"структура подтверждена; нет открытого ERL-потенциала",
           "WAIT_CONFLUENCE":"ждём ретест/реакцию FVG или OB"}.get(ctx.state,ctx.state)
    source={"SWEPT_RECLAIMED":"снята+возврат","SWEPT_NOT_RECLAIMED":"снята без возврата",
            "UNSWEPT_OR_UNKNOWN":"не снята/нет данных"}.get(ctx.source_liquidity_state,ctx.source_liquidity_state)
    target=f" · ERL {ctx.target:.5f}" if ctx.target is not None else ""
    return (f"ICT-маршрут {side}: {state} · внешняя ликвидность {source} · зона {ctx.location} · "
            f"IDM {ctx.idm_state} · ERL {ctx.liquidity_state} · структура {ctx.structure_state}{target}")
