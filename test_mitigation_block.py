from mitigation_block import MitigationContext, overlaps, annotate_event

def test_overlap_is_context_not_new_family():
    c=MitigationContext(True,True,'LONG','H1',1.10,1.11,1.12,1.09,'a','b','SWEEP_RECLAIM',2)
    assert overlaps(c,1.105,1.115)
    assert c.family=='supply_demand'

def test_no_overlap():
    c=MitigationContext(True,True,'SHORT','H1',1.10,1.11,1.09,1.12,'a','b','RECOVERY_CLOSURE',2)
    assert not overlaps(c,1.12,1.13)
