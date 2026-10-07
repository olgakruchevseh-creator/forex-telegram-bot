import hmm_calibration as c


def _row(sym,t,state,p,A=None):
    if A is None:
        A={s:{q:(.8 if s==q else .1) for q in c.STATES} for s in c.STATES}
    return {'symbol':sym,'closed_h1':f'2026-10-{t//24+1:02d}T{t%24:02d}:00:00','hmm':{'state':state,'probabilities':p,'transition_matrix':A}}

def test_next_distribution_normalized():
    r=_row('EUR/USD',0,'QUIET',{'QUIET':.8,'DIRECTIONAL':.1,'TURBULENT':.1})
    q=c._next_distribution(r)
    assert abs(sum(q.values())-1)<1e-9 and all(v>=0 for v in q.values())

def test_calibration_report_and_shrinkage():
    rows=[]
    states=list(c.STATES)
    for i in range(160):
        s=states[(i//20)%3]
        p={q:(.86 if q==s else .07) for q in c.STATES}
        rows.append(_row('EUR/USD',i,s,p))
    r=c.calibrate_rows(rows,min_rows=100,prior=2)
    assert r.status=='OK' and r.transitions==159
    assert .5<=r.temperature<=2.0
    assert all(abs(sum(row.values())-1)<1e-3 for row in r.empirical_transition.values())
    assert r.live_effect=='NONE'

def test_symbols_not_cross_connected():
    rows=[]
    for sym in ('EUR/USD','USD/JPY'):
        for i in range(4): rows.append(_row(sym,i,'QUIET',{'QUIET':.8,'DIRECTIONAL':.1,'TURBULENT':.1}))
    assert c.calibrate_rows(rows,min_rows=1).transitions==6
