"""ATS Reversal Point — событийный поиск подтверждённых точек разворота.

Это собственная реализация логики reversal-point, а не копия закрытого индикатора ATS.
Кандидаты ведутся внутри; Telegram получает только подтверждённый разворот.
"""
from __future__ import annotations
import io, json, logging, os
from dataclasses import asdict, dataclass
from pathlib import Path
import config as cfg
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair, zigzag
from chart_snapshot import freeze_by_tf

log=logging.getLogger('fxbot.ats_reversal')
TF_MINUTES={'D1':1440,'H4':240,'H1':60,'M15':15,'M5':5}
_LAST_CHART_CARDS={}

@dataclass
class Setup:
    id:str; symbol:str; side:str; extreme:float; trigger:float; created_dt:str
    age:int=0; last_dt:str=''; sent:bool=False; invalid:bool=False

def _path():
    root=os.getenv('STATE_DIR','').strip()
    return (Path(root) if root else Path(__file__).resolve().parent)/'ats_reversal_state.json'
def _load():
    try: return json.loads(_path().read_text())
    except (OSError,ValueError): return {}
def _save(x):
    p=_path(); p.parent.mkdir(parents=True,exist_ok=True); t=p.with_suffix('.tmp')
    t.write_text(json.dumps(x,ensure_ascii=False,indent=2)); t.replace(p)
def _bars(by_tf,tf): return closed_candles(by_tf.get(tf) or [],TF_MINUTES[tf])
def _bias(tf,bars):
    v=analyze_tf(tf,tf,bars) if len(bars)>=25 else None
    return v.bias if v else 0

def _candidate(symbol,h4,h1):
    if len(h1)<35 or len(h4)<25: return None
    c=h1[-1]; av=atr(h1,14)
    if av<=0:return None
    piv=zigzag(h1[:-1],float(cfg.ZIGZAG_PCT.get('H1',.18)),int(cfg.ZIGZAG_MIN_BARS))[-8:]
    highs=[p.price for p in piv if p.kind=='high']; lows=[p.price for p in piv if p.kind=='low']
    if not highs or not lows:return None
    wick_up=c.high-max(c.open,c.close); wick_dn=min(c.open,c.close)-c.low
    body=abs(c.close-c.open)
    buf=av*getattr(cfg,'ATS_REVERSAL_SWEEP_ATR',.08)
    wick_need=max(body*getattr(cfg,'ATS_REVERSAL_WICK_BODY_RATIO',1.25),av*.18)
    look=int(getattr(cfg,'ATS_REVERSAL_TRIGGER_LOOKBACK',5)); prior=h1[-look-1:-1]
    side=None; extreme=trigger=0.0
    if c.high>max(highs)+buf and c.close<max(highs) and wick_up>=wick_need:
        side='SHORT'; extreme=c.high; trigger=min(x.low for x in prior)
    elif c.low<min(lows)-buf and c.close>min(lows) and wick_dn>=wick_need:
        side='LONG'; extreme=c.low; trigger=max(x.high for x in prior)
    if not side:return None
    # Разворот должен иметь смысл относительно H4: не принимаем сигнал,
    # если H4 уже движется в новую сторону — это скорее продолжение, не reversal point.
    wanted=1 if side=='LONG' else -1
    if _bias('H4',h4)==wanted:return None
    precision=3 if 'JPY' in symbol else 5
    sid=f'{symbol}|{side}|{c.dt}|{extreme:.{precision}f}'
    return Setup(sid,symbol,side,extreme,trigger,c.dt,last_dt=c.dt)

def _confirm(s,h4,h1,m15,m5,strength):
    if s.sent or s.invalid or not m15:return None
    c=m15[-1]
    if c.dt<=s.created_dt or c.dt==s.last_dt:return None
    s.last_dt=c.dt; s.age+=1
    av=atr(h1,14)
    if av<=0:return None
    if s.age>getattr(cfg,'ATS_REVERSAL_MAX_M15_BARS',8): s.invalid=True; return None
    inv=av*getattr(cfg,'ATS_REVERSAL_INVALIDATION_ATR',.12)
    if (s.side=='LONG' and c.close<s.extreme-inv) or (s.side=='SHORT' and c.close>s.extreme+inv): s.invalid=True; return None
    wanted=1 if s.side=='LONG' else -1
    micro=max(x.high for x in m15[-5:-1]) if wanted>0 else min(x.low for x in m15[-5:-1])
    broken=(c.close>micro and c.close>c.open) if wanted>0 else (c.close<micro and c.close<c.open)
    if not broken or abs(c.close-c.open)<av*getattr(cfg,'ATS_REVERSAL_CONFIRM_BODY_ATR',.12):return None
    if _bias('M15',m15)!=wanted:return None
    if m5 and _bias('M5',m5)==-wanted:return None
    base,quote=split_pair(s.symbol); gap=strength.get(base,0)-strength.get(quote,0)
    strength_ok=gap>=0 if wanted>0 else gap<=0
    score=76 + (6 if strength_ok else 0) + (5 if _bias('H1',h1)==wanted else 0) + (4 if _bias('H4',h4)==0 else 0)
    score=min(94,score); conf=max(70,min(91,score-4))
    s.sent=True
    return {'symbol':s.symbol,'side':s.side,'extreme':s.extreme,'trigger':micro,'close':c.close,'dt':c.dt,'quality':score,'confidence':conf,'gap':gap,'strength_ok':strength_ok,'tf':'M15'}

