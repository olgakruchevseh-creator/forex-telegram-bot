"""Layer 38 — Coalition Robustness / Subset-Lattice Audit (OBSERVE_ONLY).

Extends Layers 36–37 from leave-one/leave-two diagnostics to the full subset
lattice of the nine primary mathematical coordinates. It asks whether support is
broadly preserved across many independent coordinate coalitions or exists only
when nearly the whole stack is present. No live trading effect.
"""
from __future__ import annotations
from itertools import combinations
import math
import config as cfg


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def _pct(d, key, default=0.0):
    if not isinstance(d, dict):
        return default
    try:
        return _clip(float(d.get(key, default) or 0.0) / 100.0)
    except (TypeError, ValueError):
        return default


def _aggregate(values):
    vals = list(values)
    if not vals:
        return 0.0
    harmonic = len(vals) / sum(1.0 / max(v, 1e-6) for v in vals)
    mean = sum(vals) / len(vals)
    return _clip(.55 * harmonic + .30 * mean + .15 * min(vals))


def assess(pair: str, mathematical_core: dict|None, mathematical_stability: dict|None,
           mathematical_confidence: dict|None, mathematical_resilience: dict|None,
           information_value: dict|None, mathematical_coherence: dict|None,
           uncertainty_budget: dict|None, decision_margin: dict|None,
           support_geometry: dict|None) -> dict:
    if not getattr(cfg, 'LAYER38_COALITION_ROBUSTNESS_ENABLED', True):
        return {'layer': 38, 'observe_only': True, 'state': 'DISABLED', 'trade_effect': False}

    core = mathematical_core if isinstance(mathematical_core, dict) else {}
    required = (core, information_value or {}, uncertainty_budget or {}, decision_margin or {}, support_geometry or {})
    if (str(core.get('state')) in {'NO_FACTS', 'DISABLED'} or
            any(str(x.get('state')) in {'NO_FACTS', 'DISABLED'} for x in required[1:] if isinstance(x, dict))):
        return {'layer': 38, 'observe_only': True, 'state': 'NO_FACTS', 'pair': pair,
                'coalition_robustness': 0.0, 'trade_effect': False, 'direction_claim': False,
                'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
                'telegram_effect': False, 'closed_h1_policy_preserved': True,
                'm15_m5_confirmation_only': True, 'statistical_guarantee': False,
                'basis': 'DETERMINISTIC_SUBSET_LATTICE_AUDIT'}

    x = {
        'core': _pct(core, 'quality_coordinate'),
        'stability': _pct(mathematical_stability, 'stability_score'),
        'confidence': _pct(mathematical_confidence, 'mathematical_confidence'),
        'resilience': _pct(mathematical_resilience, 'mathematical_resilience'),
        'information': _pct(information_value, 'information_value'),
        'coherence': _pct(mathematical_coherence, 'mathematical_coherence'),
        'certainty': 1.0 - _pct(uncertainty_budget, 'uncertainty_budget', 1.0),
        'margin': _pct(decision_margin, 'decision_margin'),
        'geometry': _pct(support_geometry, 'support_geometry'),
    }
    keys = tuple(x)
    baseline = _aggregate(x.values())
    min_size = int(getattr(cfg, 'LAYER38_MIN_COALITION_SIZE', 4))
    min_size = max(3, min(len(keys), min_size))
    preserve_ratio = _clip(float(getattr(cfg, 'LAYER38_PRESERVE_RATIO', .88)), .70, .98)
    threshold = baseline * preserve_ratio

    by_size = {}
    all_scores = []
    preserved_total = 0
    total = 0
    weakest_name, weakest_score = None, 1.0
    for size in range(min_size, len(keys) + 1):
        scores = []
        kept = 0
        for combo in combinations(keys, size):
            score = _aggregate(x[k] for k in combo)
            scores.append(score); all_scores.append(score); total += 1
            if score >= threshold:
                kept += 1; preserved_total += 1
            if score < weakest_score:
                weakest_score = score; weakest_name = '+'.join(combo)
        by_size[str(size)] = {
            'count': len(scores),
            'mean_score': round((sum(scores)/len(scores))*100, 2),
            'worst_score': round(min(scores)*100, 2),
            'preserved_pct': round((kept/len(scores))*100, 2),
        }

    mean = sum(all_scores)/len(all_scores) if all_scores else baseline
    dispersion = math.sqrt(sum((s-mean)**2 for s in all_scores)/len(all_scores)) if all_scores else 0.0
    preserve_fraction = preserved_total / total if total else 1.0
    worst_retention = _clip(weakest_score / max(baseline, 1e-6))
    mean_retention = _clip(mean / max(baseline, 1e-6))
    dispersion_quality = _clip(1.0 - dispersion / max(baseline * .25, .04))
    breadth = _clip(.42*preserve_fraction + .28*worst_retention + .20*mean_retention + .10*dispersion_quality)
    # Absolute quality prevents a uniformly weak stack from looking robust merely
    # because every weak coalition agrees with every other weak coalition.
    robustness = _clip(.78*breadth + .22*baseline)

    warn = _clip(float(getattr(cfg, 'LAYER38_WARNING_ROBUSTNESS_PCT', 72.0))/100.0, .55, .90)
    strong = _clip(float(getattr(cfg, 'LAYER38_STRONG_ROBUSTNESS_PCT', 86.0))/100.0, warn, .97)
    min_preserved = _clip(float(getattr(cfg, 'LAYER38_MIN_PRESERVED_COALITIONS_PCT', 70.0))/100.0, .45, .95)
    if robustness < warn or preserve_fraction < min_preserved*.75:
        state = 'COALITION_FRAGILITY'
    elif robustness < strong or preserve_fraction < min_preserved:
        state = 'COALITION_WARNING'
    else:
        state = 'COALITION_ROBUST'

    return {'layer': 38, 'observe_only': True, 'state': state, 'pair': pair,
            'coalition_robustness': round(robustness*100, 2),
            'baseline_score': round(baseline*100, 2),
            'coalition_count': total, 'min_coalition_size': min_size,
            'preserved_coalitions_pct': round(preserve_fraction*100, 2),
            'mean_coalition_score': round(mean*100, 2),
            'worst_coalition_score': round(weakest_score*100, 2),
            'worst_coalition_retention_pct': round(worst_retention*100, 2),
            'coalition_dispersion_pct': round(dispersion*100, 2),
            'weakest_coalition': weakest_name, 'by_size': by_size,
            'component_scores': {k: round(v*100, 2) for k,v in x.items()},
            'meta_layers_counted_as_independent_support': False,
            'closed_h1_policy_preserved': True, 'm15_m5_confirmation_only': True,
            'statistical_guarantee': False, 'trade_effect': False, 'direction_claim': False,
            'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
            'telegram_effect': False, 'basis': 'DETERMINISTIC_SUBSET_LATTICE_AUDIT'}
