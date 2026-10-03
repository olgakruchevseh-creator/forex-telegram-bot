"""Farley-style pattern-failure lifecycle, context only.

Replays recent HTF structural-pattern confirmations on historical prefixes and
checks whether price subsequently reclaimed the trigger in the opposite
direction. It never emits Telegram alerts and never forms a KILLER family.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from analysis import atr, closed_candles
import config as cfg
import patterns

TF_MINUTES={"W1":10080,"D1":1440,"H4":240,"H1":60}

@dataclass(frozen=True)
class PatternFailureContext:
    available: bool=False
    state: str="NONE"
    original_pattern: str=""
    original_side: str=""
    failure_side: str=""
    timeframe: str=""
    trigger: float=0.0
    reclaim_closes: int=0
    displacement: bool=False
    alignment: int=0
    score: int=50
    reason: str=""
    family: str="CORRELATED_PRICE_ACTION_CONTEXT"
    def as_dict(self): return asdict(self)

def _side(direction):
    return 1 if direction in (1,"LONG") else -1 if direction in (-1,"SHORT") else 0

def analyze_symbol(symbol, by_tf, direction):
    if not getattr(cfg,"PATTERN_FAILURE_CONTEXT_ENABLED",True): return None
    wanted=_side(direction)
    if not wanted:return None
    lookback=int(getattr(cfg,"PATTERN_FAILURE_LOOKBACK_BARS",8))
    best=None
    for tf in ("H1","H4","D1","W1"):
        raw=(by_tf or {}).get(tf) or []
        if raw and not hasattr(raw[-1], "dt"):
            continue
        bars=closed_candles(raw,TF_MINUTES[tf])
        if len(bars)<35: continue
        start=max(30,len(bars)-lookback-1)
        for end in range(start,len(bars)-1):
            hist=bars[:end+1]
            for p in patterns.structural_patterns(tf,hist):
                if str(p.dt)!=str(hist[-1].dt): continue
                original=1 if p.side=="LONG" else -1
                failure=-original
                if failure!=wanted: continue
                after=bars[end+1:]
                if not after: continue
                av=atr(hist,14) or abs(hist[-1].high-hist[-1].low) or 1e-12
                tol=av*float(getattr(cfg,"PATTERN_FAILURE_RECLAIM_ATR",.08))
                trigger=float(p.level)
                reclaimed=[x for x in after if (x.close<trigger-tol if original>0 else x.close>trigger+tol)]
                closes=len(reclaimed)
                last=after[-1]
                body=abs(last.close-last.open)
                disp=body>=av*float(getattr(cfg,"PATTERN_FAILURE_DISPLACEMENT_ATR",.40)) and ((last.close-last.open)*failure>0)
                if closes>=1:
                    score=min(90,76+min(8,closes*3)+(6 if disp else 0))
                    state="ПРОВАЛ ПАТТЕРНА ПОДТВЕРЖДЁН" if closes>=2 or disp else "ВОЗВРАТ ЗА ТРИГГЕР ПАТТЕРНА"
                    best=PatternFailureContext(True,state,p.name,p.side,"LONG" if failure>0 else "SHORT",tf,trigger,closes,disp,1,score,"ожидаемое продолжение паттерна не удержалось; цена вернулась за его trigger/neckline")
        if best: break
    return best

def score_delta(ctx,direction):
    if not ctx:return 0
    return 2 if ctx.alignment>0 and ctx.state=="ПРОВАЛ ПАТТЕРНА ПОДТВЕРЖДЁН" else 0

def describe(ctx):
    if not ctx:return "Провал паттерна: не подтверждён"
    return (f"Провал паттерна: {ctx.state} · {ctx.original_pattern} {ctx.timeframe} "
            f"{ctx.original_side} → {ctx.failure_side} · возвратов {ctx.reclaim_closes}.")
