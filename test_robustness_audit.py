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
