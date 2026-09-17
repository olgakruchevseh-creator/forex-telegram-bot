"""Quasimodo Engine — confirmed QM reversal scenario.

A QM zone is tracked internally. Formation alone is never a trade alert. The
engine requires structure/liquidity extreme -> MSS/BOS with displacement ->
return to QML/QM zone -> confirmed reaction -> shared OHLC Movement ->
late-entry gate. Structural ingredients are one correlated structure family,
not independent votes.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
import io, json, os
from pathlib import Path

import config as cfg
import ohlc_movement
from analysis import Candle, atr, closed_candles
from chart_snapshot import freeze_by_tf

TF_MINUTES={"H1":60,"M15":15,"M5":5}
_PENDING_CARDS: dict[str, tuple["QMSetup",dict]]={}

@dataclass
class QMSetup:
    setup_id:str; symbol:str; tf:str; side:int; qml:float; zone_low:float; zone_high:float
    extreme:float; mss_level:float; created_dt:str; break_dt:str; displacement_atr:float
    last_seen_dt:str=""; touch_dt:str=""; delivered:bool=False; invalid:bool=False

def _path():
    root=os.getenv("STATE_DIR","").strip()
    return (Path(root) if root else Path(__file__).resolve().parent)/"quasimodo_state.json"
def _load():
    try:
        x=json.loads(_path().read_text()); return x if isinstance(x,dict) else {}
    except (FileNotFoundError,ValueError,OSError): return {}
def _save(x):
    p=_path(); p.parent.mkdir(parents=True,exist_ok=True); t=p.with_suffix('.tmp')
    t.write_text(json.dumps(x,ensure_ascii=False,indent=2)); t.replace(p)
def _price(s,v): return f"{v:.3f}" if "JPY" in s else f"{v:.5f}"

def _pivots(bars,n=2):
    out=[]
    for i in range(n,len(bars)-n):
        c=bars[i]; around=bars[i-n:i]+bars[i+1:i+n+1]
        if c.high>max(x.high for x in around): out.append((i,1,c.high,c.dt))
        if c.low<min(x.low for x in around): out.append((i,-1,c.low,c.dt))
    return sorted(out,key=lambda x:x[0])

def _newest_setup(symbol,tf,bars):
    if len(bars)<35:return None
    av=atr(bars[:-1],14)
    if av<=0:return None
    piv=_pivots(bars,int(getattr(cfg,'QUASIMODO_PIVOT_BARS',2)))
    min_sweep=float(getattr(cfg,'QUASIMODO_MIN_SWEEP_ATR',.10))*av
    min_disp=float(getattr(cfg,'QUASIMODO_MSS_DISPLACEMENT_ATR',.70))
    zone_atr=float(getattr(cfg,'QUASIMODO_ZONE_ATR',.16))*av
    max_age=int(getattr(cfg,'QUASIMODO_MAX_AGE_BARS',30)); candidates=[]
    # Consecutive alternating H-L-H or L-H-L pivots. The last extreme must take liquidity.
    for a,b,c in zip(piv,piv[1:],piv[2:]):
        if not (a[0]<b[0]<c[0]): continue
        if (a[1],b[1],c[1])==(1,-1,1) and c[2]>=a[2]+min_sweep:
            side=-1; qml=a[2]; extreme=c[2]; mss=b[2]
            breakers=[(i,x) for i,x in enumerate(bars[c[0]+1:],start=c[0]+1) if x.close<mss]
        elif (a[1],b[1],c[1])==(-1,1,-1) and c[2]<=a[2]-min_sweep:
            side=1; qml=a[2]; extreme=c[2]; mss=b[2]
            breakers=[(i,x) for i,x in enumerate(bars[c[0]+1:],start=c[0]+1) if x.close>mss]
        else: continue
        if not breakers: continue
        bi,br=breakers[0]; body=abs(br.close-br.open)/av; rng=(br.high-br.low)/av
        if body<min_disp or rng<min_disp*1.10: continue
        if len(bars)-1-bi>max_age: continue
        zl,zh=qml-zone_atr,qml+zone_atr
        sid=f"{symbol}|{tf}|{side}|{round(qml,6)}|{br.dt[:19]}"
        candidates.append((bi,QMSetup(sid,symbol,tf,side,qml,zl,zh,extreme,mss,c[3],br.dt,body)))
    return max(candidates,key=lambda x:x[0])[1] if candidates else None

def _reaction(z,bars,by_tf):
    post=[c for c in bars if c.dt>z.break_dt]
    if not post:return None
    c=post[-1]
    if c.dt==z.last_seen_dt:return None
    z.last_seen_dt=c.dt
    av=atr(bars[:-1],14)
    if av<=0:return None
    # Structural invalidation beyond the liquidity extreme.
    inv_buf=float(getattr(cfg,'QUASIMODO_INVALIDATION_ATR',.10))*av
    if (z.side>0 and c.close<z.extreme-inv_buf) or (z.side<0 and c.close>z.extreme+inv_buf):
        z.invalid=True; return None
    if c.low<=z.zone_high and c.high>=z.zone_low: z.touch_dt=c.dt
    if not z.touch_dt:return None
    recent=post[-5:]
    if not any(x.dt==z.touch_dt for x in recent):return None
    hold=(c.close>z.zone_high and c.close>c.open) if z.side>0 else (c.close<z.zone_low and c.close<c.open)
    if not hold:return None
    guard=ohlc_movement.guard_event(by_tf,z.side,78)
    if not guard.get('allow',True) or guard.get('range_like'):return None
    detail=(guard.get('details') or {}).get(z.tf) or (guard.get('details') or {}).get('M15') or {}
    directional=int(detail.get('directional_bars',0)); net=float(detail.get('net_atr',0)); score=float(guard.get('score',50))
    multi=directional>=int(getattr(cfg,'QUASIMODO_MIN_DIRECTIONAL_BARS',3))
    equiv=net>=float(getattr(cfg,'QUASIMODO_EQUIVALENT_MOVE_ATR',.85)) and score>=68
    if not (multi or equiv):return None
    early=ohlc_movement.early_entry_check(by_tf,z.side)
    if not early.get('allow',True):return None
    z.delivered=True
    return {'dt':c.dt,'close':c.close,'guard':guard,'directional':directional,'net_atr':net,'score':score,'early':early}

def format_message(z,meta):
    side='LONG' if z.side>0 else 'SHORT'; quality=max(76,min(96,int(round(78+(z.displacement_atr-.7)*8+(meta['score']-55)*.15))))
    prob=max(70,min(92,quality-5))
    return '\n'.join([
      '━━━━━━━━━━━━━━━━━━','🔄 QUASIMODO — QM · РЕАКЦИЯ ПОДТВЕРЖДЕНА','━━━━━━━━━━━━━━━━━━','',
      f'💱 Пара: {z.symbol}',f'📊 Таймфрейм: {z.tf}',f'Направление разворота: {side}',
      f'📍 QML / QM Zone: {_price(z.symbol,z.zone_low)}–{_price(z.symbol,z.zone_high)}',
      f'🧹 Liquidity extreme: {_price(z.symbol,z.extreme)}',f'🧱 MSS/BOS level: {_price(z.symbol,z.mss_level)}',
      f'⚡ Displacement: {z.displacement_atr:.2f} ATR',
      '🕯 Подтверждение: возврат в QM Zone + удержание + OHLC Movement',
      f'🕐 Закрытие подтверждения: {meta["dt"]}',f'💵 Цена: {_price(z.symbol,meta["close"])}',
      f'OHLC: {meta["directional"]} направл. свеч. · {meta["net_atr"]:.2f} ATR · score {meta["score"]:.0f}/100',
      f'Качество: {quality}/100',f'Вероятность: {prob}%','',
      'Структурная цепочка ZigZag/экстремум → sweep → MSS/BOS → displacement → QM учитывается как одно коррелированное семейство.',
      'Факт: образование QM-зоны само по себе не отправляется. Событие создано только после подтверждённого возврата и реакции.'
    ])

def process_market(market,strength=None):
    _PENDING_CARDS.clear(); state=_load(); first=not bool(state.get('bootstrapped'))
    stored={k:QMSetup(**v) for k,v in (state.get('setups') or {}).items()}; pending=state.setdefault('pending_events',{}); messages=[]
    for raw in pending.values():
        z=QMSetup(**raw['setup']); text=raw['text']; messages.append(text); _PENDING_CARDS[text]=(z,freeze_by_tf(market.get(z.symbol) or {}))
    for symbol in cfg.PAIRS:
      by_tf=market.get(symbol) or {}
      for tf in getattr(cfg,'QUASIMODO_TIMEFRAMES',('H1','M15','M5')):
        if tf not in TF_MINUTES:continue
        bars=closed_candles(by_tf.get(tf) or [],TF_MINUTES[tf]); look=int(getattr(cfg,'QUASIMODO_LOOKBACK',100))
        if len(bars)<35:continue
        newest=_newest_setup(symbol,tf,bars[-look:])
        if newest and newest.setup_id not in stored: stored[newest.setup_id]=newest
        for z in [x for x in stored.values() if x.symbol==symbol and x.tf==tf and not x.invalid and not x.delivered]:
          meta=_reaction(z,bars,by_tf)
          if meta and not first:
            text=format_message(z,meta); key=f'{z.setup_id}|{meta["dt"][:19]}'
            if key not in pending:pending[key]={'setup':asdict(z),'text':text}
            if text not in messages:messages.append(text)
            _PENDING_CARDS[text]=(z,freeze_by_tf(by_tf))
    state['bootstrapped']=True; active=sorted(stored.values(),key=lambda x:x.break_dt)[-500:]
    state['setups']={x.setup_id:asdict(x) for x in active}; state['pending_events']=pending; _save(state); return messages

def mark_delivered(text):
    state=_load(); pending=state.setdefault('pending_events',{})
    for k,v in list(pending.items()):
      if v.get('text')==text: pending.pop(k,None); break
    state['pending_events']=pending; _save(state); _PENDING_CARDS.pop(text,None)

def image_for_alert(text):
    card=_PENDING_CARDS.get(text)
    if not card or not getattr(cfg,'QUASIMODO_CHART_IMAGES_ENABLED',True):return None
    z,by_tf=card
    from PIL import Image,ImageDraw,ImageFont
    bars=closed_candles(by_tf.get(z.tf) or [],TF_MINUTES[z.tf])[-60:]
    if not bars:return None
    W,H=1200,720; im=Image.new('RGB',(W,H),'#10131d'); d=ImageDraw.Draw(im,'RGBA')
    try: font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',23); small=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',17)
    except OSError: font=small=ImageFont.load_default()
    L,R,T,B=70,1100,75,610; vals=[v for c in bars for v in(c.low,c.high)]+[z.zone_low,z.zone_high,z.mss_level]
    lo,hi=min(vals),max(vals); pad=max((hi-lo)*.08,1e-9);lo-=pad;hi+=pad
    x=lambda i:L+i/max(1,len(bars)-1)*(R-L); y=lambda p:B-(p-lo)/max(1e-12,hi-lo)*(B-T)
    for r in range(6): d.line((L,T+r*(B-T)/5,R,T+r*(B-T)/5),fill='#293143',width=1)
    cw=max(4,int((R-L)/len(bars)*.55))
    for i,c in enumerate(bars):
      xx=x(i); col='#37d67a' if c.close>=c.open else '#ff5c6c'; d.line((xx,y(c.high),xx,y(c.low)),fill=col,width=2); d.rectangle((xx-cw/2,min(y(c.open),y(c.close)),xx+cw/2,max(y(c.open),y(c.close))+1),fill=col)
    d.rectangle((L,y(z.zone_high),R,y(z.zone_low)),fill='#f1c75b35',outline='#f1c75b',width=3); d.text((L+8,y(z.zone_high)-25),'QM Zone / QML',fill='#f1c75b',font=small)
    d.line((L,y(z.mss_level),R,y(z.mss_level)),fill='#70a7ff',width=2); d.text((R-150,y(z.mss_level)-24),'MSS/BOS',fill='#70a7ff',font=small)
    d.text((L,24),f'{z.symbol} · {z.tf} · Quasimodo · {"LONG" if z.side>0 else "SHORT"}',fill='#f1f5fb',font=font)
    d.text((L,655),'QM zone → return/retest → confirmed OHLC reaction',fill='#c9d1df',font=small)
    out=io.BytesIO();out.name=f'quasimodo_{z.symbol.replace("/","")}_{z.tf}.png';im.save(out,format='PNG',optimize=True);out.seek(0);return out
