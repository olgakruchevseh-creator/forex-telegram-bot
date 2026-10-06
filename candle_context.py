"""Контекст свечей по принципам Нисона — не самостоятельная стратегия.

Слой объединяет семь согласованных идей: качество свечного контекста,
wick/sweep/reclaim, failure паттерна, multi-candle balance shift,
Three-Line-Break principle, Kagi/ATR meaningful reversal и disparity/extension.
Он никогда не создаёт LONG/SHORT событие и не считается независимым семейством.
"""
from __future__ import annotations
from dataclasses import dataclass
import config as cfg
from analysis import atr, closed_candles, analyze_tf

_TF_MIN={"W1":10080,"D1":1440,"H4":240,"H1":60,"M15":15,"M5":5}

@dataclass(frozen=True)
class CandleContext:
    direction:int
    score:int
    delta:int
    wick_reclaim:bool
    pattern_failure:bool
    balance_shift:bool
    three_line_break:bool
    meaningful_reversal:bool
    extended:bool
    facts:tuple[str,...]
    double_trap_state:str="NONE"
    double_trap_direction:int=0

def _rng(c): return max(float(c.high)-float(c.low),1e-12)
def _body(c): return abs(float(c.close)-float(c.open))
def _dir(c): return 1 if c.close>c.open else (-1 if c.close<c.open else 0)

