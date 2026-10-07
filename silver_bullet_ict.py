"""ICT Silver Bullet: liquidity sweep -> displacement/MSS -> FVG in a NY kill-zone.

The module only emits a completed setup. Forming setups stay internal. It uses
closed candles, New-York DST-aware windows, currency strength, H1/H4 context,
high-impact-news protection, event anti-spam and an immutable chart snapshot.
"""
from __future__ import annotations
from chart_snapshot import freeze_by_tf
import io, json, os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import config as cfg
import ohlc_movement
import liquidity_map
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair

NY = ZoneInfo("America/New_York")
TF_MIN = {"H4":240,"H1":60,"M15":15,"M5":5}
_PENDING_CARDS: dict[str, tuple[dict,dict]] = {}

def _path():
    root=os.getenv("STATE_DIR","").strip(); return (Path(root) if root else Path(__file__).parent)/"silver_bullet_state.json"
def _load():
    try: return json.loads(_path().read_text())
    except (OSError,ValueError): return {}
def _save(d):
    p=_path(); p.parent.mkdir(parents=True,exist_ok=True); t=p.with_suffix('.tmp'); t.write_text(json.dumps(d,ensure_ascii=False,indent=2)); t.replace(p)
def _bars(by_tf,tf): return closed_candles(by_tf.get(tf) or [],TF_MIN[tf])
def _dt(c):
    s=str(c.dt).replace('Z','+00:00'); d=datetime.fromisoformat(s)
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
def active_window(now_utc:datetime):
    n=now_utc.astimezone(NY); h=n.hour+n.minute/60
    for a,b in getattr(cfg,"SILVER_BULLET_WINDOWS_NY",((3,4),(10,11),(14,15))):
        if a <= h < b: return f"{a:02d}:00–{b:02d}:00 NY"
    return None
def _bias(tf,bars):
    v=analyze_tf(tf,tf,bars) if len(bars)>=20 else None; return v.bias if v else 0
def _gap(symbol,strength):
    b,q=split_pair(symbol); return float(strength.get(b,0))-float(strength.get(q,0))
def _news_block(symbol,events,now):
    base,quote=split_pair(symbol); mins=float(getattr(cfg,"SILVER_BULLET_NEWS_BLOCK_MINUTES",60))
    for e in events or []:
        try:
            if str(getattr(e,'impact','')).upper()!='HIGH' or getattr(e,'currency','') not in (base,quote): continue
            if abs((getattr(e,'dt_utc')-now).total_seconds()) <= mins*60: return True
        except Exception: continue
    return False

def _recent_pivots(bars, left=2, right=2):
    """Confirmed local M5 pivots only; the right-side candles must already be closed."""
    out=[]
    for i in range(left, len(bars)-right):
        area=bars[i-left:i+right+1]
        if bars[i].high >= max(c.high for c in area): out.append((i, bars[i].high, "high"))
        if bars[i].low <= min(c.low for c in area): out.append((i, bars[i].low, "low"))
    return out

def _mss_level(bars, sweep_i, side):
    """Last confirmed opposite pivot before the sweep: the level MSS must close through."""
    piv=[x for x in _recent_pivots(bars[:sweep_i+1]) if x[0] < sweep_i]
    kind="high" if side=="LONG" else "low"
    vals=[x for x in piv if x[2]==kind]
    return vals[-1][1] if vals else None

def _context(symbol, by_tf, side):
    """Read-only confluence. Context improves quality but never invents a Silver Bullet."""
    wanted=1 if side=="LONG" else -1
    support=[]; conflict=[]
    for name in ("ltf_confirmation","choch","premium_discount","htf_irl"):
        try:
            mod=__import__(name); ctx=mod.analyze_symbol(symbol,by_tf,wanted)
            a=getattr(ctx,"alignment",0) if ctx is not None else 0
            if a>0: support.append(name)
            elif a<0: conflict.append(name)
        except Exception:
            continue
    try:
        import displacement
        if displacement.confirm_direction(by_tf,side,"M5"): support.append("displacement")
    except Exception:
        pass
    return support, conflict

def _window_bounds(now_utc: datetime):
    n=now_utc.astimezone(NY); h=n.hour+n.minute/60
    for a,b in getattr(cfg,"SILVER_BULLET_WINDOWS_NY",((3,4),(10,11),(14,15))):
        if a <= h < b: return f"{a:02d}:00–{b:02d}:00 NY",a,b
    return None

