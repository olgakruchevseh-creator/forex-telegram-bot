"""W1/D1/H4/H1 -> M15/M5 unified narrative. Context only."""
from dataclasses import dataclass, asdict
from analysis import closed_candles, analyze_tf
TF={'W1':10080,'D1':1440,'H4':240,'H1':60,'M15':15,'M5':5}
@dataclass(frozen=True)
class Narrative:
    direction:int; state:str; htf_aligned:int; trigger_aligned:int; conflicts:int; alignment:int; views:dict
    family:str='MULTI_TF_NARRATIVE'
    def as_dict(self):return asdict(self)
def analyze_symbol(symbol,by_tf,direction):
    d=1 if direction in (1,'LONG') else -1 if direction in (-1,'SHORT') else 0
    if not d:return None
    views={}
    for tf,mins in TF.items():
        b=closed_candles((by_tf or {}).get(tf) or [],mins)
        views[tf]=analyze_tf(tf,tf,b).bias if len(b)>=20 else 0
    htf=sum(views[x]==d for x in ('W1','D1','H4','H1')); trg=sum(views[x]==d for x in ('M15','M5')); conflicts=sum(views[x]==-d for x in TF)
    if htf>=3 and trg>=1: state='COHERENT'; a=1
    elif htf>=3 and trg==0: state='HTF_OK_TRIGGER_PENDING'; a=0
    elif htf<=1 and conflicts>=3: state='CONFLICT'; a=-1
    else: state='MIXED'; a=0
    return Narrative(d,state,htf,trg,conflicts,a,views)
def score_delta(ctx,direction):return 2 if ctx and ctx.alignment>0 else -3 if ctx and ctx.alignment<0 else 0
def describe(ctx):
    if not ctx:return 'Multi-TF Narrative: данных недостаточно'
    return f'Multi-TF Narrative: {ctx.state} · HTF {ctx.htf_aligned}/4 · trigger {ctx.trigger_aligned}/2'
