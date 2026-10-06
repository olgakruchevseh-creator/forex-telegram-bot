"""Layer 45 — Final Mathematical Consensus (OBSERVE_ONLY).

Terminal diagnostic for the mathematical stack. It compresses the primary
mathematical coordinates plus Layer 44 estimator agreement into a conservative
integrity score. It never creates direction, changes thresholds, vetoes trades,
or sends Telegram messages.
"""
from __future__ import annotations
import math
import statistics
import config as cfg


def _clip(x, lo=0.0, hi=1.0):
    try: return max(lo, min(hi, float(x)))
    except (TypeError, ValueError): return lo


def _pct(d, key, default=0.0):
    if not isinstance(d, dict): return default
    try: return _clip(float(d.get(key, default) or 0.0) / 100.0)
    except (TypeError, ValueError): return default


def _contract(state, pair, **extra):
    out = {'layer':45,'observe_only':True,'state':state,'pair':pair,
           'trade_effect':False,'direction_claim':False,'threshold_effect':False,
           'probability_effect':False,'veto_effect':False,'telegram_effect':False,
           'closed_h1_policy_preserved':True,'m15_m5_confirmation_only':True,
           'statistical_guarantee':False,
           'basis':'FINAL_MATHEMATICAL_CONSENSUS_INTEGRITY_AUDIT'}
    out.update(extra); return out


def assess(pair: str, mathematical_core: dict|None, mathematical_stability: dict|None,
           mathematical_confidence: dict|None, mathematical_resilience: dict|None,
           information_value: dict|None, mathematical_coherence: dict|None,
           uncertainty_budget: dict|None, decision_margin: dict|None,
           support_geometry: dict|None, aggregator_agreement: dict|None) -> dict:
    if not getattr(cfg, 'LAYER45_FINAL_CONSENSUS_ENABLED', True):
        return _contract('DISABLED', pair)

    core = mathematical_core if isinstance(mathematical_core, dict) else {}
    sources = [core, information_value, uncertainty_budget, decision_margin,
               support_geometry, aggregator_agreement]
    if str(core.get('state')) in {'NO_FACTS','DISABLED'} or any(
        not isinstance(z, dict) or str(z.get('state')) in {'NO_FACTS','DISABLED'} for z in sources[1:]
    ):
        return _contract('NO_FACTS', pair, final_consensus=0.0,
                         contradiction_penalty_pct=0.0)

    x = {
        'core': _pct(core,'quality_coordinate'),
        'stability': _pct(mathematical_stability,'stability_score'),
        'confidence': _pct(mathematical_confidence,'mathematical_confidence'),
        'resilience': _pct(mathematical_resilience,'mathematical_resilience'),
        'information': _pct(information_value,'information_value'),
        'coherence': _pct(mathematical_coherence,'mathematical_coherence'),
        'certainty': 1.0-_pct(uncertainty_budget,'uncertainty_budget',1.0),
        'margin': _pct(decision_margin,'decision_margin'),
        'geometry': _pct(support_geometry,'support_geometry'),
    }
    vals=list(x.values())
    eps=1e-9
    # Conservative center: harmonic mean punishes a weak coordinate without
    # allowing a high coordinate to compensate linearly.
    harmonic=len(vals)/sum(1.0/max(v,eps) for v in vals)
    median=statistics.median(vals)
    floor=min(vals)
    agreement=_pct(aggregator_agreement,'aggregator_agreement')

    spread=max(vals)-min(vals)
    center=statistics.median(vals)
    mad=statistics.median(abs(v-center) for v in vals)
    spread_warn=_clip(float(getattr(cfg,'LAYER45_MAX_COORDINATE_SPREAD_PCT',32.0))/100.0,.12,.60)
    mad_warn=_clip(float(getattr(cfg,'LAYER45_MAX_COORDINATE_MAD_PCT',12.0))/100.0,.04,.30)
    contradiction=max(spread/max(spread_warn,eps)-1.0, mad/max(mad_warn,eps)-1.0, 0.0)
    penalty=_clip(contradiction*float(getattr(cfg,'LAYER45_CONTRADICTION_PENALTY',0.18)),0.0,.35)

    raw=.38*harmonic+.22*median+.16*floor+.24*agreement
    final=_clip(raw-penalty)
    warn=_clip(float(getattr(cfg,'LAYER45_CONSENSUS_WARN_PCT',68.0))/100.0,.45,.90)
    strong=_clip(float(getattr(cfg,'LAYER45_CONSENSUS_STRONG_PCT',82.0))/100.0,warn,.97)

    if contradiction >= 1.0 or agreement < warn*.75:
        state='FINAL_CONSENSUS_CONFLICT'
    elif final < warn:
        state='FINAL_CONSENSUS_WEAK'
    elif final >= strong and agreement >= warn:
        state='FINAL_CONSENSUS_STRONG'
    else:
        state='FINAL_CONSENSUS_STABLE'

    return _contract(state,pair,final_consensus=round(final*100,2),
        conservative_center_pct=round(harmonic*100,2), median_pct=round(median*100,2),
        weakest_coordinate_pct=round(floor*100,2), aggregator_agreement_pct=round(agreement*100,2),
        coordinate_spread_pct=round(spread*100,2), coordinate_mad_pct=round(mad*100,2),
        contradiction_penalty_pct=round(penalty*100,2),
        component_scores={k:round(v*100,2) for k,v in x.items()},
        terminal_mathematical_layer=True, independent_support_claim=False,
        meta_layers_counted_as_independent_support=False)
