"""Layer 7: persistent structural-break health diagnostics (OBSERVE_ONLY).

Dependency-free guard for replay/edge health. It does not create/veto/rerank signals.
It adds persistence, hysteresis, recovery and cooldown semantics on top of causal
window degradation diagnostics so a single noisy window cannot flip system state.
"""
from __future__ import annotations
import math
from statistics import mean, pstdev


def _nums(values):
    out=[]
    for value in values or []:
        try:
            x=float(value)
            if math.isfinite(x): out.append(x)
        except (TypeError,ValueError): pass
    return out


def _window_score(history, current):
    if len(history)<8 or len(current)<3:
        return None
    mu=mean(history); sd=pstdev(history)
    if sd<=1e-12: return 0.0
    return (mean(current)-mu)/(sd/math.sqrt(len(current)))


def persistent_break_state(values, *, window=8, min_history=24, z_warn=-1.5,
                           z_break=-2.25, confirm_windows=2, recovery_windows=2,
                           cooldown_windows=1):
    """Classify persistent downside structural change using completed windows only.

    States: INSUFFICIENT_DATA, STABLE, WATCH, BREAK_CONFIRMED, RECOVERING, RECOVERED.
    BREAK_CONFIRMED requires consecutive bad windows. Recovery also requires consecutive
    healthy windows, providing hysteresis and preventing alert flapping.
    """
    x=_nums(values); window=max(3,int(window)); min_history=max(8,int(min_history))
    confirm_windows=max(1,int(confirm_windows)); recovery_windows=max(1,int(recovery_windows))
    need=min_history+window
    if len(x)<need:
        return {'status':'INSUFFICIENT_DATA','state':'INSUFFICIENT_DATA','n':len(x),'live_effect':'NONE'}
    baseline=x[:min_history]; chunks=[x[i:i+window] for i in range(min_history,len(x),window)]
    chunks=[c for c in chunks if len(c)==window]
    scores=[]; bad_run=healthy_run=0; confirmed_at=None; state='STABLE'; severity=0
    for idx,chunk in enumerate(chunks):
        z=_window_score(baseline,chunk); scores.append(round(z,3) if z is not None else None)
        if z is not None and z<=z_break:
            bad_run+=1; healthy_run=0
        elif z is not None and z>z_warn:
            healthy_run+=1; bad_run=0
        else:
            bad_run=0; healthy_run=0
        if confirmed_at is None:
            if bad_run>=confirm_windows:
                confirmed_at=idx; state='BREAK_CONFIRMED'
            elif z is not None and z<=z_warn:
                state='WATCH'
            else: state='STABLE'
        else:
            elapsed=idx-confirmed_at
            if elapsed<=max(0,int(cooldown_windows)):
                state='BREAK_CONFIRMED'
            elif healthy_run>=recovery_windows:
                state='RECOVERED'
            elif healthy_run>0:
                state='RECOVERING'
            elif z is not None and z<=z_warn:
                state='BREAK_CONFIRMED'
    recent=[s for s in scores[-confirm_windows:] if s is not None]
    if recent:
        worst=min(recent)
        severity=3 if worst<=z_break*1.75 else (2 if worst<=z_break else (1 if worst<=z_warn else 0))
    confidence=min(95,55+10*confirm_windows+5*severity) if state=='BREAK_CONFIRMED' else (75 if state in ('STABLE','RECOVERED') else 60)
    return {'status':'OK','state':state,'n':len(x),'window':window,'windows':len(chunks),
            'scores':scores,'confirmed_at_window':confirmed_at,'severity':severity,
            'confidence':confidence,'confirm_windows':confirm_windows,'recovery_windows':recovery_windows,
            'live_effect':'NONE'}


def detector_consensus(values, *, edge_alarm=False, adaptation_votes=0, **kwargs):
    """Compact consensus; persistence remains mandatory for a confirmed structural break."""
    p=persistent_break_state(values,**kwargs)
    if p.get('status')!='OK':
        return {'mode':'OBSERVE_ONLY','state':'INSUFFICIENT_DATA','confidence':0,'facts':[],
                'persistent':p,'live_effect':'NONE'}
    support=int(bool(edge_alarm))+min(3,max(0,int(adaptation_votes)))
    state=p['state']; conf=p['confidence']
    facts=[f"persistence={state}",f"severity={p['severity']}",f"independent_support={support}"]
    if state=='BREAK_CONFIRMED' and support>=1: conf=min(98,conf+5)
    elif state=='WATCH' and support>=2: conf=min(85,conf+10)
    return {'mode':'OBSERVE_ONLY','state':state,'confidence':conf,'support':support,
            'facts':facts,'persistent':p,'live_effect':'NONE'}
