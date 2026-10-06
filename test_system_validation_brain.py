import system_validation_brain as s

def rows(n=40):
    out=[]
    for i in range(n):
        good=i%4!=0
        out.append({'probability':80 if good else 55,'source':'Patterns' if i%2 else 'Liquidity Sweep',
          'efficiency_3h':.7 if good else .25,'targets_hit_8h':{'TR1':good},
          'timing_class':'timely' if good else 'weak_or_no_followthrough',
          'horizons_h1':{'3':{'mfe_atr':1.1 if good else .2,'mae_atr':.25 if good else .8}}})
    return out

def test_calibration_and_walk_forward_are_passive_and_numeric():
    r=rows(); c=s.calibration_reliability(r); w=s.walk_forward(r)
    assert c['n']==40 and 0<=c['brier']<=1 and 0<=c['ece']<=1
    assert w['n']==40 and 'cpcv' in w

def test_attribution_and_failure():
    r=rows(); a=s.attribution(r); f=s.failure_engine(r)
    assert 'Patterns' in a and 'Liquidity Sweep' in a
    assert f['classes']['NO_FOLLOWTHROUGH']==10

def test_correlated_vote_detection():
    j=[{'confirmation_families':{'source_count':3,'correlated_source_count':1,'by_family':{'structure':['A','B'],'levels':['C']}}}]
    x=s.correlated_voting(j); assert x['decisions_with_correlated_votes']==1 and x['repeated_families']['structure']==1
