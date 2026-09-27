"""Historical analog context from mature Decision Journal records. No synthetic history."""
from dataclasses import dataclass, asdict
import json
import decision_journal
@dataclass(frozen=True)
class SetupAnalog:
    available:bool=False; samples:int=0; favorable:int=0; unfavorable:int=0; effectiveness:float|None=None; alignment:int=0; reason:str=''
    family:str='SETUP_MEMORY'
    def as_dict(self):return asdict(self)
def analyze(symbol,direction,regime='',session='',min_samples=5):
    path=decision_journal._path()
    if not path.exists():return SetupAnalog(reason='journal_missing')
    side='LONG' if direction in (1,'LONG') else 'SHORT'
    rows=[]
    try:
        for line in path.read_text(encoding='utf-8').splitlines()[-4000:]:
            try:r=json.loads(line)
            except Exception:continue
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
