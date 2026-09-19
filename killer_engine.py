"""KILLER meta-selector: rare, high-convergence trade candidates.

KILLER is not another detector. It consumes already confirmed module events and
selects only exceptional same-side convergence. Correlated modules are grouped
into families so FVG/BPR/Imbalance or ZigZag/MSS/BOS cannot inflate the score.
"""
from __future__ import annotations
import hashlib, io, re, time
from collections import defaultdict

import config as cfg
import market_regime
import ohlc_movement
import liquidity_context
import po3_fvg_context
import structure_context
import precision_entry
import market_maker_model
from analysis import analyze_tf, atr, closed_candles

_PENDING: dict[str, dict] = {}
# Fresh confirmed facts survive across scan cycles so KILLER can recognize a real
# sequence (sweep -> structure -> zone reaction) instead of requiring same-tick alerts.
_FACT_MEMORY: dict[tuple[str,str,str], dict] = {}
_TF_MIN={"D1":1440,"H4":240,"H1":60,"M15":15,"M5":5}

_TF_FRESH_SECONDS={"D1":36*3600,"H4":12*3600,"H1":4*3600,"M15":90*60,"M5":30*60}

def _tf_of(t):
 m=re.search(r"(?:TF|Таймфрейм):\s*(D1|H4|H1|M15|M5)\b",t or "",re.I)
 if not m: m=re.search(r"\b(D1|H4|H1|M15|M5)\b",t or "",re.I)
 return m.group(1).upper() if m else "H1"

def _fresh_seconds(tf):
 custom=getattr(cfg,"KILLER_FAMILY_FRESHNESS_SECONDS",None)
 if isinstance(custom,dict) and tf in custom:
  try:return max(60,int(custom[tf]))
  except Exception:pass
 return _TF_FRESH_SECONDS.get(tf,4*3600)

def _expire_memory(now=None):
 now=float(now if now is not None else time.time())
 for key,fact in list(_FACT_MEMORY.items()):
  if now-float(fact.get("seen_at",0)) > _fresh_seconds(fact.get("tf","H1")):
   _FACT_MEMORY.pop(key,None)

def _remember_alerts(alerts, now=None):
 """Store one freshest fact per pair/side/family; structure flip invalidates opposite setup."""
 now=float(now if now is not None else time.time()); _expire_memory(now)
 for t in alerts:
  pair,side=_pair(t),_side(t)
  if not pair or not side: continue
  fams=_families(t)
  if not fams: continue
  tf=_tf_of(t); q=_quality(t); fp=hashlib.sha1(t.encode()).hexdigest()[:12]
  if "structure" in fams:
   opposite="SHORT" if side=="LONG" else "LONG"
   # A fresh opposite structural confirmation invalidates the accumulated thesis.
   for key in [k for k in _FACT_MEMORY if k[0]==pair and k[1]==opposite]: _FACT_MEMORY.pop(key,None)
  for fam in fams:
   _FACT_MEMORY[(pair,side,fam)]={"text":t,"seen_at":now,"tf":tf,"quality":q,"fingerprint":fp}

def _memory_texts(pair,side,now=None):
 _expire_memory(now)
 facts=[v for (p,s,_f),v in _FACT_MEMORY.items() if p==pair and s==side]
 # De-duplicate a single source text that legitimately maps to more than one family.
 seen=set(); out=[]
 for f in sorted(facts,key=lambda x:x.get("seen_at",0)):
  fp=f.get("fingerprint")
  if fp in seen: continue
  seen.add(fp); out.append(f["text"])
 return out

def reset_memory():
 """Test/maintenance hook; does not affect normal event delivery state."""
 _FACT_MEMORY.clear()