def _price(sym,x): return f'{x:.3f}' if 'JPY' in sym else f'{x:.5f}'
def _format(e):
    return '\n'.join([
      '━━━━━━━━━━━━━━━━━━','🎯 ATS REVERSAL POINT — РАЗВОРОТ ПОДТВЕРЖДЁН','━━━━━━━━━━━━━━━━━━','',
      f"Пара: {e['symbol']}",f"Направление разворота: {e['side']}",
      f"Экстремум разворота: {_price(e['symbol'],e['extreme'])}",
      f"Подтверждение: закрытая {e['tf']}-свеча + слом микроструктуры",
      f"Цена подтверждения: {_price(e['symbol'],e['close'])}",
      f"Разница силы валют: {e['gap']:+.2f}",f"Качество: {e['quality']}/100",f"Вероятность: {e['confidence']}%",'',
      '✅ Факт: цена сняла предыдущий H1-экстремум, вернулась за него и затем закрытой M15-свечой подтвердила смену локальной структуры.',
      'ℹ️ ATS не создаёт сигнал принудительно: без подтверждённого разворота карточка не отправляется.'
    ])

def process_market(market,strength):
    st=_load(); first=not st.get('bootstrapped'); setups={k:Setup(**v) for k,v in (st.get('setups') or {}).items()}; out=[]
    for symbol in cfg.PAIRS:
      try:
        by_tf=market.get(symbol) or {}; h4,h1,m15,m5=(_bars(by_tf,t) for t in ('H4','H1','M15','M5'))
        for s in list(setups.values()):
          if s.symbol!=symbol or s.sent or s.invalid:continue
          e=_confirm(s,h4,h1,m15,m5,strength)
          if e and not first:
            msg=_format(e); out.append(msg); _LAST_CHART_CARDS[msg]=(e,freeze_by_tf(by_tf))
        fresh=_candidate(symbol,h4,h1)
        if fresh and fresh.id not in setups:
          for old in setups.values():
            if old.symbol==symbol and old.side==fresh.side and not old.sent: old.invalid=True
          setups[fresh.id]=fresh
      except Exception: log.exception('ATS Reversal Point %s',symbol)
    st['bootstrapped']=True
    kept=[s for s in setups.values() if not s.invalid][-500:]; st['setups']={s.id:asdict(s) for s in kept}; _save(st); return out

def render_chart(e,by_tf):
    import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    bars=list(by_tf.get('M15') or [])[-72:]
    if not bars:return None
    fig,ax=plt.subplots(figsize=(10,5.4))
    for i,c in enumerate(bars):
      ax.vlines(i,c.low,c.high,linewidth=1); b=min(c.open,c.close); h=max(abs(c.close-c.open),(c.high-c.low)*.015)
      ax.add_patch(Rectangle((i-.32,b),.64,h,fill=False,linewidth=1.1))
    ax.axhline(e['extreme'],linestyle='--',linewidth=1.3,label='ATS reversal extreme')
    ax.annotate(e['side'],(len(bars)-1,e['close']),xytext=(-55,25),textcoords='offset points',arrowprops={'arrowstyle':'->'})
    ax.set_title(f"{e['symbol']} · ATS REVERSAL POINT · {e['side']}"); ax.grid(True,alpha=.2); ax.legend(); fig.tight_layout()
    buf=io.BytesIO(); fig.savefig(buf,format='png',dpi=150,bbox_inches='tight'); plt.close(fig); buf.seek(0); return buf

def image_for_alert(text):
    card=_LAST_CHART_CARDS.pop(text,None)
    return render_chart(*card) if card else None