def _in_same_window(candle,now_utc,a,b):
    d=_dt(candle).astimezone(NY); n=now_utc.astimezone(NY)
    return d.date()==n.date() and a <= d.hour+d.minute/60 < b

def _candidate_liquidity(symbol,by_tf,m5,window_indices,av):
    out=[]
    try:
        for p in liquidity_map.build_map(symbol,by_tf):
            if p.hierarchy in ("HTF","STRUCTURAL") and p.status!="invalidated":
                out.append((p.side,float(p.level),p.source,p.rank))
    except Exception: pass
    first=min(window_indices) if window_indices else len(m5); pre=m5[max(0,first-20):first]
    if pre:
        out += [("BSL",max(c.high for c in pre),"локальный максимум до окна",1),("SSL",min(c.low for c in pre),"локальный минимум до окна",1)]
    return out

def _find_sweep(m5,window_indices,pools):
    hits=[]
    for i in window_indices:
        c=m5[i]
        for pside,level,source,rank in pools:
            if pside=="SSL" and c.low<level and c.close>level: hits.append((i,"LONG",level,c.low,source,rank))
            elif pside=="BSL" and c.high>level and c.close<level: hits.append((i,"SHORT",level,c.high,source,rank))
    return max(hits,key=lambda x:(x[0],x[5])) if hits else None

def _touches_entry(c,side,entry,tol):
    return c.low<=entry+tol and c.high>=entry-tol

def _targets(symbol,by_tf,side,entry,av):
    want="BSL" if side=="LONG" else "SSL"; direction=1 if side=="LONG" else -1; vals=[]
    try:
        for p in liquidity_map.build_map(symbol,by_tf):
            if p.side==want and p.status!="invalidated" and (p.level-entry)*direction>.10*av:
                vals.append((float(p.level),p.source,p.rank))
    except Exception: pass
    vals.sort(key=lambda x:(x[0]-entry)*direction); chosen=[]
    for x in vals:
        if not chosen or abs(x[0]-chosen[-1][0])>=.15*av: chosen.append(x)
        if len(chosen)==3: break
    mult=(1.0,1.8,2.8)
    while len(chosen)<3:
        k=len(chosen); price=entry+direction*mult[k]*av
        if chosen and (price-chosen[-1][0])*direction<=.15*av: price=chosen[-1][0]+direction*.55*av
        chosen.append((price,f"ATR {mult[k]:.1f}",0))
    return chosen

