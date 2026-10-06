"""Layer 15 — Event Sequence / Setup State Brain (OBSERVE_ONLY).

Links already-existing facts into ordered setup lifecycles instead of adding a
new strategy/vote.  It recognizes generic SMC chains and a conservative Monday
range continuation chain.  It never creates/vetoes Telegram signals, changes
probability, or bypasses the closed-H1 policy.
"""
from __future__ import annotations
from datetime import datetime, timezone
import math
import config as cfg
from analysis import atr, closed_candles, find_fvgs


def _dt(v):
    try:
        d=datetime.fromisoformat(str(v).replace('Z','+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:return None

def _src(texts):
    s='\n'.join(texts or []).upper(); out=[]
    rules=(("SWEEP",("СНЯТИЕ ЛИКВИДНОСТИ","СНЯТИЕ PDH","СНЯТИЕ PDL","SWEEP")),
           ("FVG",("IMBALANCE","ДИСБАЛАНС","FVG")),
           ("STRUCTURE_SHIFT",("CHOCH","MSS","СМЕНА СТРУКТУР","ZIGZAG")),
           ("RETEST",("РЕТЕСТ","RETEST","MITIGATION")),
           ("ORDERFLOW",("ORDER BLOCK","BREAKER","CISD")))
    for name,marks in rules:
        if any(x in s for x in marks):out.append(name)
    return out

def _monday_range(h1):
    if not h1:return None
    last=_dt(h1[-1].dt)
    if not last:return None
    iso=last.isocalendar(); week=(iso.year,iso.week)
    cur=[c for c in h1 if (_dt(c.dt) and (_dt(c.dt).isocalendar().year,_dt(c.dt).isocalendar().week)==week)]
    mon=[c for c in cur if _dt(c.dt).isocalendar().weekday==1]
    if len(mon)<4:return None
    return min(c.low for c in mon),max(c.high for c in mon),cur

def _monday_chain(by_tf, side):
    h1=closed_candles((by_tf or {}).get('H1') or [],60)
    mr=_monday_range(h1)
    if not mr:return {'state':'NO_MONDAY_RANGE','steps':[]}
    lo,hi,cur=mr; av=atr(h1[-min(40,len(h1)):],14) if len(h1)>=15 else max(hi-lo,1e-9)
    buf=max(av,1e-9)*float(getattr(cfg,'LAYER15_MONDAY_SWEEP_ATR',0.05))
    after=[c for c in cur if _dt(c.dt).isocalendar().weekday>=2]
    if not after:return {'state':'MONDAY_RANGE_READY','steps':['MONDAY_RANGE'] ,'monday_low':lo,'monday_high':hi}
    want=1 if side=='LONG' else -1
    sweep_i=None
    for i,c in enumerate(after):
        if want>0 and c.low<lo-buf and c.close>lo: sweep_i=i; break
        if want<0 and c.high>hi+buf and c.close<hi: sweep_i=i; break
    steps=['MONDAY_RANGE']
    if sweep_i is None:return {'state':'WAITING_SWEEP','steps':steps,'monday_low':lo,'monday_high':hi}
    steps.append('MONDAY_EXTREME_SWEEP_RECLAIM')
    post=after[sweep_i:]
    # FVG must be formed after the sweep and agree with the candidate side.
    gaps=find_fvgs(post,last_n=min(40,len(post))) if len(post)>=3 else []
    good=[g for g in gaps if (g.kind=='bull')==(want>0)]
    if not good:return {'state':'SWEEP_RECLAIM','steps':steps,'monday_low':lo,'monday_high':hi}
    steps.append('POST_SWEEP_FVG')
    # Closed-H1 displacement: body >= configured ATR fraction in intended direction.
    disp=float(getattr(cfg,'LAYER15_DISPLACEMENT_ATR',0.45))*max(av,1e-9)
    displaced=any((c.close-c.open)>=disp if want>0 else (c.open-c.close)>=disp for c in post)
    if displaced:steps.append('H1_DISPLACEMENT')
    return {'state':'SEQUENCE_CONFIRMED' if displaced else 'FVG_FORMED','steps':steps,
            'monday_low':round(lo,6),'monday_high':round(hi,6),'fvg_count':len(good)}

def assess(pair:str, side:str, texts:list[str], by_tf:dict, ctx:dict|None=None)->dict:
    if not getattr(cfg,'LAYER15_EVENT_SEQUENCE_ENABLED',True):
        return {'layer':15,'observe_only':True,'state':'DISABLED','trade_effect':False}
    facts=_src(texts); generic=[]
    for step in ('SWEEP','STRUCTURE_SHIFT','FVG','RETEST','ORDERFLOW'):
        if step in facts:generic.append(step)
    mon=_monday_chain(by_tf,side)
    return {'layer':15,'observe_only':True,'state':mon.get('state','NO_SEQUENCE'),
            'pair':pair,'side':side,'generic_facts':facts,'generic_sequence':generic,
            'monday_sequence':mon,'closed_h1_required':True,'m15_m5_confirmation_only':True,
            'trade_effect':False,'direction_claim':False,'threshold_effect':False,
            'basis':'ORDERED_EVENTS_NOT_INDEPENDENT_VOTES'}
