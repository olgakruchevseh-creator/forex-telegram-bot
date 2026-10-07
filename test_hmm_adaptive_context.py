import hmm_adaptive_context as a

def _h():
    return {'status':'OK','state':'DIRECTIONAL','converged':True,
      'probabilities':{'QUIET':.1,'DIRECTIONAL':.8,'TURBULENT':.1},
      'transition_matrix':{s:{q:(.8 if s==q else .1) for q in a.STATES} for s in a.STATES}}

def _cal(n=240,T=1.2):
    return {'status':'OK','transitions':n,'temperature':T,'brier':.20,'calibrated_brier':.16,'ece':.12,'calibrated_ece':.08,
      'empirical_transition':{s:{q:(.7 if s==q else .15) for q in a.STATES} for s in a.STATES}}

def test_adaptive_is_direction_neutral_and_normalized():
    x=a.build(_h(),character={'trend_persistence':80,'impulse':70,'noise':20,'session_activity':70},regime='TREND',calibration=_cal())
    assert x.status=='OK' and x.live_effect=='NONE'
    assert abs(sum(x.calibrated_probabilities.values())-1)<1e-3
    assert all(abs(sum(r.values())-1)<1e-3 for r in x.adaptive_transition_matrix.values())

def test_small_sample_cannot_dominate_transition_matrix():
    x=a.build(_h(),calibration=_cal(12))
    assert x.calibration_weight < .1

def test_no_calibration_still_safe():
    x=a.build(_h(),calibration={'status':'INSUFFICIENT_DATA','transitions':0})
    assert x.status=='OK' and x.calibration_weight==0 and x.calibration_transitions==0
