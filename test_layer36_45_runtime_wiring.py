"""Regression guard: terminal mathematical diagnostics must stay in live context.

The test is deliberately structural: Layers 36-45 are OBSERVE_ONLY and must be
called from signal_context without changing verdict/delivery semantics.
"""
from pathlib import Path


def test_terminal_math_layers_are_imported_and_wired():
    source = Path('signal_context.py').read_text(encoding='utf-8')
    modules = {
        36: ('layer36_component_influence', 'component_influence'),
        37: ('layer37_pairwise_interaction', 'pairwise_interaction'),
        38: ('layer38_coalition_robustness', 'coalition_robustness'),
        39: ('layer39_marginal_attribution', 'marginal_attribution'),
        40: ('layer40_nonlinear_redundancy', 'nonlinear_redundancy'),
        41: ('layer41_residual_information', 'residual_information'),
        42: ('layer42_downside_tail', 'downside_tail'),
        43: ('layer43_asymmetry', 'asymmetry'),
        44: ('layer44_aggregator_agreement', 'aggregator_agreement'),
        45: ('layer45_final_mathematical_consensus', 'final_mathematical_consensus'),
    }
    for _layer, (module, key) in modules.items():
        assert f'import {module}' in source
        assert (f'("{key}", {module},' in source) or (f'ctx["{key}"]' in source)
    assert 'ctx.get("aggregator_agreement")' in source


def test_terminal_math_remains_observe_only_before_verdict():
    source = Path('signal_context.py').read_text(encoding='utf-8')
    terminal = source.index('# Layers 36-44: terminal mathematical diagnostics')
    verdict = source.index('ok, reason = verdict(ctx)', terminal)
    assert terminal < verdict
    segment = source[terminal:verdict]
    assert 'verdict(' not in segment
    assert 'dropped.append' not in segment
