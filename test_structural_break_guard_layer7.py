from structural_break_guard import persistent_break_state, detector_consensus

def test_single_bad_window_does_not_confirm_break():
    x=[1,-1]*12 + [-4]*8 + [1,-1]*8
    r=persistent_break_state(x,window=8,min_history=24,confirm_windows=2)
    assert r['state'] != 'BREAK_CONFIRMED'

def test_persistent_drop_confirms_break():
    x=[1,-1]*12 + [-4]*16
    r=persistent_break_state(x,window=8,min_history=24,confirm_windows=2)
    assert r['state']=='BREAK_CONFIRMED'
    assert r['severity']>=2

def test_recovery_requires_persistence():
    x=[1,-1]*12 + [-4]*16 + [1,-1]*4
    r=persistent_break_state(x,window=8,min_history=24,confirm_windows=2,recovery_windows=2,cooldown_windows=0)
    assert r['state']=='RECOVERING'
    x += [1,-1]*4
    r=persistent_break_state(x,window=8,min_history=24,confirm_windows=2,recovery_windows=2,cooldown_windows=0)
    assert r['state']=='RECOVERED'

def test_consensus_never_has_live_effect():
    x=[1,-1]*12 + [-4]*16
    r=detector_consensus(x,edge_alarm=True,adaptation_votes=2,window=8,min_history=24)
    assert r['live_effect']=='NONE'
    assert r['state']=='BREAK_CONFIRMED'