_FAMILIES={
 "structure":("ZIGZAG","MSS","BOS","QUASIMODO","DOUBLE TOP","DOUBLE BOTTOM","ДВОЙН","ГОЛОВА И ПЛЕЧИ","1-2-3"),
 "liquidity":("СНЯТИЕ ЛИКВИДНОСТИ","LIQUIDITY SWEEP","PDH","PDL","EQH","EQL","INDUCEMENT","IDM"),
 "imbalance":("IMBALANCE","FVG","SIBI","BISI","ДИСБАЛАНС","BPR","BALANCED PRICE RANGE"),
 "blocks":("ORDER BLOCK","MITIGATION BLOCK","BREAKER BLOCK","PROPULSION"),
 "levels":("ПРОБОЙ УРОВНЯ","ОТБОЙ ОТ","РЕТТЕСТ УРОВНЯ","CONSOLIDATION","КОНСОЛИДАЦ","POC"),
 "smc_fib":("FIB + SMC","ФИБОНАЧЧИ","SMART MONEY 62-26","PREMIUM","DISCOUNT"),
 "session_setup":("SILVER BULLET","POWER OF THREE","AMD","CRT —","CANDLE RANGE THEORY"),
 "reversal":("ATS REVERSAL","EXHAUSTION"),
 "entry_location_execution":("IOFED","OTE","CONSEQUENT ENCROACHMENT","PRECISION ENTRY"),
}
_LABELS={"structure":"Structure/MSS","liquidity":"Liquidity","imbalance":"FVG/BPR/Imbalance","po3_fvg_scenario":"PO3×FVG scenario",
 "blocks":"OB/MB/Breaker","levels":"Levels/PDH-PDL","smc_fib":"Fib/SMC","session_setup":"Session/CRT/AMD","reversal":"Reversal","entry_location_execution":"Entry Location/Execution"}
_WEIGHTS={"structure":13,"liquidity":12,"imbalance":10,"po3_fvg_scenario":10,"blocks":9,"levels":9,"smc_fib":8,"session_setup":8,"reversal":7,"entry_location_execution":7}

def _pair(t):
 m=re.search(r"(?:Пара:\s*|💱 Пара:\s*)([A-Z]{3}/[A-Z]{3})",t or "") or re.search(r"(?:LONG|SHORT)\s+([A-Z]{3}/[A-Z]{3})",t or "",re.I)
 return m.group(1) if m else ""
def _side(t):
 m=re.search(r"(?:^|\n)[🟢🔴]?\s*(LONG|SHORT)\s+[A-Z]{3}/[A-Z]{3}",t or "",re.I) or re.search(r"Направление(?:\s+(?:реакции|пробоя|разворота))?:\s*(LONG|SHORT)\b",t or "",re.I)
 return m.group(1).upper() if m else ""
def _quality(t):
 vals=[int(x) for x in re.findall(r"(?:Качество|Уверенность модели|Вероятность):\s*(\d{1,3})",t or "",re.I)]
 return max(vals) if vals else 70
def _families(t):
 u=(t or "").upper(); return {f for f,marks in _FAMILIES.items() if any(x in u for x in marks)}
def _views(by_tf, direction):
 out={}
 for tf,m in _TF_MIN.items():
  b=closed_candles((by_tf or {}).get(tf) or [],m)
  out[tf]=analyze_tf(tf,tf,b).bias if len(b)>=20 else 0
 return out

