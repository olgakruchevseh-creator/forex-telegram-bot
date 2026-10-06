"""Layer 26 — Mathematical Coherence & Cross-Estimator Agreement (OBSERVE_ONLY).

Cross-checks the independent mathematical views created by Layers 21–25.  The
purpose is to detect a deceptively strong aggregate when its component
estimators disagree.  It never changes live trading behaviour.
"""
from __future__ import annotations
import math
import config as cfg


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def _pct(d, key, default=0.0):
    if not isinstance(d, dict): return default
    try: return _clip(float(d.get(key, default) or 0.0) / 100.0)
    except (TypeError, ValueError): return default


def _rms_dispersion(values):
    if len(values) < 2: return 0.0
    mean=sum(values)/len(values)
    return math.sqrt(sum((x-mean)**2 for x in values)/len(values))


def assess(pair: str, mathematical_core: dict|None, mathematical_stability: dict|None,
           mathematical_confidence: dict|None, mathematical_resilience: dict|None,
           information_value: dict|None) -> dict:
    if not getattr(cfg, 'LAYER26_MATHEMATICAL_COHERENCE_ENABLED', True):
        return {'layer':26,'observe_only':True,'state':'DISABLED','trade_effect':False}

    core=mathematical_core if isinstance(mathematical_core,dict) else {}
    stab=mathematical_stability if isinstance(mathematical_stability,dict) else {}
    conf=mathematical_confidence if isinstance(mathematical_confidence,dict) else {}
    res=mathematical_resilience if isinstance(mathematical_resilience,dict) else {}
    info=information_value if isinstance(information_value,dict) else {}

    quality=_pct(core,'quality_coordinate')
    stability=_pct(stab,'stability_score')
    confidence=_pct(conf,'mathematical_confidence')
    resilience=_pct(res,'mathematical_resilience')
    information=_pct(info,'information_value')
    scores=[quality,stability,confidence,resilience,information]
    dispersion=_rms_dispersion(scores)

    # Two estimators arrive at directional support differently: Layer 21 uses
    # structural/calibration shrinkage; Layer 23 uses family-effective Beta shrinkage.
    shadow=_pct(core,'shadow_probability',0.5)
    posterior=_pct(conf,'smoothed_directional_support',0.5)
    estimator_gap=abs(shadow-posterior)

    reliability=_pct(core,'reliability')
    info_suff=_pct(info,'information_sufficiency')
    contradiction=_pct(info,'contradiction_index',1.0)
    data_readiness=0.55*reliability + 0.45*info_suff

    max_gap=max(0.05,float(getattr(cfg,'LAYER26_MAX_ESTIMATOR_GAP_PCT',18.0))/100.0)
    max_disp=max(0.05,float(getattr(cfg,'LAYER26_MAX_SCORE_DISPERSION_PCT',16.0))/100.0)
    estimator_agreement=_clip(1.0-estimator_gap/max_gap)
    score_agreement=_clip(1.0-dispersion/max_disp)
    contradiction_control=_clip(1.0-contradiction)
    coherence=_clip(0.34*estimator_agreement + 0.28*score_agreement +
                    0.22*data_readiness + 0.16*contradiction_control)

    no_data=(str(core.get('state')) in {'NO_FACTS','DISABLED'} or str(info.get('state'))=='NO_FACTS')
    if no_data:
        state='NO_FACTS'
    elif estimator_gap > max_gap or dispersion > max_disp:
        state='INCOHERENT'
    elif coherence >= 0.72 and data_readiness >= 0.55:
        state='COHERENT'
    else:
        state='PARTIAL_COHERENCE'

    return {'layer':26,'observe_only':True,'state':state,'pair':pair,
            'cross_estimator_gap':round(estimator_gap*100,2),
            'score_dispersion':round(dispersion*100,2),
            'estimator_agreement':round(estimator_agreement*100,2),
            'score_agreement':round(score_agreement*100,2),
            'data_readiness':round(data_readiness*100,2),
            'mathematical_coherence':round(coherence*100,2),
            'shadow_probability':round(shadow*100,2),
            'smoothed_directional_support':round(posterior*100,2),
            'closed_h1_policy_preserved':True,'m15_m5_confirmation_only':True,
            'statistical_guarantee':False,'trade_effect':False,'direction_claim':False,
            'threshold_effect':False,'probability_effect':False,'veto_effect':False,
            'telegram_effect':False,
            'basis':'CROSS_ESTIMATOR_AGREEMENT_SCORE_DISPERSION_DATA_READINESS'}
