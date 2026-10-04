"""Иерархический W1/D1/H4/H1 -> M15/M5 контекст.

Контекстный слой: никогда не создаёт самостоятельный LONG/SHORT сигнал.
Старшие ТФ отвечают за направление/структуру, H1 за рабочее состояние,
M15/M5 только за подтверждение тайминга. Краткий LTF-откат не имеет права
самостоятельно переворачивать HTF-сценарий.
"""
from dataclasses import dataclass, asdict, field
from analysis import closed_candles, analyze_tf

TF={'W1':10080,'D1':1440,'H4':240,'H1':60,'M15':15,'M5':5}
WEIGHTS={'W1':5,'D1':4,'H4':3,'H1':2,'M15':1,'M5':1}

@dataclass(frozen=True)
class Narrative:
    direction:int; state:str; htf_aligned:int; trigger_aligned:int; conflicts:int; alignment:int; views:dict
    family:str='MULTI_TF_NARRATIVE'
    weighted_score:int=0
    htf_score:int=0
    trigger_score:int=0
    htf_opposite:int=0
    trigger_opposite:int=0
    cascade_conflict:bool=False
    pullback_only:bool=False
    role_map:dict=field(default_factory=dict)
    def as_dict(self):return asdict(self)

def _view(by_tf, tf, mins):
    b=closed_candles((by_tf or {}).get(tf) or [],mins)
    return analyze_tf(tf,tf,b).bias if len(b)>=20 else 0

def analyze_symbol(symbol,by_tf,direction):
    d=1 if direction in (1,'LONG') else -1 if direction in (-1,'SHORT') else 0
    if not d:return None
    views={tf:_view(by_tf,tf,mins) for tf,mins in TF.items()}
    htf_names=('W1','D1','H4','H1'); trigger_names=('M15','M5')
    htf=sum(views[x]==d for x in htf_names)
    trg=sum(views[x]==d for x in trigger_names)
    htf_opp=sum(views[x]==-d for x in htf_names)
    trg_opp=sum(views[x]==-d for x in trigger_names)
    conflicts=htf_opp+trg_opp

    # Иерархия важнее простого количества голосов: W1/D1 не равны M5.
    weighted=sum(WEIGHTS[tf] * (1 if views[tf]==d else -1 if views[tf]==-d else 0) for tf in TF)
    htf_score=sum(WEIGHTS[tf] * (1 if views[tf]==d else -1 if views[tf]==-d else 0) for tf in htf_names)
    trigger_score=sum(WEIGHTS[tf] * (1 if views[tf]==d else -1 if views[tf]==-d else 0) for tf in trigger_names)

    # Каскадный конфликт требует противоположного H4/H1 и подтверждения M15/M5.
    # Один M5/M15 против старшего сценария = откат/тайминг, не разворот.
    cascade=views['H4']==-d and views['H1']==-d and trg_opp>=1
    pullback=htf>=3 and trg==0 and trg_opp>=1 and not cascade

    if cascade or (views['D1']==-d and views['H4']==-d and views['H1']==-d):
        state='CASCADE_CONFLICT'; alignment=-1
    elif htf>=3 and trg>=1 and htf_score>0:
        state='COHERENT'; alignment=1
    elif htf>=3 and trg==0 and htf_score>0:
        state='HTF_PULLBACK' if pullback else 'HTF_OK_TRIGGER_PENDING'; alignment=0
    elif htf_score>=5 and weighted>0:
        state='HTF_DOMINANT_MIXED'; alignment=0
    elif htf<=1 and htf_opp>=2:
        state='CONFLICT'; alignment=-1
    else:
        state='MIXED'; alignment=0

    roles={
        'W1_D1':'фон/старшее направление',
        'H4':'структура/рабочий сценарий',
        'H1':'рабочее подтверждение',
        'M15_M5':'триггер/тайминг, не самостоятельное направление',
    }
    return Narrative(d,state,htf,trg,conflicts,alignment,views,
                     weighted_score=weighted, htf_score=htf_score, trigger_score=trigger_score,
                     htf_opposite=htf_opp, trigger_opposite=trg_opp,
                     cascade_conflict=cascade, pullback_only=pullback, role_map=roles)

def score_delta(ctx,direction):
    if not ctx:return 0
    if ctx.state=='COHERENT':return 3
    if ctx.state=='CASCADE_CONFLICT':return -5
    if ctx.state=='CONFLICT':return -4
    if ctx.state=='HTF_PULLBACK':return 0
    if ctx.state=='HTF_DOMINANT_MIXED':return 1
    return 0

def describe(ctx):
    if not ctx:return 'Мульти-ТФ контекст: данных недостаточно'
    names={
        'COHERENT':'СОГЛАСОВАН',
        'HTF_PULLBACK':'СТАРШИЙ СЦЕНАРИЙ, МЛАДШИЙ ОТКАТ',
        'HTF_OK_TRIGGER_PENDING':'СТАРШИЙ СЦЕНАРИЙ, ТРИГГЕР НЕ ПОДТВЕРЖДЁН',
        'HTF_DOMINANT_MIXED':'СТАРШИЕ ТФ ДОМИНИРУЮТ, КОНТЕКСТ СМЕШАННЫЙ',
        'CASCADE_CONFLICT':'КАСКАДНЫЙ КОНФЛИКТ',
        'CONFLICT':'КОНФЛИКТ',
        'MIXED':'СМЕШАННЫЙ',
    }
    return (f"Мульти-ТФ контекст: {names.get(ctx.state,ctx.state)} · "
            f"W1/D1/H4/H1 {ctx.htf_aligned}/4 · M15/M5 {ctx.trigger_aligned}/2 · "
            f"взвешенный баланс {ctx.weighted_score:+d}")