def evaluate(pair, side, texts, market, strength):
 direction=1 if side=="LONG" else -1; by_tf=market.get(pair) or {}
 families=set(); fam_best={}
 for t in texts:
  for f in _families(t):
   families.add(f); fam_best[f]=max(fam_best.get(f,0),_quality(t))
 po3_ctx=po3_fvg_context.analyze(pair,side,by_tf,texts)
 raw_families=set(families)
 families=po3_fvg_context.collapse_families(families,po3_ctx,"session_setup","imbalance")
 if "po3_fvg_scenario" in families:
  fam_best["po3_fvg_scenario"]=max(fam_best.get("session_setup",70),fam_best.get("imbalance",70))
 precision_ctx=precision_entry.analyze(pair,side,by_tf,texts)
 # OTE + CE + IOFED are exactly ONE correlated execution family. They can
 # contribute one family only when the whole execution chain is READY.
 if precision_ctx.ready:
  families.add("entry_location_execution")
  fam_best["entry_location_execution"]=max(fam_best.get("entry_location_execution",0),82)
 if len(families)<int(getattr(cfg,"KILLER_MIN_FAMILIES",5)):
  return {"eligible":False,"reason":"not_enough_independent_families","families":families,"po3_fvg_context":po3_ctx,"precision_entry":precision_ctx}
 h1=closed_candles(by_tf.get("H1") or [],60)
 if len(h1)<25:return {"eligible":False,"reason":"insufficient_h1","families":families}
 views=_views(by_tf,direction); senior=sum(views[x]==direction for x in ("D1","H4","H1")); junior=sum(views[x]==direction for x in ("H1","M15","M5"))
 try:
  base,quote=pair.split("/"); gap=(float(strength.get(base,0))-float(strength.get(quote,0)))*direction
 except Exception: gap=0.0
 guard=ohlc_movement.guard_event(by_tf,direction,82)
 early=ohlc_movement.early_entry_check(by_tf,direction)
 if not early.get("allow",True):return {"eligible":False,"reason":early.get("reason","late_entry"),"families":families}
 if guard.get("weak_reversal") or (not guard.get("allow",True)):
  return {"eligible":False,"reason":"ohlc_contradiction","families":families}
 regime=market_regime.analyze_symbol(pair,by_tf); rname=regime.name if regime else "UNKNOWN"
 structure_ctx=structure_context.analyze_symbol(pair,by_tf,direction)
 liquidity_ctx=liquidity_context.analyze_symbol(pair,by_tf,direction,texts)
 mmm_ctx=market_maker_model.analyze(pair,side,by_tf,texts,liquidity_ctx,precision_ctx)
 # A KILLER entry cannot be exceptional if its external liquidity target is already consumed.
 if liquidity_ctx and liquidity_ctx.residual_state=="EXHAUSTED":
  return {"eligible":False,"reason":"erl_residual_exhausted","families":families,"liquidity_context":liquidity_ctx,"po3_fvg_context":po3_ctx,"structure_context":structure_ctx,"precision_entry":precision_ctx,"market_maker_model":mmm_ctx}
 # Hard contradiction only for genuinely poor context; soft disagreements reduce score.
 if senior==0 or junior==0:return {"eligible":False,"reason":"critical_tf_contradiction","families":families}
 family_score=sum(_WEIGHTS[f] for f in families)
 quality=sum(fam_best.values())/max(1,len(fam_best))
 ohlc_score=float(guard.get("score",50) or 50)
 score=family_score + min(14,senior*3+junior*1.5) + max(-4,min(6,(quality-70)*.18)) + max(-4,min(5,(ohlc_score-55)*.16)) + max(-3,min(4,gap*18))
 if rname in ("TREND","EXPANSION"):score+=3
 elif rname in ("RANGE","COMPRESSION"):score-=3
 score += liquidity_context.score_delta(liquidity_ctx)
 score += structure_context.score_delta(structure_ctx,direction)
 score += precision_entry.score_delta(precision_ctx)
 score += market_maker_model.score_delta(mmm_ctx)
 score=max(0,min(100,int(round(score))))
 threshold=int(getattr(cfg,"KILLER_SCORE_THRESHOLD",88))
 if score<threshold:return {"eligible":False,"reason":"score_below_threshold","score":score,"families":families}
 av=atr(h1,14); entry=float(h1[-1].close); mult=(1 if direction>0 else -1)
 targets=[entry+mult*av*x for x in (1.0,1.75,2.5)]
 return {"eligible":True,"score":score,"families":families,"quality":round(quality),"ohlc":round(ohlc_score),"senior":senior,"junior":junior,"gap":gap,"regime":rname,"entry":entry,"atr":av,"targets":targets,"early":early,"liquidity_context":liquidity_ctx,"po3_fvg_context":po3_ctx,"structure_context":structure_ctx,"precision_entry":precision_ctx,"market_maker_model":mmm_ctx}

