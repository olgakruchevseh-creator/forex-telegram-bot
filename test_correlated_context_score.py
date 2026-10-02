from types import SimpleNamespace
import correlated_context_score as c

def test_false_break_positive_bonus_is_counted_once():
    inside=SimpleNamespace(confirmed=True,state='FALSE_BREAK',direction=1)
    auction=SimpleNamespace(state='REJECTION_RECLAIM',alignment=1)
    turtle=SimpleNamespace(state='TURTLE SOUP PLUS ONE',alignment=1)
    total,meta=c.score(inside,auction,turtle,1)
    assert meta['duplicate_collapsed'] is True
    assert meta['parts']=={'inside_bar':2,'auction':2,'turtle':3}
    assert total==3

def test_negative_contradictions_are_not_softened():
    inside=SimpleNamespace(confirmed=True,state='FALSE_BREAK',direction=-1)
    auction=SimpleNamespace(state='REJECTION_RECLAIM',alignment=-1)
    turtle=SimpleNamespace(state='TURTLE SOUP PLUS ONE',alignment=-1)
    total,meta=c.score(inside,auction,turtle,1)
    assert meta['duplicate_collapsed'] is False
    assert total==-8

def test_unrelated_positive_contexts_are_not_collapsed():
    inside=SimpleNamespace(confirmed=True,state='BREAKOUT_CONFIRMED',direction=1)
    auction=SimpleNamespace(state='ACCEPTANCE_CONTINUATION',alignment=1)
    turtle=SimpleNamespace(state='ЗРЕЛЫЙ УРОВЕНЬ / ОЖИДАНИЕ',alignment=0)
    total,meta=c.score(inside,auction,turtle,1)
    assert meta['duplicate_collapsed'] is False
    assert total==4