def detect(symbol,by_tf,strength,events=None,now_utc=None):
    m5,m15,h1,h4=(_bars(by_tf,t) for t in ("M5","M15","H1","H4"))
    if len(m5)<35 or len(m15)<20 or len(h1)<20 or len(h4)<20:return None
    now=now_utc or _dt(m5[-1]); wb=_window_bounds(now)
    if not wb or _news_block(symbol,events,now):return None
    window,a,b=wb; idx=[i for i,c in enumerate(m5) if _in_same_window(c,now,a,b)]
    if len(idx)<2:return None
    av=atr(m5,14) or atr(m15,14)
    if not av:return None
    hit=_find_sweep(m5,idx,_candidate_liquidity(symbol,by_tf,m5,idx,av))
    if not hit:return None
    sweep,side,level,sweep_extreme,liquidity_source,_=hit; wanted=1 if side=="LONG" else -1; gap=_gap(symbol,strength)
    if wanted*gap<float(getattr(cfg,"SILVER_BULLET_MIN_STRENGTH_GAP",.05)):return None
    if _bias("H4",h4)==-wanted or _bias("H1",h1)==-wanted:return None
    mss_level=_mss_level(m5,sweep,side)
    if mss_level is None:return None
    max_age=max(1,int(getattr(cfg,"SILVER_BULLET_MAX_SETUP_AGE_M5",12))); min_disp=av*float(getattr(cfg,"SILVER_BULLET_MIN_DISPLACEMENT_ATR",.55)); min_fvg=av*float(getattr(cfg,"SILVER_BULLET_MIN_FVG_ATR",.08))
    fvg=None; trigger=None; trigger_i=None
    for i in idx:
        if i<=sweep or i-sweep>max_age:continue
        c=m5[i]; body=abs(c.close-c.open); mss=c.close>mss_level if side=="LONG" else c.close<mss_level; impulse=c.close>c.open if side=="LONG" else c.close<c.open
        if not(mss and impulse and body>=min_disp):continue
        if side=="LONG" and m5[i].low-m5[i-2].high>=min_fvg: fvg=(m5[i-2].high,m5[i].low); trigger=c; trigger_i=i; break
        if side=="SHORT" and m5[i-2].low-m5[i].high>=min_fvg: fvg=(m5[i].high,m5[i-2].low); trigger=c; trigger_i=i; break
    if not fvg:return None
    if len(m5)-1-trigger_i>max_age:return None
    entry=sum(fvg)/2; tol=av*float(getattr(cfg,"SILVER_BULLET_ENTRY_TOUCH_TOL_ATR",.05)); fill_i=None
    for i in idx:
        if i>trigger_i and i-trigger_i<=max_age and _touches_entry(m5[i],side,entry,tol): fill_i=i; break
    if fill_i is None or fill_i!=len(m5)-1:return None
    current=m5[fill_i].close; stop_buffer=av*float(getattr(cfg,"SILVER_BULLET_STOP_BUFFER_ATR",.10)); stop=sweep_extreme-wanted*stop_buffer
    if (entry-stop)*wanted<=0:return None
    if (current-entry)*wanted>av*float(getattr(cfg,"SILVER_BULLET_MAX_CHASE_ATR",.45)):return None
    if side=="LONG" and current<min(fvg)-.10*av:return None
    if side=="SHORT" and current>max(fvg)+.10*av:return None
    support,conflict=_context(symbol,by_tf,side); liquidity_pool=None
    try:
        liquidity_pool=liquidity_map.swept_context(symbol,by_tf,side)
        if liquidity_pool:support.append("liquidity_map")
    except Exception:pass
    targets=_targets(symbol,by_tf,side,entry,av); risk=abs(entry-stop); rr1=abs(targets[0][0]-entry)/risk if risk else 0
    if rr1<float(getattr(cfg,"SILVER_BULLET_MIN_TR1_RR",1.0)):return None
    quality=78+(5 if _bias("H1",h1)==wanted else 0)+(4 if _bias("H4",h4)==wanted else 0)+min(5,int(abs(gap)*25))+min(5,2*len(support))-min(6,3*len(conflict))
    if liquidity_source not in ("локальный максимум до окна","локальный минимум до окна"):quality+=3
    quality=max(72,min(95,quality))
    return {"symbol":symbol,"side":side,"window":window,"liquidity":level,"liquidity_source":liquidity_source,"sweep":sweep_extreme,"mss_level":mss_level,"fvg_low":min(fvg),"fvg_high":max(fvg),"entry":entry,"fill":current,"stop":stop,"tr1":targets[0][0],"tr2":targets[1][0],"tr3":targets[2][0],"tr1_source":targets[0][1],"tr2_source":targets[1][1],"tr3_source":targets[2][1],"rr1":rr1,"close":current,"quality":quality,"confidence":max(70,quality-5),"gap":gap,"trigger_dt":trigger.dt,"fill_dt":m5[fill_i].dt,"atr":av,"context_support":support,"context_conflict":conflict,"liquidity_pool":(liquidity_pool.side if liquidity_pool else None)}