def process_candidates(alerts, market, strength):
 _PENDING.clear(); grouped=defaultdict(list)
 _remember_alerts(alerts)
 # Only a pair/side touched in the current scan may emit KILLER; memory supplies
 # preceding fresh confirmations but can never emit an event by itself.
 for t in alerts:
  p,s=_pair(t),_side(t)
  if p and s:grouped[(p,s)].append(t)
 out=[]
 for (pair,side),current_texts in grouped.items():
  texts=_memory_texts(pair,side)
  meta=evaluate(pair,side,texts,market,strength)
  if not meta.get("eligible"):continue
  fams=sorted(meta["families"]); sig="|".join(sorted(hashlib.sha1(t.encode()).hexdigest()[:10] for t in texts))
  event_id=hashlib.sha1(f"{pair}|{side}|{','.join(fams)}|{sig}".encode()).hexdigest()[:20]
  labels=" · ".join(_LABELS[f] for f in fams)
  def px(v):return f"{v:.3f}" if "JPY" in pair else f"{v:.5f}"
  tr=meta["targets"]
  text="\n".join(["━━━━━━━━━━━━━━━━━━","🏹🎯 KILLER — ВЫСОКАЯ КОНВЕРГЕНЦИЯ","━━━━━━━━━━━━━━━━━━","",f"💱 Пара: {pair}",f"Направление: {side}",f"Killer Score: {meta['score']}/100",f"Независимые семейства: {len(fams)} · {labels}",f"TF: D1/H4/H1 {meta['senior']}/3 · H1/M15/M5 {meta['junior']}/3",f"OHLC Movement: {meta['ohlc']}/100 · Regime: {meta['regime']}",f"Liquidity Context: {liquidity_context.describe(meta.get('liquidity_context'))}",f"{structure_context.describe(meta.get('structure_context'))}",f"{po3_fvg_context.describe(meta.get('po3_fvg_context'))}",f"{precision_entry.describe(meta.get('precision_entry'))}",f"{market_maker_model.describe(meta.get('market_maker_model'))}",f"Currency Strength по направлению: {meta['gap']:+.2f}",f"Цена подтверждения: {px(meta['entry'])}",f"TR1: {px(tr[0])}",f"TR2: {px(tr[1])}",f"TR3: {px(tr[2])}","","Факт: KILLER учитывает коррелированные подтверждения как одно семейство; одиночные совпадения score не раздувают.","Late-entry / OHLC / критическое TF-противоречие проверены до выпуска события."])
  out.append(text);_PENDING[text]={"pair":pair,"side":side,"meta":meta,"event_id":event_id,"by_tf":by_tf if (by_tf:=market.get(pair)) else {}}
 return out

def event_id_for_alert(text):
 c=_PENDING.get(text);return f"KILLER:{c['event_id']}" if c else ""
def mark_delivered(text):_PENDING.pop(text,None)
def image_for_alert(text):
 c=_PENDING.get(text)
 if not c or not getattr(cfg,"KILLER_CHART_IMAGES_ENABLED",True):return None
 from PIL import Image,ImageDraw,ImageFont
 pair=c["pair"];meta=c["meta"]; bars=closed_candles(c["by_tf"].get("H1") or [],60)[-55:]
 if not bars:return None
 W,H=1200,720;im=Image.new("RGB",(W,H),"#10131d");d=ImageDraw.Draw(im,"RGBA")
 try:font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',22);small=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',16)
 except OSError:font=small=ImageFont.load_default()
 L,R,T,B=70,1080,70,610; vals=[v for x in bars for v in (x.low,x.high)]+meta["targets"]+[meta["entry"]];lo,hi=min(vals),max(vals);pad=max((hi-lo)*.07,1e-9);lo-=pad;hi+=pad
 X=lambda i:L+i/max(1,len(bars)-1)*(R-L);Y=lambda p:B-(p-lo)/max(1e-12,hi-lo)*(B-T);cw=max(4,int((R-L)/len(bars)*.55))
 for i,b in enumerate(bars):
  x=X(i);col='#37d67a' if b.close>=b.open else '#ff5c6c';d.line((x,Y(b.high),x,Y(b.low)),fill=col,width=2);d.rectangle((x-cw/2,min(Y(b.open),Y(b.close)),x+cw/2,max(Y(b.open),Y(b.close))+1),fill=col)
 for label,p in [("ENTRY",meta["entry"]),("TR1",meta["targets"][0]),("TR2",meta["targets"][1]),("TR3",meta["targets"][2])]:d.line((L,Y(p),R,Y(p)),fill='#d8dde8',width=2);d.text((R+8,Y(p)-10),label,fill='#f1f5fb',font=small)
 d.text((L,22),f"{pair} · H1 · KILLER {c['side']} · {meta['score']}/100",fill='#f1f5fb',font=font)
 out=io.BytesIO();out.name=f"killer_{pair.replace('/','')}.png";im.save(out,format='PNG',optimize=True);out.seek(0);return out
