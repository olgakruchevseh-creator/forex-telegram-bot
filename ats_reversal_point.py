"""ATS Reversal Point — событийный поиск подтверждённых точек разворота.

Это собственная реализация логики reversal-point, а не копия закрытого индикатора ATS.
Кандидаты ведутся внутри; Telegram получает только подтверждённый разворот.
"""
from __future__ import annotations
import io, json, logging, os, hashlib, statistics
from dataclasses import asdict, dataclass
from pathlib import Path
import config as cfg
import ohlc_movement
import pullback_regime
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair, zigzag
from chart_snapshot import freeze_by_tf

log=logging.getLogger('fxbot.ats_reversal')
TF_MINUTES={'D1':1440,'H4':240,'H1':60,'M15':15,'M5':5}
_LAST_CHART_CARDS={}

@dataclass
class Setup:
    id:str; symbol:str; side:str; extreme:float; trigger:float; created_dt:str
    age:int=0; last_dt:str=''; sent:bool=False; invalid:bool=False
    wick_anomaly:float=0.0; rejection_class:str='ОБЫЧНЫЙ REJECTION'

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
    # Kangaroo-style context: compare the rejection wick with the normal H1 wick
    # distribution.  It is a quality feature only, never a standalone signal.
    sample=h1[-int(getattr(cfg,'ATS_REVERSAL_WICK_CONTEXT_BARS',20))-1:-1]
    prior_wicks=[]
    for b in sample:
        prior_wicks.extend((b.high-max(b.open,b.close), min(b.open,b.close)-b.low))
    typical_wick=statistics.median([w for w in prior_wicks if w>=0]) if prior_wicks else 0.0
    look=int(getattr(cfg,'ATS_REVERSAL_TRIGGER_LOOKBACK',5)); prior=h1[-look-1:-1]
    side=None; extreme=trigger=0.0; selected_wick=0.0
    if c.high>max(highs)+buf and c.close<max(highs) and wick_up>=wick_need:
        side='SHORT'; extreme=c.high; trigger=min(x.low for x in prior); selected_wick=wick_up
    elif c.low<min(lows)-buf and c.close>min(lows) and wick_dn>=wick_need:
        side='LONG'; extreme=c.low; trigger=max(x.high for x in prior); selected_wick=wick_dn
    if not side:return None
    # Разворот должен иметь смысл относительно H4: не принимаем сигнал,
    # если H4 уже движется в новую сторону — это скорее продолжение, не reversal point.
    wanted=1 if side=='LONG' else -1
    if _bias('H4',h4)==wanted:return None
    precision=3 if 'JPY' in symbol else 5
    sid=f'{symbol}|{side}|{c.dt}|{extreme:.{precision}f}'
    anomaly=(selected_wick/typical_wick) if typical_wick>0 else 0.0
    strong_at=float(getattr(cfg,'ATS_REVERSAL_WICK_ANOMALY_RATIO',1.8))
    rejection='ЭКСТРЕМАЛЬНЫЙ KANGAROO REJECTION' if anomaly>=strong_at else ('СИЛЬНЫЙ REJECTION' if anomaly>=1.25 else 'ОБЫЧНЫЙ REJECTION')
    return Setup(sid,symbol,side,extreme,trigger,c.dt,last_dt=c.dt,wick_anomaly=round(anomaly,2),rejection_class=rejection)

def _confirm(s,h4,h1,m15,m5,strength,by_tf=None):
    """Confirm an ATS setup on a *later closed H1 candle*.

    M15/M5 are auxiliary evidence only.  They may strengthen or veto a weak
    microstructure picture, but can never independently release an ATS card.
    """
    if s.sent or s.invalid or len(h1) < 20:return None
    c=h1[-1]
    if c.dt<=s.created_dt or c.dt==s.last_dt:return None
    s.last_dt=c.dt; s.age+=1
    av=atr(h1,14)
    if av<=0:return None
    max_h1=int(getattr(cfg,'ATS_REVERSAL_MAX_H1_BARS',2))
    if s.age>max_h1: s.invalid=True; return None
    inv=av*getattr(cfg,'ATS_REVERSAL_INVALIDATION_ATR',.12)
    if (s.side=='LONG' and c.close<s.extreme-inv) or (s.side=='SHORT' and c.close>s.extreme+inv):
        s.invalid=True; return None
    wanted=1 if s.side=='LONG' else -1

    # H1 owns the structural confirmation.  Trigger is the internal H1 level
    # frozen when the sweep candidate was created; a close through it prevents
    # an intrabar M15 poke from becoming a standalone signal.
    broken=(c.close>s.trigger and c.close>c.open) if wanted>0 else (c.close<s.trigger and c.close<c.open)
    body_atr=abs(c.close-c.open)/av
    if not broken or body_atr<getattr(cfg,'ATS_REVERSAL_CONFIRM_BODY_ATR',.12):return None
    if _bias('H1',h1)!=wanted:return None

    # M15/M5 are confirmation-only.  Require no explicit opposite micro bias;
    # reward aligned M15, but never create direction from it.
    m15_bias=_bias('M15',m15) if len(m15)>=25 else 0
    m5_bias=_bias('M5',m5) if len(m5)>=25 else 0
    if m15_bias==-wanted or m5_bias==-wanted:return None

    # Reuse the project-wide pullback/range classifier so ATS cannot maintain a
    # private definition of a correction.  This is context, not direction.
    d1=_bars(by_tf or {},'D1') if by_tf else []
    d1_bias=_bias('D1',d1) if d1 else 0
    h4_bias=_bias('H4',h4)
    pb=pullback_regime.classify(s.symbol,wanted,d1_bias,h4_bias,by_tf or {})

    # Global anti-late geometry is mandatory for every new H1-confirmed entry.
    early=ohlc_movement.early_entry_check(by_tf or {},wanted)
    if not early.get('allow',True):return None

    base,quote=split_pair(s.symbol); gap=strength.get(base,0)-strength.get(quote,0)
    strength_ok=gap>=0 if wanted>0 else gap<=0
    rejection_bonus=3 if s.rejection_class.startswith('ЭКСТРЕМАЛЬНЫЙ') else (1 if s.rejection_class.startswith('СИЛЬНЫЙ') else 0)
    micro_bonus=2 if m15_bias==wanted else 0
    score=76 + (6 if strength_ok else 0) + 5 + (4 if h4_bias==0 else 0) + rejection_bonus + micro_bonus
    score=min(94,score); conf=max(70,min(91,score-4))
    return {'symbol':s.symbol,'side':s.side,'extreme':s.extreme,'trigger':s.trigger,'close':c.close,'dt':c.dt,
            'quality':score,'confidence':conf,'gap':gap,'strength_ok':strength_ok,'tf':'H1',
            'wick_anomaly':s.wick_anomaly,'rejection_class':s.rejection_class,
            'pullback_mode':pb.mode,'pullback_bars':pb.bars,'pullback_move_atr':pb.move_atr,
            'pullback_efficiency':pb.efficiency,'market_regime':pb.regime,
            'm15_bias':m15_bias,'m5_bias':m5_bias,'early_reason':early.get('reason','ok')}

