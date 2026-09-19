"""Shared Precision Entry / Entry Refinement context: OTE + CE + IOFED.

This layer is deliberately NOT a Telegram detector and never creates LONG/SHORT.
It refines an already confirmed thesis after structure/liquidity/displacement and
zone evidence. OTE, CE and IOFED are one correlated ENTRY_LOCATION/EXECUTION
family, never three independent votes.
"""
from __future__ import annotations
from dataclasses import dataclass
import re

import config as cfg
import ohlc_movement
from analysis import atr, closed_candles, zigzag


@dataclass(frozen=True)
class PrecisionEntryContext:
    available: bool = False
    ready: bool = False
    side: str = ""
    tf: str = "H1"
    price: float = 0.0
    ote_low: float = 0.0
    ote_high: float = 0.0
    ce: float = 0.0
    zone_low: float = 0.0
    zone_high: float = 0.0
    in_ote: bool = False
    near_ce: bool = False
    has_structure: bool = False
    has_sweep: bool = False
    has_displacement: bool = False
    has_zone: bool = False
    reaction_confirmed: bool = False
    reaction_path: str = ""
    reason: str = ""
    family: str = "entry_location_execution"


def _same_side(texts, side: str) -> list[str]:
    out=[]
    for t in texts or []:
        u=(t or "").upper()
        if re.search(rf"(?:^|\n)[🟢🔴]?\s*{side}\s+[A-Z]{{3}}/[A-Z]{{3}}", t or "", re.I) or re.search(rf"НАПРАВЛЕНИЕ(?:\s+[^:\n]+)?:\s*{side}\b",u):
            out.append(t)
    return out


def _zone_from_texts(texts: list[str]) -> tuple[float,float]:
    # Prefer explicit execution zones (MB/OB/FVG/BPR); avoid TR/target lines.
    keys=("MITIGATION BLOCK","ORDER BLOCK","BREAKER BLOCK","BPR","BALANCED PRICE RANGE","FVG","IMBALANCE","ИМБАЛАНС","ЗОНА")
    for text in reversed(texts):
        for line in (text or "").splitlines():
            u=line.upper()
            if not any(k in u for k in keys) or any(k in u for k in ("TR1","TR2","TR3","ЦЕЛЬ")):
                continue
            nums=[]
            for raw in re.findall(r"(?<![A-Z0-9])\d{1,3}\.\d{3,5}(?!\d)",line):
                try: nums.append(float(raw))
                except ValueError: pass
            if len(nums)>=2:
                a,b=nums[-2],nums[-1]
                if a>0 and b>0 and max(a,b)/min(a,b)<1.25:
                    return min(a,b),max(a,b)
    return 0.0,0.0


def _facts(texts: list[str]) -> tuple[bool,bool,bool,bool,str]:
    u="\n".join(texts).upper()
    structure=any(k in u for k in ("MSS","BOS","CHOCH","STRUCTURE SHIFT","СЛОМ СТРУКТУР"))
    sweep=any(k in u for k in ("LIQUIDITY SWEEP","СНЯТИЕ ЛИКВИДНОСТИ","SWEEP","PDH","PDL","EQH","EQL","INDUCEMENT","IDM"))
    displacement=any(k in u for k in ("DISPLACEMENT","ИМПУЛЬС","FVG","IMBALANCE","ИМБАЛАНС","BPR"))
    reaction=any(k in u for k in ("РЕТЕСТ ПОДТВЕРЖДЁН","REACTION_CONFIRMED","SWEEP_RECLAIM","RECOVERY_CLOSURE"))
    path=""
    m=re.search(r"Подтверждение зоны:\s*([^\n]+)","\n".join(texts),re.I)
    if m and m.group(1).strip() not in ("—","-"): reaction=True; path=m.group(1).strip()
    elif "SWEEP_RECLAIM" in u: path="SWEEP_RECLAIM"
    elif "RECOVERY_CLOSURE" in u: path="RECOVERY_CLOSURE"
    return structure,sweep,displacement,reaction,path


