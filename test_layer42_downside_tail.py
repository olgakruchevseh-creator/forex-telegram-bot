import layer42_downside_tail as m


def d(v, key, state='OK'):
    return {'state':state, key:v}


def run(vals):
    return m.assess('EUR/USD', d(vals[0], 'quality_coordinate'),
        d(vals[1], 'stability_score'), d(vals[2], 'mathematical_confidence'),
        d(vals[3], 'mathematical_resilience'), d(vals[4], 'information_value'),
        d(vals[5], 'mathematical_coherence'), d(100-vals[6], 'uncertainty_budget'),
        d(vals[7], 'decision_margin'), d(vals[8], 'support_geometry'))


def test_layer42_observe_only_contract():
    r = run([70,72,74,76,78,80,82,84,86])
    assert r['layer'] == 42 and r['observe_only'] is True
    for k in ('trade_effect','direction_claim','threshold_effect','probability_effect','veto_effect','telegram_effect'):
        assert r[k] is False
    assert r['closed_h1_policy_preserved'] and r['m15_m5_confirmation_only']


def test_layer42_clustered_weak_tail_is_detected():
    healthy = run([70,72,74,76,78,80,82,84,86])
    weak = run([20,28,35,76,78,80,82,84,86])
    assert weak['state'] == 'DOWNSIDE_TAIL_FRAGILE'
    assert weak['downside_tail_health'] < healthy['downside_tail_health']
    assert weak['tail_gap_pct'] > healthy['tail_gap_pct']


def test_layer42_isolated_low_is_less_bad_than_cluster():
    isolated = run([30,72,74,76,78,80,82,84,86])
    clustered = run([30,38,44,76,78,80,82,84,86])
    assert isolated['tail_mean_pct'] > clustered['tail_mean_pct']
    assert isolated['downside_tail_health'] > clustered['downside_tail_health']


def test_layer42_no_facts_safe():
    r = m.assess('EUR/USD', {'state':'NO_FACTS'}, {}, {}, {}, {}, {}, {}, {}, {})
    assert r['state'] == 'NO_FACTS' and r['trade_effect'] is False
