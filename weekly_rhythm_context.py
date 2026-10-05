"""Weekly Rhythm — shared weekly context, never a standalone LONG/SHORT strategy.

Combines time-in-week, Weekly Open/current WH-WL, H4/D1 dealing-range position,
liquidity sweep/reclaim and the existing structure/regime facts into one lifecycle.
It does not emit Telegram cards, create an evidence family or vote independently.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
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
    extreme_day: str
    rhythm_model: str
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

    # Weekly rhythm: identify WHEN the current weekly extreme formed.  The weekday is
    # context, never a forced forecast.  Tue/Wed/Thu are common formation windows, but
    # confirmation still requires price delivery away from the extreme + HTF location + structure.
    candidate=""
    extreme_day=""
    rhythm_model=""
    recent_n=max(1,int(getattr(cfg,"WEEKLY_RHYTHM_EXTREME_RECENT_H4",6)))
    recent=current[-recent_n:]
    low_bar=min(current,key=lambda c: float(c.low))
    high_bar=max(current,key=lambda c: float(c.high))
    low_dt=_dt(low_bar.dt); high_dt=_dt(high_bar.dt)
    low_age=max(0,(last_dt-low_dt).total_seconds()/3600.0) if low_dt else 999
    high_age=max(0,(last_dt-high_dt).total_seconds()/3600.0) if high_dt else 999
    away_atr=float(getattr(cfg,"WEEKLY_RHYTHM_EXTREME_AWAY_ATR",0.35))*max(av,1e-12)
    low_departed=px >= wl + away_atr
    high_departed=px <= wh - away_atr
    max_age=float(getattr(cfg,"WEEKLY_RHYTHM_EXTREME_MAX_AGE_HOURS",72))

    # Candidate is retained internally.  Merely printing a fresh WH/WL is not an event.
    if low_age <= max_age and low_departed: candidate="LOW"
    if high_age <= max_age and high_departed:
        if not candidate or (wh-px)>(px-wl): candidate="HIGH"

    ext_dt=low_dt if candidate=="LOW" else high_dt if candidate=="HIGH" else None
    if ext_dt:
        extreme_day=names[ext_dt.isocalendar().weekday-1]
        ed=ext_dt.isocalendar().weekday
        if ed==1: rhythm_model="РАННИЙ_ЭКСТРЕМУМ_ВТОРНИК"
        elif ed==2: rhythm_model="РАЗВОРОТ_СЕРЕДИНЫ_НЕДЕЛИ"
        elif ed==3: rhythm_model="ПОЗДНИЙ_ЭКСТРЕМУМ_ЧЕТВЕРГ"
        elif ed==0: rhythm_model="РАННИЙ_ЭКСТРЕМУМ_ПОНЕДЕЛЬНИК"
        else: rhythm_model="НЕТИПИЧНОЕ_ОКНО"

    # Screenshot logic: an extreme can be confirmed either by an external liquidity reclaim
    # OR by a genuine departure from the weekly extreme while H4 is in the correct half of
    # its dealing range.  In both cases structural delivery in the new direction is mandatory.
    low_location=h4pd in ("DISCOUNT","EQUILIBRIUM")
    high_location=h4pd in ("PREMIUM","EQUILIBRIUM")
    low_reclaim=(sweep=="SSL_RECLAIM") or (low_departed and low_location)
    high_reclaim=(sweep=="BSL_RECLAIM") or (high_departed and high_location)
    strong_structure=sstate in ("CONFIRMED","SHIFT_CONFIRMED")
    confirmed=(candidate=="LOW" and low_reclaim and sside>0 and strong_structure) or \
              (candidate=="HIGH" and high_reclaim and sside<0 and strong_structure)

    # A confirmed weekly extreme is itself the directional rhythm.  This is context, not entry.
    if confirmed:
        extreme_side=1 if candidate=="LOW" else -1
        if confidence >= int(getattr(cfg,"WEEKLY_RHYTHM_EXTREME_MIN_CONFIDENCE",58)):
            expansion_side=extreme_side
            confidence_state="ПОДТВЕРЖДЕНО"
            invalidated=False

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
    if weekday==4 and expansion_side and realization>=float(getattr(cfg,"WEEKLY_RHYTHM_FRIDAY_MATURE_REALIZATION",0.70)):
        phase="ПЯТНИЦА_ПОДВЕДЕНИЕ_ИТОГА"

    target=None; target_name=""
    if expansion_side>0:
        candidates=[x for x in (pwh,wh) if x is not None and x>px]
        if candidates: target=min(candidates); target_name="PWH/WH"
    elif expansion_side<0:
        candidates=[x for x in (pwl,wl) if x is not None and x<px]
        if candidates: target=max(candidates); target_name="PWL/WL"

    return WeeklyRhythmContext(weekday,names[weekday-1],wo,wh,wl,px,pos,
        h4pd, d1pd,
        phase,expansion_side,candidate,bool(confirmed),extreme_day,rhythm_model,sweep,realization,target,target_name,sstate,regime,
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

def _delivery_path() -> Path:
    root=os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "weekly_rhythm_delivery.json"

def _load_delivery() -> dict:
    try:
        data=json.loads(_delivery_path().read_text(encoding="utf-8"))
        return data if isinstance(data,dict) else {}
    except Exception:
        return {}

def _save_delivery(data: dict) -> None:
    try:
        path=_delivery_path(); path.parent.mkdir(parents=True,exist_ok=True)
        tmp=path.with_suffix(".tmp"); tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8"); tmp.replace(path)
    except Exception:
        log.exception("Не удалось сохранить состояние Ритма недели")

def _confirmed_side(ctx: WeeklyRhythmContext) -> int:
    return int(ctx.expansion_side or 0)

def _event_kind(ctx: WeeklyRhythmContext, previous_side: int = 0) -> str:
    """Стабильное событие. Отмена использует последнее подтверждённое направление."""
    if ctx.invalidated and previous_side:
        return f"СЦЕНАРИЙ_ОТМЕНЁН:{previous_side}"
    if ctx.extreme_confirmed and ctx.expansion_side:
        return f"ЭКСТРЕМУМ_ПОДТВЕРЖДЁН:{ctx.extreme_candidate}:{ctx.expansion_side}"
    if ctx.phase == "НЕДЕЛЬНАЯ_ЭКСПАНСИЯ" and ctx.expansion_side:
        return f"НЕДЕЛЬНАЯ_ЭКСПАНСИЯ:{ctx.expansion_side}"
    if ctx.phase == "ДВИЖЕНИЕ_РЕАЛИЗОВАНО" and ctx.expansion_side:
        return f"ДВИЖЕНИЕ_РЕАЛИЗОВАНО:{ctx.expansion_side}"
    return ""

def _event_key(symbol: str, kind: str) -> str:
    return f"{symbol}:{kind}"

def _significant(ctx: WeeklyRhythmContext) -> bool:
    """Совместимость тестов: без предыдущего подтверждения отмена остаётся тихой."""
    return bool(_event_kind(ctx, 0))

def format_alert(symbol: str, ctx: WeeklyRhythmContext, previous_side: int = 0) -> str:
    display_side=ctx.expansion_side or (previous_side if ctx.invalidated else 0)
    side="ЛОНГ" if display_side>0 else "ШОРТ" if display_side<0 else "НАПРАВЛЕНИЕ НЕ ПОДТВЕРЖДЕНО"
    phase="СЦЕНАРИЙ ОТМЕНЁН" if ctx.invalidated and previous_side else ctx.phase
    return "\n".join([
        "📅 РИТМ НЕДЕЛИ — КОНТЕКСТ", "━━━━━━━━━━━━━━━━━━",
        f"Пара: {symbol}", f"Фаза: {phase}", f"Направление: {side}",
        f"Модель недели: {ctx.rhythm_model or 'формируется'}", f"День экстремума: {ctx.extreme_day or 'ещё не подтверждён'}",
        f"Уверенность контекста: {ctx.confidence}/100 · {ctx.confidence_state}",
        f"Weekly Open: {ctx.weekly_open:.5f}", f"WH / WL: {ctx.weekly_high:.5f} / {ctx.weekly_low:.5f}",
        f"Положение в недельном диапазоне: {ctx.week_position*100:.0f}%", f"H4: {ctx.h4_pd} · D1: {ctx.d1_pd}",
        f"Снятие ликвидности/возврат: {ctx.sweep_reclaim or 'нет подтверждения'}",
        f"Структура: {ctx.structure_state} · Режим: {ctx.regime}", f"Реализация недельного движения: {ctx.realization*100:.0f}%",
        f"Штраф за конфликты: {ctx.conflict_penalty}", "Факт: это недельный контекст, а не самостоятельный сигнал на вход.",
    ])

def process_market(market: dict, strength: dict | None = None) -> list[str]:
    state=_load_delivery(); delivered=set(state.get("delivered") or []); last=state.get("last_event") or {}; confirmed=state.get("confirmed_side") or {}
    out=[]
    for symbol, by_tf in (market or {}).items():
        ctx=analyze_symbol(symbol,by_tf)
        if not ctx: continue
        previous=int(confirmed.get(symbol,0) or 0)
        kind=_event_kind(ctx,previous)
        if not kind: continue
        key=_event_key(symbol,kind)
        if last.get(symbol)==kind or key in delivered: continue
        text=format_alert(symbol,ctx,previous)
        _PENDING_CARDS[text]=({"symbol":symbol,"ctx":ctx,"kind":kind,"previous_side":previous},freeze_by_tf(by_tf))
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
    if not card: return
    event=card[0]; symbol=event["symbol"]; ctx=event["ctx"]; kind=event.get("kind") or _event_kind(ctx,event.get("previous_side",0))
    state=_load_delivery(); delivered=set(state.get("delivered") or []); delivered.add(_event_key(symbol,kind))
    state["delivered"]=sorted(delivered)[-500:]; state.setdefault("last_event",{})[symbol]=kind
    if ctx.expansion_side: state.setdefault("confirmed_side",{})[symbol]=int(ctx.expansion_side)
    elif ctx.invalidated: state.setdefault("confirmed_side",{}).pop(symbol,None)
    _save_delivery(state)
