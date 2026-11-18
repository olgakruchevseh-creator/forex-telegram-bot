"""Historical analog context from mature Decision Journal records. No synthetic history."""
from dataclasses import dataclass, asdict
from contextlib import contextmanager
from contextvars import ContextVar
import json
import decision_journal
from journal_tail import tail_jsonl_fields
@dataclass(frozen=True)
class SetupAnalog:
    available:bool=False; samples:int=0; favorable:int=0; unfavorable:int=0; effectiveness:float|None=None; alignment:int=0; reason:str=''
    family:str='SETUP_MEMORY'
    def as_dict(self):return asdict(self)
_FROZEN_REPLAY = ContextVar("setup_memory_frozen_replay", default=None)

@contextmanager
def frozen_replay(snapshot):
    """Frozen journal-derived observation; never read today's journal in replay."""
    token=_FROZEN_REPLAY.set({"active":True,"snapshot":snapshot})
    try:
        yield
    finally:
        _FROZEN_REPLAY.reset(token)


def analyze(symbol,direction,regime='',session='',min_samples=5):
    replay=_FROZEN_REPLAY.get()
    if replay is not None:
        raw=replay.get("snapshot")
        if isinstance(raw,dict):
            try:return SetupAnalog(**{k:v for k,v in raw.items() if k in SetupAnalog.__dataclass_fields__})
            except (TypeError,ValueError):pass
        return SetupAnalog(reason='replay_journal_snapshot_missing')
    path=decision_journal._path()
    if not path.exists():return SetupAnalog(reason='journal_missing')
    side='LONG' if direction in (1,'LONG') else 'SHORT'
    rows=[]
    try:
        for r in tail_jsonl_fields(path, 4000, (
                'pair', 'side', 'status', 'session', 'market_state',
                'mfe_atr', 'mae_atr', 'replay')):
            if r.get('pair')!=symbol or r.get('side')!=side or r.get('status') not in ('SENT','REPLAY'):continue
            ms=r.get('market_state') or {}; rn=((ms.get('regime') or {}).get('name') if isinstance(ms.get('regime'),dict) else '')
            if regime and rn and rn!=regime:continue
            if session and r.get('session') and r.get('session')!=session:continue
            mfe=r.get('mfe_atr'); mae=r.get('mae_atr')
            if mfe is None:
                replay=r.get('replay') or {}; mfe=replay.get('mfe_atr'); mae=replay.get('mae_atr')
            if mfe is not None:rows.append((float(mfe),float(mae or 0)))
    except Exception:return SetupAnalog(reason='journal_read_error')
    if len(rows)<min_samples:return SetupAnalog(False,len(rows),reason='insufficient_analogs')
    fav=sum(m>=1.0 and m>=a for m,a in rows); bad=sum(a>m and a>=1.0 for m,a in rows); eff=round(fav/len(rows)*100,1)
    align=1 if eff>=65 else -1 if eff<=35 else 0
    return SetupAnalog(True,len(rows),fav,bad,eff,align,'mature_journal_analogs')
def score_delta(ctx):return 2 if ctx and ctx.alignment>0 else -2 if ctx and ctx.alignment<0 else 0
def describe(ctx):
    if not ctx or not ctx.available:return f'Setup Memory: недостаточно истории ({getattr(ctx,"samples",0) if ctx else 0})'
    return f'Setup Memory: похожих {ctx.samples} · историческая эффективность {ctx.effectiveness:.0f}%'
