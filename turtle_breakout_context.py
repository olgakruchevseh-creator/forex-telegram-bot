"""Turtle / Turtle Soup breakout-quality context.

Context-only synthesis of the book-derived ideas we agreed to keep:
- level maturity and repeated tests;
- breakout -> acceptance vs failure/reclaim;
- delayed failure (Turtle Soup Plus One adaptation);
- trapped-breakout context;
- fast invalidation when price is accepted beyond the level;
- confirmed re-entry only after a fresh reclaim + displacement.

It never creates a Telegram signal and never counts as an independent KILLER family.
All decisions use CLOSED candles only.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, replace
from analysis import atr, closed_candles
import config as cfg
import liquidity_map

@dataclass(frozen=True)
class TurtleBreakoutContext:
    available: bool=False
    direction: int=0
    state: str="NONE"
    level: float=0.0
    source: str=""
    timeframe: str=""
    maturity_bars: int=0
    attempts: int=0
    reclaims: int=0
    plus_one: bool=False
    trapped: bool=False
    invalidated: bool=False
    reentry_ready: bool=False
    break_depth_atr: float=0.0
    reclaim_body_atr: float=0.0
    trap_quality: int=0
    acceptance_closes: int=0
    wyckoff_test: bool=False
    wyckoff_test_confirmed: bool=False
    first_attempt_failed: bool=False
    second_entry_ready: bool=False
    lifecycle_note: str=""
    alignment: int=0
    score: int=50
    reason: str=""
    family: str="TURTLE_BREAKOUT_CONTEXT"
    delayed_failure: bool=False
    failure_bars: int=0
    body_break_seen: bool=False
    def as_dict(self): return asdict(self)

def _side(direction):
    return 1 if direction in (1,"LONG") else -1 if direction in (-1,"SHORT") else 0

def analyze_symbol(symbol, by_tf, direction):
    if not getattr(cfg,"TURTLE_BREAKOUT_CONTEXT_ENABLED",True): return None
    d=_side(direction)
    m15=closed_candles((by_tf or {}).get("M15") or [],15)
    h1=closed_candles((by_tf or {}).get("H1") or [],60)
    bars=m15 if len(m15)>=20 else h1
    if not d or len(bars)<12:return None
    av=atr(h1,14) if len(h1)>=15 else atr(bars,14)
    if not av:return None
    # For a reversal LONG we care about failed downside breaks (SSL); for SHORT, BSL.
    wanted="SSL" if d>0 else "BSL"
    pools=[p for p in liquidity_map.build_map(symbol,by_tf) if p.side==wanted]
    if not pools:return TurtleBreakoutContext(True,d,reason="нет значимого уровня для проверки")
    # Prefer mature/significant pools, then proximity. Rank prevents a tiny local pivot
    # from outranking PDH/PDL/HTF liquidity merely because it is a few ticks nearer.
    p=max(pools,key=lambda x:(x.rank,-x.distance_atr))
    level=float(p.level); tol=av*float(getattr(cfg,"TURTLE_LEVEL_TOLERANCE_ATR",.10))
    look=bars[-int(getattr(cfg,"TURTLE_MEMORY_LOOKBACK_BARS",32)):]
    natural=1 if wanted=="SSL" else -1
    def beyond(c): return c.close < level-tol if wanted=="SSL" else c.close > level+tol
    def reclaim(c): return (c.low<level and c.close>level+tol) if wanted=="SSL" else (c.high>level and c.close<level-tol)
    def test(c): return c.low<=level+tol if wanted=="SSL" else c.high>=level-tol
    attempts=sum(1 for c in look if test(c))
    reclaims=sum(1 for c in look if reclaim(c))
    first_touch=next((i for i,c in enumerate(look) if test(c)),len(look)-1)
    maturity=max(0,len(look)-1-first_touch)
    last=look[-1]; prev=look[-2]; prev2=look[-3]
    plus_one=bool(beyond(prev) and reclaim(last))
    immediate_failure=bool(reclaim(last))
    # Multi-bar failed breakout: a BODY/CLOSE genuinely escaped the level, but
    # price failed to build two-close acceptance and reclaimed within a short
    # closed-candle window. This catches traps that are slower than Plus One
    # without treating an old unrelated breakout as a current reversal.
    failure_window=max(2,int(getattr(cfg,"TURTLE_FAILED_BREAK_MAX_BARS",4)))
    recent_window=look[-(failure_window+1):]
    last_break_rel=None
    for j in range(len(recent_window)-1):
        if beyond(recent_window[j]):
            last_break_rel=j
    body_break_seen=last_break_rel is not None
    failure_bars=0
    delayed_failure=False
    if body_break_seen and reclaim(last):
        failure_bars=(len(recent_window)-1)-last_break_rel
        between=recent_window[last_break_rel+1:]
        # Two closes beyond the level mean acceptance, not a trap.
        max_run=run=0
        for x in recent_window[last_break_rel:]:
            run=run+1 if beyond(x) else 0
            max_run=max(max_run,run)
        delayed_failure=bool(2 <= failure_bars <= failure_window and max_run < 2)
    trapped=bool((plus_one or immediate_failure or delayed_failure) and attempts>=2)
    mature=maturity>=int(getattr(cfg,"TURTLE_LEVEL_MATURITY_BARS",8)) or p.rank>=4
    acceptance_closes=int(beyond(last))+int(beyond(prev))
    accepted=bool(acceptance_closes>=2)
    # Closed-candle quality of the trap: meaningful excursion through the level,
    # reclaim body, repeated interaction and level maturity. It is descriptive
    # context only and cannot create an independent family/signal.
    excursion=max(0.0,(level-last.low) if wanted=="SSL" else (last.high-level))
    break_depth_atr=excursion/av if av else 0.0
    reclaim_body_atr=abs(last.close-last.open)/av if av else 0.0
    q=45
    q += min(18, attempts*3)
    q += 10 if mature else 0
    q += min(12, int(round(break_depth_atr*20)))
    q += min(15, int(round(reclaim_body_atr*18))) if immediate_failure else 0
    q += 5 if plus_one else 0
    trap_quality=max(0,min(100,q))
    # Fast invalidation of a reversal thesis: two closed candles accepted beyond level.
    invalidated=accepted
    body=abs(last.close-last.open)
    displacement=body>=av*float(getattr(cfg,"TURTLE_REENTRY_MIN_BODY_ATR",.45))
    reentry_ready=bool(reclaims>=2 and reclaim(last) and displacement and not invalidated)

    # Wyckoff adaptation: after a Spring/Upthrust reclaim, require a later test
    # that revisits the level without a fresh accepted break. This is context,
    # not a new signal/family. The test is deliberately based on CLOSED bars.
    prior_reclaim_i = next((i for i in range(len(look)-2, max(-1,len(look)-8), -1) if reclaim(look[i])), None)
    wyckoff_test=False; wyckoff_test_confirmed=False
    if prior_reclaim_i is not None and prior_reclaim_i < len(look)-1:
        after=look[prior_reclaim_i+1:]
        wyckoff_test=any(test(x) for x in after)
        if wyckoff_test:
            # Successful test: current close remains back on the reclaimed side
            # and the test does not show two-close acceptance beyond the level.
            recent=after[-2:]
            accepted_after=sum(1 for x in recent if beyond(x))>=2
            held=(last.close>level+tol if wanted=="SSL" else last.close<level-tol)
            wyckoff_test_confirmed=bool(held and not accepted_after)

    # Brooks adaptation: a first continuation attempt after reclaim may fail,
    # but a second closed-candle attempt with displacement can restore quality.
    # It is intentionally stricter than merely counting two reclaims.
    first_attempt_failed=False; second_entry_ready=False
    if prior_reclaim_i is not None and prior_reclaim_i <= len(look)-3:
        post=look[prior_reclaim_i+1:]
        if len(post)>=2:
            first=post[0]
            first_dir=(first.close-first.open)*natural
            first_attempt_failed=bool(first_dir<=0 or abs(first.close-first.open)<av*.20)
            second_dir=(last.close-last.open)*natural
            second_entry_ready=bool(first_attempt_failed and wyckoff_test_confirmed and second_dir>0 and displacement and not invalidated)
    lifecycle_note=("Wyckoff test подтверждён" if wyckoff_test_confirmed else
                    "Wyckoff test наблюдается" if wyckoff_test else "")
    if second_entry_ready:
        lifecycle_note=(lifecycle_note+"; " if lifecycle_note else "")+"Brooks second entry подтверждён"

    if invalidated:
        state="ПРИНЯТИЕ ЦЕНЫ ЗА УРОВНЕМ"; align=-1 if d==natural else 1; score=28
        reason="два закрытия подтверждают acceptance; reversal-гипотеза отменена"
    elif second_entry_ready:
        state="ВТОРАЯ ПОПЫТКА ПОСЛЕ ЛОЖНОГО ПРОБОЯ"; align=1 if d==natural else -1; score=89
        reason="reclaim удержан на Wyckoff test; первая попытка ослабла, вторая подтверждена displacement"
    elif wyckoff_test_confirmed:
        state="SPRING/UPTHRUST — TEST ПОДТВЕРЖДЁН"; align=1 if d==natural else -1; score=87
        reason="после reclaim уровень повторно протестирован и удержан закрытой свечой"
    elif reentry_ready:
        state="ПОВТОРНЫЙ RECLAIM ПОДТВЕРЖДЁН"; align=1 if d==natural else -1; score=88
        reason="повторный reclaim + свежий displacement разрешают повторную оценку"
    elif delayed_failure:
        state="МНОГОСВЕЧНЫЙ ЛОЖНЫЙ ПРОБОЙ"; align=1 if d==natural else -1; score=86 if mature else 78
        reason=f"закрытие вышло за уровень, но acceptance не сформировался; возврат через {failure_bars} закрытые свечи"
    elif plus_one:
        state="TURTLE SOUP PLUS ONE"; align=1 if d==natural else -1; score=84 if mature else 76
        reason="после закрытия за уровнем следующая закрытая свеча вернулась обратно"
    elif trapped:
        state="ЛОВУШКА ПРОБОЯ"; align=1 if d==natural else -1; score=82 if mature else 74
        reason="повторный тест завершился reclaim; участники пробоя потенциально заперты"
    elif immediate_failure:
        state="ЛОЖНЫЙ ПРОБОЙ / RECLAIM"; align=1 if d==natural else -1; score=80 if mature else 72
        reason="уровень проколот, но закрытие вернулось обратно"
    elif beyond(last):
        state="ПРОБОЙ — ОЖИДАНИЕ ACCEPTANCE/FAILURE"; align=0; score=55
        reason="одного закрытия за уровнем недостаточно для вывода"
    else:
        state="ЗРЕЛЫЙ УРОВЕНЬ / ОЖИДАНИЕ" if mature else "УРОВЕНЬ / ОЖИДАНИЕ"; align=0; score=58 if mature else 52
        reason="контекст уровня сохранён; подтверждённого breakout lifecycle пока нет"
    ctx=TurtleBreakoutContext(True,d,state,level,p.source,p.timeframe,maturity,attempts,reclaims,plus_one,trapped,invalidated,reentry_ready,round(break_depth_atr,2),round(reclaim_body_atr,2),trap_quality,acceptance_closes,wyckoff_test,wyckoff_test_confirmed,first_attempt_failed,second_entry_ready,lifecycle_note,align,score,reason)
    return replace(ctx, delayed_failure=delayed_failure, failure_bars=failure_bars, body_break_seen=body_break_seen)

def score_delta(ctx,direction):
    if not ctx:return 0
    return 3 if ctx.alignment>0 else -4 if ctx.alignment<0 else 0

def describe(ctx):
    if not ctx:return "Пробой/ложный пробой: данных недостаточно"
    if not ctx.level:return f"Пробой/ложный пробой: {ctx.reason}"
    return (f"Пробой/ложный пробой: {ctx.state} · {ctx.source} {ctx.timeframe} · "
            f"тестов {ctx.attempts} · возвратов {ctx.reclaims} · зрелость {ctx.maturity_bars} бар · "
            f"качество ловушки {ctx.trap_quality}/100 · глубина {ctx.break_depth_atr:.2f} ATR" + (f" · возврат через {ctx.failure_bars} св." if ctx.delayed_failure else "") + (f" · {ctx.lifecycle_note}." if ctx.lifecycle_note else "."))
