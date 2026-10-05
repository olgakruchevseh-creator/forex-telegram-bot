import robustness_audit as r


def _row(i, value, pair='EUR/USD', source='Patterns', regime='TREND'):
    # value = MFE - MAE proxy
    mfe=max(value,0)+0.5; mae=max(-value,0)+0.5
    return {'decision_id':str(i),'evaluated_utc':f'2026-09-{(i%28)+1:02d}T10:00:00+00:00','status':'SENT','pair':pair,'source':source,'regime':regime,
            'horizons_h1':{'3':{'mfe_atr':mfe,'mae_atr':mae}}}

def test_metrics_are_passive_proxy():
    m=r._metrics([_row(1,1),_row(2,-.5),_row(3,2)])
    assert m['n']==3 and m['expectancy_proxy_atr']>0 and m['profit_factor_proxy']>1

def test_monte_carlo_is_deterministic():
    rows=[_row(i,1 if i%3 else -.5) for i in range(30)]
    assert r._monte_carlo(rows,200)==r._monte_carlo(rows,200)

def test_rolling_oos_waits_for_enough_data():
    assert r._rolling_oos([_row(i,.2) for i in range(10)])['status']=='INSUFFICIENT_DATA'

def test_build_never_claims_live_effect():
    out=r.build([_row(i,.4) for i in range(35)])
    assert out['mode']=='OBSERVE_ONLY' and out['live_effect']=='NONE'
    assert 'by_pair' in out and out['rolling_oos']['status']=='OK'

def test_cost_stress_reduces_expectancy():
    rows=[_row(i,.25) for i in range(30)]
    s=r._cost_stress(rows)['scenarios']
    assert s[0]['expectancy_proxy_atr'] > s[-1]['expectancy_proxy_atr']

def test_dual_oos_requires_both_sections():
    rows=[_row(i,.3) for i in range(40)]
    out=r._dual_oos(rows)
    assert out['status']=='OK' and out['both_positive'] is True

def test_psr_and_minimum_track_record_are_passive():
    rows=[_row(i,.4 if i%5 else -.1) for i in range(60)]
    assert r._probabilistic_sharpe(rows)['status']=='OK'
    assert r._minimum_track_record(rows)['status']=='OK'

def test_embargoed_blocks_cover_oos_without_live_effect():
    rows=[_row(i,.2 if i%4 else -.1) for i in range(50)]
    out=r._embargoed_blocks(rows,5,1)
    assert out['status']=='OK' and len(out['blocks'])==5
    report=r.build(rows)
    assert report['live_effect']=='NONE' and report['schema']==2
    assert 'quality_by_source' in report
