"""Market Maker Model (MMM) orchestration context.

Internal scenario layer only: it never emits LONG/SHORT or Telegram events.
SELL: Premium/upper ERL -> buy-side sweep -> MSS/BOS -> bearish displacement
-> PD Array -> return/refinement (OTE/CE/IOFED) -> confirmed zone reaction/OHLC
-> residual/late-entry guard -> IRL / lower ERL.
BUY is exactly mirrored. Existing engines remain the source of every fact.
"""
from __future__ import annotations
from dataclasses import dataclass
import re

import config as cfg
import ohlc_movement
import precision_entry
from analysis import closed_candles


@dataclass(frozen=True)
class MMMContext:
    available: bool = False
    side: str = ""
    model: str = ""
    stage: str = "NONE"
    stage_no: int = 0
    confirmed: bool = False
    premium_discount_ok: bool = False
    erl_ok: bool = False
    sweep_ok: bool = False
    structure_ok: bool = False
    displacement_ok: bool = False
    pd_array_ok: bool = False
    return_ok: bool = False
    precision_ok: bool = False
    reaction_ok: bool = False
    ohlc_ok: bool = False
    residual_ok: bool = True
    late_entry_ok: bool = True
    reason: str = ""
    family: str = "scenario_orchestration"


def _same_side(texts, pair, side):
    out=[]
    for t in texts or []:
        u=(t or "").upper()
        p=re.search(r"(?:ПАРА:\s*|💱 ПАРА:\s*)([A-Z]{3}/[A-Z]{3})",u) or re.search(r"(?:LONG|SHORT)\s+([A-Z]{3}/[A-Z]{3})",u)
        s=re.search(r"(?:^|\n)[🟢🔴]?\s*(LONG|SHORT)\s+[A-Z]{3}/[A-Z]{3}",t or "",re.I) or re.search(r"НАПРАВЛЕНИЕ(?:\s+[^:\n]+)?:\s*(LONG|SHORT)\b",u)
        if p and s and p.group(1)==pair and s.group(1).upper()==side: out.append(t)
    return out


def _range_location(by_tf, side):
    bars=closed_candles((by_tf or {}).get("H4") or [],240)
    if len(bars)<20: bars=closed_candles((by_tf or {}).get("H1") or [],60)
    if len(bars)<20: return False
    bars=bars[-60:]; lo=min(float(x.low) for x in bars); hi=max(float(x.high) for x in bars)
    if hi<=lo:return False
    pos=(float(bars[-1].close)-lo)/(hi-lo)
    # Allow a modest CE tolerance: MMM is a scenario, not a mechanical 50% trigger.
    return pos>=.50 if side=="SHORT" else pos<=.50


def analyze(pair: str, side: str, by_tf: dict, texts: list[str] | None,
            liquidity_ctx=None, precision_ctx=None) -> MMMContext:
    if not getattr(cfg,"MARKET_MAKER_MODEL_ENABLED",True): return MMMContext(reason="disabled")
    side=(side or "").upper()
    if side not in ("LONG","SHORT"): return MMMContext(reason="bad_side")
    same=_same_side(texts,pair,side); u="\n".join(same).upper()
    model="MMM_BUY" if side=="LONG" else "MMM_SELL"
    loc=_range_location(by_tf,side)

    # ERL/sweep facts are consumed from existing liquidity alerts/context, never recreated as votes.
    if side=="SHORT":
        erl_marks=("ERL HIGH","BUY-SIDE","BUY SIDE","PDH","EQH")
    else:
        erl_marks=("ERL LOW","SELL-SIDE","SELL SIDE","PDL","EQL")
    erl=any(x in u for x in erl_marks)
    sweep=("SWEEP" in u or "СНЯТИЕ ЛИКВИДНОСТИ" in u) and erl
    structure=any(x in u for x in ("MSS","BOS","CHOCH","STRUCTURE SHIFT","СЛОМ СТРУКТУР"))
    displacement=any(x in u for x in ("DISPLACEMENT","ИМПУЛЬС","FVG","IMBALANCE","ИМБАЛАНС","BPR"))
    pd_array=any(x in u for x in ("FVG","IFVG","I-FVG","IMBALANCE","ИМБАЛАНС","BREAKER","MITIGATION BLOCK","ORDER BLOCK","BPR"))
    reaction=any(x in u for x in ("РЕТЕСТ ПОДТВЕРЖДЁН","REACTION_CONFIRMED","SWEEP_RECLAIM","RECOVERY_CLOSURE")) or ("ПОДТВЕРЖДЕНИЕ ЗОНЫ:" in u and "ПОДТВЕРЖДЕНИЕ ЗОНЫ: —" not in u)
    if precision_ctx is None:
        precision_ctx=precision_entry.analyze(pair,side,by_tf,texts)
    precision=bool(precision_ctx and precision_ctx.ready)
    returned=bool(precision_ctx and precision_ctx.available and (precision_ctx.in_ote or precision_ctx.near_ce)) or reaction
    direction=1 if side=="LONG" else -1
    guard=ohlc_movement.guard_event(by_tf or {},direction,82); early=ohlc_movement.early_entry_check(by_tf or {},direction)
    ohlc_ok=bool(guard.get("allow",True) and not guard.get("weak_reversal")); late_ok=bool(early.get("allow",True))
    residual_ok=not bool(liquidity_ctx and getattr(liquidity_ctx,"residual_state","")=="EXHAUSTED")

    checks=[loc,erl,sweep,structure,displacement,pd_array,returned,precision,reaction,ohlc_ok and late_ok and residual_ok]
    names=["HTF_LOCATION","ERL","LIQUIDITY_SWEEP","MSS_BOS","DISPLACEMENT","PD_ARRAY","RETURN","PRECISION_ENTRY","ZONE_REACTION","CONFIRMED"]
    n=0
    for ok in checks:
        if not ok: break
        n+=1
    stage=names[n-1] if n else "FORMING"
    confirmed=(n==len(checks))
    reason="confirmed" if confirmed else f"waiting_{names[n].lower()}"
    return MMMContext(True,side,model,stage,n,confirmed,loc,erl,sweep,structure,displacement,pd_array,returned,precision,reaction,ohlc_ok,residual_ok,late_ok,reason)


def score_delta(ctx: MMMContext | None) -> float:
    """Scenario coherence adjustment only; MMM never becomes an independent family/vote."""
    if not ctx or not ctx.available:return 0.0
    if ctx.confirmed:return float(getattr(cfg,"MARKET_MAKER_MODEL_CONFIRMED_BONUS",3.0))
    if not ctx.residual_ok or not ctx.late_entry_ok:return -4.0
    return 0.0


def describe(ctx: MMMContext | None) -> str:
    if not ctx or not ctx.available:return "MMM: контекст пока не сформирован"
    status="CONFIRMED" if ctx.confirmed else ctx.stage
    return f"{ctx.model}: {status} · stage {ctx.stage_no}/10"
