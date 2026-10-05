"""Weekly Rhythm — shared weekly context, never a standalone LONG/SHORT strategy.

Combines time-in-week, Weekly Open/current WH-WL, H4/D1 dealing-range position,
liquidity sweep/reclaim and the existing structure/regime facts into one lifecycle.
It does not emit Telegram cards, create an evidence family or vote independently.
"""
from __future__ import annotations
from dataclasses import dataclass
import io

from chart_snapshot import freeze_by_tf
from datetime import datetime, timezone

import config as cfg
from analysis import atr, closed_candles
import structure_context
import market_regime

_TF_MIN = {"D1":1440, "H4":240, "H1":60}

@dataclass(frozen=True)
class WeeklyRhythmContext:
    weekday: int
    day_name: str
    weekly_open: float
    weekly_high: float
    weekly_low: float
    price: float
    week_position: float
    h4_pd: str
    d1_pd: str
    phase: str
    expansion_side: int
    extreme_candidate: str
    extreme_confirmed: bool
    sweep_reclaim: str
    realization: float
    target: float | None
    target_name: str
    structure_state: str
    regime: str
    confidence: int
    confidence_state: str
    conflict_penalty: int
    invalidated: bool
    family: str = "WEEKLY_CONTEXT"


def _dt(value: str) -> datetime | None:
    raw=str(value or "").strip().replace("Z", "+00:00")
    try:
        d=datetime.fromisoformat(raw)
    except ValueError:
        try:d=datetime.strptime(raw.replace("T"," ")[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _pd(bars, lookback: int) -> str:
    if len(bars) < max(8, lookback//2): return "UNKNOWN"
    b=bars[-min(len(bars),lookback):]
    lo=min(x.low for x in b); hi=max(x.high for x in b); px=b[-1].close
    if hi <= lo:return "UNKNOWN"
    p=(px-lo)/(hi-lo); band=float(getattr(cfg,"WEEKLY_RHYTHM_EQ_BAND",0.08))
    if abs(p-.5)<=band:return "EQUILIBRIUM"
    return "DISCOUNT" if p<.5 else "PREMIUM"


def _week_key(c):
    d=_dt(c.dt)
    if not d:return None
    iso=d.isocalendar()
    return iso.year, iso.week


def analyze_symbol(symbol: str, by_tf: dict, now_utc: datetime | None = None) -> WeeklyRhythmContext | None:
    if not getattr(cfg,"WEEKLY_RHYTHM_ENABLED",True): return None
    h4=closed_candles((by_tf or {}).get("H4") or [],240)
    h1=closed_candles((by_tf or {}).get("H1") or [],60)
    d1=closed_candles((by_tf or {}).get("D1") or [],1440)
    source=h4 if len(h4)>=8 else h1
    if len(source)<8:return None
    last_dt=_dt(source[-1].dt)
    if not last_dt:return None
    key=_week_key(source[-1]); current=[c for c in source if _week_key(c)==key]
    if not current:return None
    wo=float(current[0].open); wh=max(float(c.high) for c in current); wl=min(float(c.low) for c in current)
    px=float(current[-1].close); width=max(wh-wl,1e-12); pos=max(0.0,min(1.0,(px-wl)/width))
    iso=last_dt.isocalendar(); weekday=int(iso.weekday)
    names=("ПН","ВТ","СР","ЧТ","ПТ","СБ","ВС")

    # Previous completed week gives a stable realization yardstick and external targets.
    previous=[c for c in source if (_week_key(c) or (9999,99)) < key]
    prev_key=_week_key(previous[-1]) if previous else None
    prev_week=[c for c in previous if _week_key(c)==prev_key] if prev_key else []
    pwh=max((c.high for c in prev_week),default=None); pwl=min((c.low for c in prev_week),default=None)
    prev_range=(pwh-pwl) if pwh is not None and pwl is not None and pwh>pwl else width
    realization=min(2.0,width/max(prev_range,1e-12))

    av=atr(source[-min(40,len(source)):],14) if len(source)>=15 else width/max(1,len(current))
    reject=float(getattr(cfg,"WEEKLY_RHYTHM_RECLAIM_ATR",0.12))*max(av,1e-12)
    sweep=""
    if pwh is not None and max(c.high for c in current)>pwh and px < pwh-reject: sweep="BSL_RECLAIM"
    elif pwl is not None and min(c.low for c in current)<pwl and px > pwl+reject: sweep="SSL_RECLAIM"

    sctx=structure_context.analyze_symbol(symbol,by_tf,0)
    sside=getattr(sctx,"side",0); sstate=getattr(sctx,"state","FORMING")
    rctx=market_regime.analyze_symbol(symbol,by_tf); regime=getattr(rctx,"name","UNKNOWN")
    open_side=1 if px>wo+reject else -1 if px<wo-reject else 0

    # Direction is deliberately conservative: Weekly Open alone never confirms it.
    # Score independent context facts and subtract conflicts; this remains context, not a trade probability.
    raw_side = sside if sside and sstate in ("CONFIRMED","SHIFT_CONFIRMED") else open_side
    score = 0
    penalty = 0
    if raw_side:
        if sside == raw_side and sstate in ("CONFIRMED","SHIFT_CONFIRMED"): score += 30
        if open_side == raw_side: score += 12
        elif open_side and open_side != raw_side: penalty += 18
        h4pd=_pd(h4,int(getattr(cfg,"WEEKLY_RHYTHM_H4_PD_LOOKBACK",30)))
        d1pd=_pd(d1,int(getattr(cfg,"WEEKLY_RHYTHM_D1_PD_LOOKBACK",20)))
        favorable = "DISCOUNT" if raw_side > 0 else "PREMIUM"
        adverse = "PREMIUM" if raw_side > 0 else "DISCOUNT"
        if h4pd == favorable: score += 12
        elif h4pd == adverse: penalty += 8
        if d1pd == favorable: score += 16
        elif d1pd == adverse: penalty += 12
        if regime in ("TREND","EXPANSION"): score += 10
        elif regime in ("RANGE","COMPRESSION"): penalty += 10
        # Mature movement is useful context but a poor place to assert a fresh direction.
        if realization >= float(getattr(cfg,"WEEKLY_RHYTHM_OVEREXTENDED_REALIZATION",1.15)): penalty += 12
    else:
        h4pd=_pd(h4,int(getattr(cfg,"WEEKLY_RHYTHM_H4_PD_LOOKBACK",30)))
        d1pd=_pd(d1,int(getattr(cfg,"WEEKLY_RHYTHM_D1_PD_LOOKBACK",20)))
    confidence=max(0,min(100,score-penalty+20 if raw_side else 0))
    min_conf=int(getattr(cfg,"WEEKLY_RHYTHM_DIRECTION_MIN_CONFIDENCE",68))
    invalidated=bool(raw_side and ((sside and open_side and sside != open_side and penalty >= 18) or confidence < int(getattr(cfg,"WEEKLY_RHYTHM_INVALIDATED_CONFIDENCE",35))))
    expansion_side=raw_side if raw_side and confidence>=min_conf and not invalidated else 0
    confidence_state="ПОДТВЕРЖДЕНО" if expansion_side else ("ОТМЕНЕНО" if invalidated else "КАНДИДАТ")

    # A weekly extreme is first a candidate; confirmation needs reclaim + opposite structural delivery.
    candidate=""
    recent_n=max(1,int(getattr(cfg,"WEEKLY_RHYTHM_EXTREME_RECENT_H4",6)))
    recent=current[-recent_n:]
    if recent:
        if min(c.low for c in recent)<=wl and px>=wl+reject: candidate="LOW"
        if max(c.high for c in recent)>=wh and px<=wh-reject:
            # If both occurred in a tiny early range, keep the edge furthest from price.
            if not candidate or (wh-px)>(px-wl): candidate="HIGH"
    confirmed=(candidate=="LOW" and sweep=="SSL_RECLAIM" and sside>0 and sstate=="SHIFT_CONFIRMED") or \
              (candidate=="HIGH" and sweep=="BSL_RECLAIM" and sside<0 and sstate=="SHIFT_CONFIRMED")

    if weekday<=1 and realization < float(getattr(cfg,"WEEKLY_RHYTHM_EXPANSION_REALIZATION",0.55)):
        phase="НАЧАЛО_НЕДЕЛИ"
    elif confirmed:
        phase="ЭКСТРЕМУМ_ПОДТВЕРЖДЁН"
    elif candidate:
        phase="ЭКСТРЕМУМ_КАНДИДАТ"
    elif expansion_side and (realization>=float(getattr(cfg,"WEEKLY_RHYTHM_EXPANSION_REALIZATION",0.55)) or regime in ("TREND","EXPANSION")):
        phase="НЕДЕЛЬНАЯ_ЭКСПАНСИЯ"
    else:
        phase="ПОИСК_ЭКСТРЕМУМА"
    if realization>=float(getattr(cfg,"WEEKLY_RHYTHM_MATURE_REALIZATION",0.90)) and phase=="НЕДЕЛЬНАЯ_ЭКСПАНСИЯ":
        phase="ДВИЖЕНИЕ_РЕАЛИЗОВАНО"

    target=None; target_name=""
    if expansion_side>0:
        candidates=[x for x in (pwh,wh) if x is not None and x>px]
        if candidates: target=min(candidates); target_name="PWH/WH"
    elif expansion_side<0:
        candidates=[x for x in (pwl,wl) if x is not None and x<px]
        if candidates: target=max(candidates); target_name="PWL/WL"

    return WeeklyRhythmContext(weekday,names[weekday-1],wo,wh,wl,px,pos,
        h4pd, d1pd,
        phase,expansion_side,candidate,bool(confirmed),sweep,realization,target,target_name,sstate,regime,
        confidence,confidence_state,penalty,invalidated)


def alignment(ctx: WeeklyRhythmContext | None, side: int) -> int:
    """Context agreement only. Callers decide how to use it; it is not a vote."""
    if not ctx or side not in (-1,1) or not ctx.expansion_side:return 0
    return 1 if ctx.expansion_side==side else -1


def describe(ctx: WeeklyRhythmContext | None) -> str:
    if not ctx:return "Ритм недели: данных недостаточно"
    direction="LONG" if ctx.expansion_side>0 else "SHORT" if ctx.expansion_side<0 else "НЕЙТРАЛЬНО"
    ext=(f" · экстремум {ctx.extreme_candidate}" + (" подтверждён" if ctx.extreme_confirmed else " кандидат")) if ctx.extreme_candidate else ""
    sweep=f" · {ctx.sweep_reclaim}" if ctx.sweep_reclaim else ""
    target=f" · цель {ctx.target:.5f}" if ctx.target is not None else ""
    return (f"Ритм недели {ctx.day_name}: {ctx.phase} · {direction} · WO {ctx.weekly_open:.5f} · "
            f"WH/WL {ctx.weekly_high:.5f}/{ctx.weekly_low:.5f} · позиция {ctx.week_position*100:.0f}% · "
            f"H4 {ctx.h4_pd} · D1 {ctx.d1_pd} · реализация {ctx.realization*100:.0f}% · уверенность {ctx.confidence}/100 ({ctx.confidence_state}){ext}{sweep}{target}")


_PENDING_CARDS: dict[str, tuple[dict, dict]] = {}
_DELIVERED: set[str] = set()
_LAST_EVENT: dict[str, str] = {}

def _event_kind(ctx: WeeklyRhythmContext) -> str:
    """Stable Telegram event identity; confidence/candidate noise must not create a new alert."""
    if ctx.invalidated:
        return "СЦЕНАРИЙ_ОТМЕНЁН"
    if ctx.extreme_confirmed:
        return f"ЭКСТРЕМУМ_ПОДТВЕРЖДЁН:{ctx.extreme_candidate}:{ctx.expansion_side}"
    if ctx.sweep_reclaim:
        return f"SWEEP_RECLAIM:{ctx.sweep_reclaim}"
    if ctx.phase == "НЕДЕЛЬНАЯ_ЭКСПАНСИЯ" and ctx.expansion_side:
        return f"НЕДЕЛЬНАЯ_ЭКСПАНСИЯ:{ctx.expansion_side}"
    if ctx.phase == "ДВИЖЕНИЕ_РЕАЛИЗОВАНО" and ctx.expansion_side:
        return f"ДВИЖЕНИЕ_РЕАЛИЗОВАНО:{ctx.expansion_side}"
    return ""

def _event_key(symbol: str, ctx: WeeklyRhythmContext) -> str:
    return f"{symbol}:{_event_kind(ctx)}"

def _significant(ctx: WeeklyRhythmContext) -> bool:
    # Telegram is event-driven. Candidate/confidence fluctuations stay internal.
    return bool(_event_kind(ctx))

def format_alert(symbol: str, ctx: WeeklyRhythmContext) -> str:
    side="ЛОНГ" if ctx.expansion_side>0 else "ШОРТ" if ctx.expansion_side<0 else "НАПРАВЛЕНИЕ НЕ ПОДТВЕРЖДЕНО"
    return "\n".join([
        "📅 РИТМ НЕДЕЛИ — КОНТЕКСТ",
        "━━━━━━━━━━━━━━━━━━",
        f"Пара: {symbol}", f"Фаза: {ctx.phase}", f"Направление: {side}",
        f"Уверенность контекста: {ctx.confidence}/100 · {ctx.confidence_state}",
        f"Weekly Open: {ctx.weekly_open:.5f}", f"WH / WL: {ctx.weekly_high:.5f} / {ctx.weekly_low:.5f}",
        f"Положение в недельном диапазоне: {ctx.week_position*100:.0f}%",
        f"H4: {ctx.h4_pd} · D1: {ctx.d1_pd}",
        f"Sweep/возврат: {ctx.sweep_reclaim or 'нет подтверждения'}",
        f"Структура: {ctx.structure_state} · Режим: {ctx.regime}",
        f"Реализация недельного движения: {ctx.realization*100:.0f}%",
        f"Штраф за конфликты: {ctx.conflict_penalty}",
        "Факт: это недельный контекст, а не самостоятельный сигнал на вход.",
    ])

def process_market(market: dict, strength: dict | None = None) -> list[str]:
    out=[]
    for symbol, by_tf in (market or {}).items():
        ctx=analyze_symbol(symbol,by_tf)
        if not ctx or not _significant(ctx): continue
        kind=_event_kind(ctx)
        key=_event_key(symbol,ctx)
        # The same stable event is never re-announced just because confidence/aux fields moved.
        if _LAST_EVENT.get(symbol) == kind or key in _DELIVERED: continue
        text=format_alert(symbol,ctx)
        _PENDING_CARDS[text]=({"symbol":symbol,"ctx":ctx},freeze_by_tf(by_tf))
        out.append(text)
    return out

def render_chart(event: dict, by_tf: dict) -> io.BytesIO | None:
    from PIL import Image, ImageDraw, ImageFont
    ctx=event["ctx"]; bars=closed_candles((by_tf or {}).get("H4") or [],240)[-42:]
    if len(bars)<8:return None
    width,height=1200,720; left,right,top,bottom=70,1125,85,610
    vals=[v for b in bars for v in (b.low,b.high)]+[ctx.weekly_open,ctx.weekly_high,ctx.weekly_low,ctx.price]
    lo,hi=min(vals),max(vals); pad=max((hi-lo)*.08,abs(ctx.price)*.0001); lo-=pad; hi+=pad
    im=Image.new("RGB",(width,height),"#10131d"); d=ImageDraw.Draw(im,"RGBA")
    try:
        font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",22); small=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",17)
    except OSError: font=small=ImageFont.load_default()
    def x(i):return left+i/max(1,len(bars)-1)*(right-left)
    def y(p):return bottom-(p-lo)/max(1e-12,hi-lo)*(bottom-top)
    for i,b in enumerate(bars):
        xx=x(i); col="#37d67a" if b.close>=b.open else "#ff5c6c"; d.line((xx,y(b.high),xx,y(b.low)),fill=col,width=2); d.rectangle((xx-5,min(y(b.open),y(b.close)),xx+5,max(y(b.open),y(b.close))+1),fill=col)
    for price,label,col in ((ctx.weekly_open,"WO","#f4dc4b"),(ctx.weekly_high,"WH","#6db7ff"),(ctx.weekly_low,"WL","#6db7ff")):
        yy=y(price); d.line((left,yy,right,yy),fill=col,width=2); d.text((right-42,yy-22),label,fill=col,font=small)
    d.text((left,25),f"{event['symbol']} · РИТМ НЕДЕЛИ · {ctx.phase}",fill="#f1f5fb",font=font)
    d.text((left,650),f"Уверенность {ctx.confidence}/100 · {ctx.confidence_state} · закрытые H4 свечи",fill="#aeb7c6",font=small)
    out=io.BytesIO(); out.name=f"weekly_rhythm_{event['symbol'].replace('/','')}.png"; im.save(out,format="PNG",optimize=True); out.seek(0); return out

def image_for_alert(text: str):
    card=_PENDING_CARDS.get(text)
    if not card or not getattr(cfg,"WEEKLY_RHYTHM_CHART_IMAGES_ENABLED",True):return None
    return render_chart(card[0],card[1])

def mark_delivered(text: str) -> None:
    card=_PENDING_CARDS.pop(text,None)
    if card:
        symbol=card[0]["symbol"]; ctx=card[0]["ctx"]
        _DELIVERED.add(_event_key(symbol,ctx))
        _LAST_EVENT[symbol]=_event_kind(ctx)
