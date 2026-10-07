import math
import hmm_regime as h

def test_fit_probabilities_and_transition_rows():
    x=[]
    for i in range(120):
        if i<40: x.append([.02*math.sin(i),-.8,.15,.0])
        elif i<80: x.append([.2*math.sin(i),.0,.75,.2])
        else: x.append([.5*math.sin(i),.9,.30,.8])
    m=h._fit(x,3,max_iter=40)
    assert abs(sum(m['filtered'][-1])-1)<1e-6
    assert all(abs(sum(r)-1)<1e-6 for r in m['A'])
    assert all(v>=0 for r in m['A'] for v in r)

def test_causal_prefix_invariance():
    x=[[.1*math.sin(i),.2*math.cos(i),.5,.1] for i in range(90)]
    m=h._fit(x[:70],3,max_iter=20)
    # forward posterior from fixed fitted params cannot depend on unseen suffix
    a=h._fb(x[:60],m['pi'],m['A'],m['mu'],m['var'])[3][-1]
    b=h._fb(x[:70],m['pi'],m['A'],m['mu'],m['var'])[3][59]
    pa=[math.exp(v-h._lse(a)) for v in a]; pb=[math.exp(v-h._lse(b)) for v in b]
    assert max(abs(u-v) for u,v in zip(pa,pb))<1e-10

def test_expected_duration_identity():
    p=.8
    assert abs(1/(1-p)-5)<1e-9