def _p(symbol,x): return f"{x:.3f}" if "JPY" in symbol else f"{x:.5f}"
def format_message(e):
    direction="🟢 ЛОНГ" if e["side"]=="LONG" else "🔴 ШОРТ"
    return "\n".join(["━━━━━━━━━━━━━━━━━━",f"⚡ РЕШЕНИЕ: {direction} · {e['symbol']}","Основание: 🥈 ICT SILVER BULLET",f"качество {e['quality']}/100 · M5 execution · {e['window']}","━━━━━━━━━━━━━━━━━━","","ДЕТАЛИ","━━━━━━━━━━━━━━━━━━",f"Снятая ликвидность: {_p(e['symbol'],e['liquidity'])} · {e.get('liquidity_source','—')}",f"Sweep-экстремум: {_p(e['symbol'],e['sweep'])}",f"MSS: закрытие через {_p(e['symbol'],e['mss_level'])}",f"FVG: {_p(e['symbol'],e['fvg_low'])}–{_p(e['symbol'],e['fvg_high'])}",f"Вход CE 50%: {_p(e['symbol'],e['entry'])} · возврат подтверждён закрытой M5",f"Отмена / SL: {_p(e['symbol'],e['stop'])} · за sweep-экстремумом",f"TR1: {_p(e['symbol'],e['tr1'])} · {e.get('tr1_source','')}",f"TR2: {_p(e['symbol'],e['tr2'])} · {e.get('tr2_source','')}",f"TR3: {_p(e['symbol'],e['tr3'])} · {e.get('tr3_source','')}",f"RR до TR1: {e.get('rr1',0):.2f}R",f"Разница силы валют: {e['gap']:+.2f}",f"Вероятность: {e['confidence']}%","","✅ Факт: ликвидность снята внутри активного окна, закрытая M5 дала MSS + displacement + FVG, затем цена вернулась в CE. Формирующийся FVG без возврата наружу не отправляется."])

def process_market(market,strength,events=None,now_utc=None):
    st=_load(); sent=st.setdefault('sent',{}); out=[]
    for symbol in cfg.PAIRS:
        e=detect(symbol,market.get(symbol) or {},strength,events,now_utc)
        if not e: continue
        og=ohlc_movement.guard_event(market.get(symbol) or {},e.get('side'),e.get('quality'))
        if not og.get('allow',True): continue
        if 'quality' in og: e['quality']=og['quality']; e['confidence']=min(e.get('confidence',90),max(0,e['quality']-3))
        key=f"{symbol}|{e['side']}|{e['window']}|{e.get('fill_dt',e['trigger_dt'])}"
        pending=st.setdefault('pending',{})
        if key in sent or key in pending: continue
        text=format_message(e); out.append(text)
        pending[key]=True
        _PENDING_CARDS[text]=(dict(e),freeze_by_tf(market.get(symbol) or {}),key)
    if len(sent)>500: st['sent']=dict(list(sent.items())[-350:])
    _save(st); return out


def mark_delivered(text: str) -> bool:
    card=_PENDING_CARDS.get(text or "")
    key=card[2] if card and len(card) > 2 else None
    if not key:
        return False
    st=_load(); pending=st.setdefault('pending',{}); sent=st.setdefault('sent',{})
    pending.pop(key, None)
    sent[key]=datetime.now(timezone.utc).isoformat()
    if len(sent)>500: st['sent']=dict(list(sent.items())[-350:])
    _save(st)
    return True

def render_chart(e,by_tf):
    import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    bars=list(by_tf.get('M5') or [])[-int(getattr(cfg,'SILVER_BULLET_CHART_LOOKBACK',72)):]
    if not bars:return None
    fig,ax=plt.subplots(figsize=(10,5.4))
    for i,c in enumerate(bars):
        ax.vlines(i,c.low,c.high,linewidth=1); ax.add_patch(Rectangle((i-.3,min(c.open,c.close)),.6,max(abs(c.close-c.open),(c.high-c.low)*.015),fill=False,linewidth=1))
    ax.axhspan(e['fvg_low'],e['fvg_high'],alpha=.12); ax.axhline(e['liquidity'],linestyle='--',linewidth=1.2)
    for label,key,style in (("CE","entry","-."),("SL","stop",":"),("TR1","tr1","--"),("TR2","tr2","--"),("TR3","tr3","--")):
        if e.get(key) is not None:
            ax.axhline(e[key],linestyle=style,linewidth=1); ax.text(max(0,len(bars)-1),e[key],f" {label}",va='center',fontsize=8)
    ax.set_title(f"{e['symbol']} · ICT Silver Bullet · {e['side']} · {e['window']}"); ax.grid(True,alpha=.2); fig.tight_layout()
    b=io.BytesIO(); fig.savefig(b,format='png',dpi=150,bbox_inches='tight'); plt.close(fig); b.seek(0); return b

def image_for_alert(text):
    if not getattr(cfg,'SILVER_BULLET_CHART_IMAGES_ENABLED',True): return None
    card=_PENDING_CARDS.pop(text,None); return render_chart(*card) if card else None
