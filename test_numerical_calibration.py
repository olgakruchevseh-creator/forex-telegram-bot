import decision_journal
import numerical_parameter_registry as registry

def test_registry_is_read_only_snapshot():
    snap=registry.snapshot()
    assert snap['MASTER_MIN_QUALITY'] == 78
    assert snap['KILLER_SCORE_THRESHOLD'] == 84
    assert snap['KILLER_MIN_FAMILIES'] == 5

def test_killer_numbers_are_parsed_for_calibration():
    text='Killer Score: 89/100\nНезависимые семейства: 6 · Structure'
    m=decision_journal._calibration_metrics(text,{},1,{})
    assert m['killer_score']==89
    assert m['killer_family_count']==6