def analyze_symbol(symbol:str, by_tf:dict, direction:int, source_tf:str="H1") -> CandleContext:
    direction=1 if direction>0 else -1
    tf=source_tf if source_tf in _TF_MIN else "H1"
    b=closed_candles((by_tf or {}).get(tf) or [],_TF_MIN[tf])
    if len(b)<8:
        return CandleContext(direction,50,0,False,False,False,False,False,False,("недостаточно закрытых свечей",),"NONE",0)
    c,p=b[-1],b[-2]; av=atr(b,14) or _rng(c); facts=[]; raw=0

    # 1/2. Candle Context + Wick/Sweep/Reclaim: прокол предыдущего экстремума
    # ценен только когда закрытие вернулось внутрь и поддерживает направление.
    lower=min(c.open,c.close)-c.low; upper=c.high-max(c.open,c.close)
    wick_reclaim=(direction>0 and c.low<p.low and c.close>p.low and lower>=_body(c)*1.25) or \
                 (direction<0 and c.high>p.high and c.close<p.high and upper>=_body(c)*1.25)
    if wick_reclaim: raw+=12; facts.append("тень сняла экстремум и цена вернулась закрытием")

    # 3. Failure: сильная предыдущая свеча противоположного направления не получила
    # продолжения и её тело поглощено/сломано закрытием. Это контекст, не сигнал.
    pattern_failure=(_dir(p)==-direction and _body(p)>=av*.35 and
        ((direction>0 and c.close>max(p.open,p.close)) or (direction<0 and c.close<min(p.open,p.close))))
    if pattern_failure: raw+=8; facts.append("предыдущая противоположная свечная идея не получила продолжения")

    # 4. Multi-candle balance shift: последние 3 закрытия и суммарные тела показывают
    # устойчивую передачу контроля, а не одиночную свечу.
    last=b[-4:]; signed=sum((_dir(x)*_body(x)) for x in last)/max(av,1e-12)
    closes=[x.close for x in last]
    monotonic=(all(closes[i]>=closes[i-1] for i in range(1,len(closes))) if direction>0
               else all(closes[i]<=closes[i-1] for i in range(1,len(closes))))
    balance_shift=(signed*direction>=.85 and monotonic)
    if balance_shift: raw+=10; facts.append("баланс нескольких свечей устойчиво сместился по направлению")

    # 5. Three-Line-Break principle: значимое закрытие за диапазоном трёх предыдущих
    # закрытий. Используем принцип подтверждения, не отдельный тип графика.
    prior=b[-4:-1]
    three_line_break=(c.close>max(x.close for x in prior) if direction>0 else c.close<min(x.close for x in prior)) and _body(c)>=av*.30
    if three_line_break: raw+=9; facts.append("закрытие преодолело диапазон трёх предыдущих закрытий")

    # 6. Kagi/ATR meaningful reversal: разворот считается значимым лишь после хода
    # достаточного относительно ATR от недавнего экстремума.
    recent=b[-7:-1]
    reversal_move=(c.close-min(x.low for x in recent) if direction>0 else max(x.high for x in recent)-c.close)
    meaningful_reversal=reversal_move>=av*.80 and _dir(c)==direction
    if meaningful_reversal: raw+=7; facts.append("разворотный ход значим относительно ATR")

    # 7. Disparity/Extension: чрезмерное удаление от локального равновесия не запрещает
    # направление, но ухудшает качество нового входа.
    mean=sum(float(x.close) for x in b[-8:-1])/7.0
    extension=(float(c.close)-mean)*direction/max(av,1e-12)
    extended=extension>=1.65
    if extended: raw-=10; facts.append("цена чрезмерно растянута от локального равновесия")

    # Outside Double Trap lifecycle. An outside H1 that takes BOTH sides is not
    # directional by itself. Only the next CLOSED candle can resolve it. This
    # prevents a two-sided liquidity event from being counted as an instant signal.
    double_trap_state="NONE"; double_trap_direction=0
    prev2=b[-3]; outside=p.high>prev2.high and p.low<prev2.low
    meaningful_outside=getattr(cfg,"OUTSIDE_DOUBLE_TRAP_ENABLED",True) and outside and _rng(p)>=av*float(getattr(cfg,"OUTSIDE_DOUBLE_TRAP_MIN_RANGE_ATR",.85))
    if meaningful_outside:
        if c.close>p.high and _body(c)>=av*float(getattr(cfg,"OUTSIDE_DOUBLE_TRAP_CONFIRM_BODY_ATR",.20)):
            double_trap_state="RESOLVED"; double_trap_direction=1
        elif c.close<p.low and _body(c)>=av*float(getattr(cfg,"OUTSIDE_DOUBLE_TRAP_CONFIRM_BODY_ATR",.20)):
            double_trap_state="RESOLVED"; double_trap_direction=-1
        elif c.high<=p.high and c.low>=p.low:
            double_trap_state="PENDING"
        else:
            double_trap_state="INVALIDATED"
        if double_trap_state=="RESOLVED":
            if double_trap_direction==direction:
                raw+=6; facts.append("двухсторонняя ловушка разрешилась закрытием по направлению")
            else:
                raw-=6; facts.append("двухсторонняя ловушка разрешилась против направления")
        elif double_trap_state=="PENDING":
            facts.append("двухсторонняя ловушка пока нейтральна — ждёт закрытого подтверждения")

    # If the CURRENT closed candle is itself the outside trap, expose it only as
    # PENDING. There is deliberately no score change until another H1 closes.
    current_outside=getattr(cfg,"OUTSIDE_DOUBLE_TRAP_ENABLED",True) and c.high>p.high and c.low<p.low and _rng(c)>=av*float(getattr(cfg,"OUTSIDE_DOUBLE_TRAP_MIN_RANGE_ATR",.85))
    if current_outside:
        double_trap_state="PENDING"; double_trap_direction=0
        facts.append("текущая H1 сняла обе стороны — направление ещё не подтверждено")

    # HTF context: свечные факты сильнее, когда не спорят с D1/H4/H1.
    votes=[]
    for htf in ("D1","H4","H1"):
        xs=closed_candles((by_tf or {}).get(htf) or [],_TF_MIN[htf])
        if len(xs)>=20:
            v=analyze_tf(htf,htf,xs).bias
            if v: votes.append(v)
    aligned=sum(v==direction for v in votes); opposite=sum(v==-direction for v in votes)
    raw += min(8,aligned*3)-min(8,opposite*4)
    score=max(0,min(100,50+raw))
    delta=max(-6,min(6,round((score-50)/8)))
    return CandleContext(direction,score,delta,wick_reclaim,pattern_failure,balance_shift,three_line_break,meaningful_reversal,extended,tuple(facts) or ("нейтральный свечной контекст",),double_trap_state,double_trap_direction)

def score_delta(ctx:CandleContext|None, direction:int)->int:
    return int(ctx.delta) if ctx and ctx.direction==(1 if direction>0 else -1) else 0

def describe(ctx:CandleContext|None)->str:
    if not ctx: return "Свечной контекст: нет данных"
    state="поддерживает" if ctx.delta>0 else ("противоречит" if ctx.delta<0 else "нейтрален")
    return f"Свечной контекст: {state} · {ctx.score}/100 · " + "; ".join(ctx.facts[:3])