def analyze(pair: str, side: str, by_tf: dict, texts: list[str] | None) -> PrecisionEntryContext:
    if not getattr(cfg,"PRECISION_ENTRY_ENABLED",True):
        return PrecisionEntryContext(reason="disabled")
    side=(side or "").upper()
    if side not in ("LONG","SHORT"):
        return PrecisionEntryContext(reason="bad_side")
    same=_same_side(texts,side)
    h1=closed_candles((by_tf or {}).get("H1") or [],60)
    if len(h1)<25:
        return PrecisionEntryContext(side=side,reason="insufficient_h1")
    av=atr(h1,14)
    if av<=0:
        return PrecisionEntryContext(side=side,reason="insufficient_atr")
    price=float(h1[-1].close)
    piv=zigzag(h1,float(getattr(cfg,"PRECISION_ENTRY_ZIGZAG_PCT",.18)),int(getattr(cfg,"ZIGZAG_MIN_BARS",3)))
    if len(piv)<2:
        return PrecisionEntryContext(side=side,price=price,reason="impulse_missing")
    # Find the freshest completed impulse matching the requested side.
    start=end=None
    for a,b in reversed(list(zip(piv[:-1],piv[1:]))):
        if side=="LONG" and a.kind=="low" and b.kind=="high": start,end=a,b; break
        if side=="SHORT" and a.kind=="high" and b.kind=="low": start,end=a,b; break
    if start is None:
        return PrecisionEntryContext(side=side,price=price,reason="matching_impulse_missing")
    move=abs(float(end.price)-float(start.price))
    if move < av*float(getattr(cfg,"PRECISION_ENTRY_MIN_IMPULSE_ATR",1.2)):
        return PrecisionEntryContext(side=side,price=price,reason="impulse_too_small")
    lo_r=float(getattr(cfg,"PRECISION_ENTRY_OTE_MIN",.62)); hi_r=float(getattr(cfg,"PRECISION_ENTRY_OTE_MAX",.79))
    if side=="LONG":
        ote=sorted((float(end.price)-move*lo_r,float(end.price)-move*hi_r))
    else:
        ote=sorted((float(end.price)+move*lo_r,float(end.price)+move*hi_r))
    zl,zh=_zone_from_texts(same); has_zone=zl>0 and zh>zl
    ce=(zl+zh)/2 if has_zone else 0.0
    tol=av*float(getattr(cfg,"PRECISION_ENTRY_CE_TOLERANCE_ATR",.12))
    in_ote=ote[0]-tol <= price <= ote[1]+tol
    near_ce=bool(has_zone and abs(price-ce)<=max(tol,(zh-zl)*.35))
    structure,sweep,disp,reaction,path=_facts(same)
    # OTE/CE are location refinements, not signals. IOFED is considered READY
    # only after the pre-existing confirmation chain is present.
    prerequisites=structure and sweep and disp and has_zone
    location=in_ote and near_ce
    direction=1 if side=="LONG" else -1
    guard=ohlc_movement.guard_event(by_tf or {},direction,82)
    early=ohlc_movement.early_entry_check(by_tf or {},direction)
    ohlc_ok=bool(guard.get("allow",True) and not guard.get("weak_reversal"))
    early_ok=bool(early.get("allow",True))
    ready=bool(prerequisites and location and reaction and ohlc_ok and early_ok)
    if not prerequisites: reason="prerequisites_missing"
    elif not location: reason="outside_ote_ce"
    elif not reaction: reason="zone_reaction_missing"
    elif not ohlc_ok: reason="ohlc_contradiction"
    elif not early_ok: reason=early.get("reason","late_entry")
    else: reason="iofed_ready"
    return PrecisionEntryContext(True,ready,side,"H1",price,ote[0],ote[1],ce,zl,zh,in_ote,near_ce,structure,sweep,disp,has_zone,reaction,path,reason)


def score_delta(ctx: PrecisionEntryContext | None) -> float:
    """Small contextual adjustment; never three votes for OTE+CE+IOFED."""
    if not ctx or not ctx.available: return 0.0
    if ctx.ready: return float(getattr(cfg,"PRECISION_ENTRY_READY_SCORE_BONUS",4.0))
    if ctx.reason in ("outside_ote_ce","zone_reaction_missing"): return -2.0
    return 0.0


def describe(ctx: PrecisionEntryContext | None) -> str:
    if not ctx or not ctx.available:
        return "Precision Entry: контекст пока не сформирован"
    def px(v): return f"{v:.5f}" if v else "—"
    loc=f"OTE {px(ctx.ote_low)}–{px(ctx.ote_high)}"
    if ctx.ce: loc+=f" · CE {px(ctx.ce)}"
    if ctx.ready:
        return f"Precision Entry: IOFED READY · {loc} · Zone Reaction/OHLC/late-entry подтверждены"
    labels={"outside_ote_ce":"цена вне совместной OTE/CE области","zone_reaction_missing":"ждём подтверждённую реакцию зоны","prerequisites_missing":"цепочка Sweep→MSS/BOS→displacement→zone ещё неполна","ohlc_contradiction":"OHLC не подтверждает исполнение","late_entry":"вход уже поздний"}
    return f"Precision Entry: {loc} · {labels.get(ctx.reason,ctx.reason)}"