def _price(sym,x): return f'{x:.3f}' if 'JPY' in sym else f'{x:.5f}'
def _format(e):
    return '\n'.join([
      '━━━━━━━━━━━━━━━━━━','🎯 ATS REVERSAL POINT — РАЗВОРОТ ПОДТВЕРЖДЁН','━━━━━━━━━━━━━━━━━━','',
      f"Пара: {e['symbol']}",f"Направление разворота: {e['side']}",
      f"Экстремум разворота: {_price(e['symbol'],e['extreme'])}",
      f"Подтверждение: закрытая {e['tf']}-свеча + слом внутренней H1-структуры",
      f"Цена подтверждения: {_price(e['symbol'],e['close'])}",
      f"Хвост H1: {e.get('rejection_class','ОБЫЧНЫЙ REJECTION')} · {float(e.get('wick_anomaly') or 0):.2f}× типичного хвоста",
      f"Режим рынка: {e.get('market_regime','UNKNOWN')} · маршрут: {e.get('pullback_mode','LOCAL')}",
      f"Геометрия возврата: {int(e.get('pullback_bars') or 0)} H1 · {float(e.get('pullback_move_atr') or 0):.2f} ATR · эффективность {float(e.get('pullback_efficiency') or 0):.0%}",
      f"M15/M5: {'СОГЛАСОВАНО' if e.get('m15_bias') in (0, 1 if e['side']=='LONG' else -1) and e.get('m5_bias') in (0, 1 if e['side']=='LONG' else -1) else 'НЕЙТРАЛЬНО'} · только подтверждение",
      f"Разница силы валют: {e['gap']:+.2f}",f"Качество: {e['quality']}/100",f"Вероятность: {e['confidence']}%",'',
      '✅ Факт: цена сняла предыдущий H1-экстремум, вернулась за него и следующей закрытой H1-свечой подтвердила смену внутренней структуры.',
      'ℹ️ ATS не создаёт сигнал принудительно: без подтверждённого разворота карточка не отправляется.'
    ])

def process_market(market,strength):
    st=_load(); first=not st.get('bootstrapped'); setups={k:Setup(**v) for k,v in (st.get('setups') or {}).items()}; pending=st.setdefault('pending',{}); out=[item['text'] for item in pending.values() if isinstance(item,dict) and item.get('text')]
    for symbol in cfg.PAIRS:
      try:
        by_tf=market.get(symbol) or {}; h4,h1,m15,m5=(_bars(by_tf,t) for t in ('H4','H1','M15','M5'))
        for s in list(setups.values()):
          if s.symbol!=symbol or s.sent or s.invalid:continue
          e=_confirm(s,h4,h1,m15,m5,strength,by_tf)
          if e and not first:
            og=ohlc_movement.guard_event(by_tf,e.get('side'),e.get('quality'))
            if not og.get('allow',True): continue
            if 'quality' in og: e['quality']=og['quality']; e['confidence']=min(e.get('confidence',90), max(0,e['quality']-3))
            msg=_format(e); digest=hashlib.sha256(msg.encode()).hexdigest()[:20]; pending[digest]={'id':s.id,'text':msg}; out.append(msg); _LAST_CHART_CARDS[msg]=(e,freeze_by_tf(by_tf))
        fresh=_candidate(symbol,h4,h1)
        if fresh and fresh.id not in setups:
          for old in setups.values():
            if old.symbol==symbol and old.side==fresh.side and not old.sent: old.invalid=True
          setups[fresh.id]=fresh
      except Exception: log.exception('ATS Reversal Point %s',symbol)
    st['bootstrapped']=True
    kept=[s for s in setups.values() if not s.invalid][-500:]; st['setups']={s.id:asdict(s) for s in kept}; _save(st); return out

def mark_delivered(text: str) -> bool:
    st=_load(); digest=hashlib.sha256((text or "").encode()).hexdigest()[:20]; item=(st.get("pending") or {}).pop(digest,None)
    if not item: return False
    rec=(st.get("setups") or {}).get(item.get("id"))
    if rec: rec["sent"]=True
    _save(st); _LAST_CHART_CARDS.pop(text,None); return True

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
