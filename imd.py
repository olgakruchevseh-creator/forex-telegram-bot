"""Intermarket Divergence — internal USD-major cross-check for Master Direction.

No Telegram output. Uses closed H1/H4 moves across configured USD majors and only
adds a small confirmation/penalty when a genuine peer majority exists.
"""
from __future__ import annotations
from dataclasses import dataclass
import config as cfg
from analysis import atr, closed_candles, split_pair

@dataclass(frozen=True)
class IMDContext:
    alignment:int
    timeframe:str
    peers:int
    agreeing:int
    opposing:int

def _usd_move(symbol,bars,look):
    if len(bars)<=look:return 0
    old,new=bars[-look-1].close,bars[-1].close
    if old<=0:return 0
    raw=(new-old)/old*100
    base,quote=split_pair(symbol)
    # positive means USD strengthened
    return raw if base=="USD" else (-raw if quote=="USD" else 0)

def analyze_symbol(symbol:str, market:dict, candidate_side:int)->IMDContext|None:
    if not getattr(cfg,"IMD_ENABLED",True) or candidate_side not in (-1,1):return None
    base,quote=split_pair(symbol)
    if "USD" not in (base,quote):return None
    expected_usd=candidate_side if base=="USD" else -candidate_side
    for tf,mins,look in (("H1",60,int(getattr(cfg,"IMD_H1_LOOKBACK",3))),("H4",240,int(getattr(cfg,"IMD_H4_LOOKBACK",2)))):
        pos=neg=0
        for peer in getattr(cfg,"PAIRS",()):
            if peer==symbol or "USD" not in peer:continue
            bars=closed_candles((market.get(peer) or {}).get(tf) or [],mins)
            if len(bars)<=look:continue
            move=_usd_move(peer,bars,look)
            av=atr(bars,14)
            atr_move=abs(bars[-1].close-bars[-look-1].close)/av if av>0 else 0
            if abs(move)<float(getattr(cfg,"IMD_MIN_MOVE_PCT",.025)) and atr_move<float(getattr(cfg,"IMD_MIN_MOVE_ATR",.20)):continue
            if move>0:pos+=1
            elif move<0:neg+=1
        peers=pos+neg; need=int(getattr(cfg,"IMD_MIN_PEERS",2)); margin=int(getattr(cfg,"IMD_MAJORITY_MARGIN",1))
        if peers<need:continue
        usd_bias=1 if pos-neg>=margin else (-1 if neg-pos>=margin else 0)
        if not usd_bias:return IMDContext(0,tf,peers,0,0)
        align=1 if usd_bias==expected_usd else -1
        agreeing=pos if usd_bias>0 else neg; opposing=neg if usd_bias>0 else pos
        return IMDContext(align,tf,peers,agreeing,opposing)
    return None

def describe(ctx:IMDContext|None)->str:
    if ctx is None:return "IMD: межрыночное большинство не определено"
    state="подтверждает направление" if ctx.alignment>0 else ("межрыночная дивергенция против направления" if ctx.alignment<0 else "нейтрально")
    return f"IMD {ctx.timeframe}: {state} · подтверждают {ctx.agreeing}/{ctx.peers}"
