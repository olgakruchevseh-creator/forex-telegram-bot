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

def detect(symbol,by_tf,strength,events=None,now_utc=None):
    m5,m15,h1,h4=(_bars(by_tf,t) for t in ("M5","M15","H1","H4"))
    if len(m5)<35 or len(m15)<20 or len(h1)<20: return None
    now=now_utc or _dt(m5[-1]); window=active_window(now)
    if not window or _news_block(symbol,events,now): return None
    av=atr(m5,14) or atr(m15,14)
    if not av: return None
    # Liquidity pool excludes the last 3 bars; a recent bar must sweep and close back inside.
    look=m5[-23:-3]; hi=max(c.high for c in look); lo=min(c.low for c in look)
    sweep=None; side=None; level=None
    for i in range(len(m5)-3,len(m5)):
        c=m5[i]
        if c.low < lo and c.close > lo: sweep=i; side="LONG"; level=lo
        if c.high > hi and c.close < hi: sweep=i; side="SHORT"; level=hi
    if sweep is None: return None
    wanted=1 if side=="LONG" else -1
    gap=_gap(symbol,strength)
    if (wanted*gap) < float(getattr(cfg,"SILVER_BULLET_MIN_STRENGTH_GAP",.05)): return None
    if _bias("H4",h4)==-wanted or _bias("H1",h1)==-wanted: return None
    # Displacement + 3-candle FVG after the sweep. This is the completion trigger.
    min_disp=av*float(getattr(cfg,"SILVER_BULLET_MIN_DISPLACEMENT_ATR",.55))
    min_fvg=av*float(getattr(cfg,"SILVER_BULLET_MIN_FVG_ATR",.08))
    fvg=None; trigger=None
    start=max(2,sweep)
    for i in range(start,len(m5)):
        c=m5[i]
        body=abs(c.close-c.open)
        if body < min_disp: continue
        if side=="LONG" and c.close>c.open and m5[i].low-m5[i-2].high >= min_fvg:
            fvg=(m5[i-2].high,m5[i].low); trigger=c; break
        if side=="SHORT" and c.close<c.open and m5[i-2].low-m5[i].high >= min_fvg:
            fvg=(m5[i].high,m5[i-2].low); trigger=c; break
    if not fvg: return None
    # Late-entry ban: current close may not already have run >1 ATR from FVG midpoint.
    mid=sum(fvg)/2; current=m5[-1].close
    if (current-mid)*wanted > av: return None
    quality=min(94,78 + (5 if _bias("H1",h1)==wanted else 0)+(4 if _bias("H4",h4)==wanted else 0)+min(7,int(abs(gap)*30)))
    return {"symbol":symbol,"side":side,"window":window,"liquidity":level,"sweep":m5[sweep].low if side=="LONG" else m5[sweep].high,
            "fvg_low":min(fvg),"fvg_high":max(fvg),"entry":mid,"close":current,"quality":quality,"confidence":max(70,quality-5),
            "gap":gap,"trigger_dt":trigger.dt,"atr":av}

def _p(symbol,x): return f"{x:.3f}" if "JPY" in symbol else f"{x:.5f}"
def format_message(e):
    return "\n".join(["━━━━━━━━━━━━━━━━━━",f"🥈 ICT SILVER BULLET — {e['side']}","━━━━━━━━━━━━━━━━━━","",f"💱 Пара: {e['symbol']}",f"Окно: {e['window']}",
        f"Направление: {e['side']}",f"Снятая ликвидность: {_p(e['symbol'],e['liquidity'])}",f"Экстремум sweep: {_p(e['symbol'],e['sweep'])}",
        f"FVG: {_p(e['symbol'],e['fvg_low'])}–{_p(e['symbol'],e['fvg_high'])}",f"Рабочая середина FVG: {_p(e['symbol'],e['entry'])}",
        f"Разница силы валют: {e['gap']:+.2f}",f"Качество: {e['quality']}/100",f"Вероятность: {e['confidence']}%","",
        "✅ Факт: в активном Silver Bullet окне снята ликвидность, затем закрытой M5 подтверждены displacement/MSS и FVG. Поздний вход заблокирован автоматически."])

def process_market(market,strength,events=None,now_utc=None):
    st=_load(); sent=st.setdefault('sent',{}); out=[]
    for symbol in cfg.PAIRS:
        e=detect(symbol,market.get(symbol) or {},strength,events,now_utc)
        if not e: continue
        key=f"{symbol}|{e['side']}|{e['window']}|{e['trigger_dt']}"
        if key in sent: continue
        text=format_message(e); out.append(text); sent[key]=datetime.now(timezone.utc).isoformat(); _PENDING_CARDS[text]=(dict(e),freeze_by_tf(market.get(symbol) or {}))
    if len(sent)>500: st['sent']=dict(list(sent.items())[-350:])
    _save(st); return out

def render_chart(e,by_tf):
    import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    bars=list(by_tf.get('M5') or [])[-int(getattr(cfg,'SILVER_BULLET_CHART_LOOKBACK',72)):]
    if not bars:return None
    fig,ax=plt.subplots(figsize=(10,5.4))
    for i,c in enumerate(bars):
        ax.vlines(i,c.low,c.high,linewidth=1); ax.add_patch(Rectangle((i-.3,min(c.open,c.close)),.6,max(abs(c.close-c.open),(c.high-c.low)*.015),fill=False,linewidth=1))
    ax.axhspan(e['fvg_low'],e['fvg_high'],alpha=.12); ax.axhline(e['liquidity'],linestyle='--',linewidth=1.2)
    ax.set_title(f"{e['symbol']} · ICT Silver Bullet · {e['side']} · {e['window']}"); ax.grid(True,alpha=.2); fig.tight_layout()
    b=io.BytesIO(); fig.savefig(b,format='png',dpi=150,bbox_inches='tight'); plt.close(fig); b.seek(0); return b

def image_for_alert(text):
    if not getattr(cfg,'SILVER_BULLET_CHART_IMAGES_ENABLED',True): return None
    card=_PENDING_CARDS.pop(text,None); return render_chart(*card) if card else None
