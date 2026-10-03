from types import SimpleNamespace
from unittest.mock import patch
import turtle_breakout_context as tb
import pattern_failure_context as pf
import correlated_context_score as cs

class C:
    def __init__(self,o,h,l,c,dt='2026-10-03 10:00:00'):
        self.open=o; self.high=h; self.low=l; self.close=c; self.dt=dt

def test_new_turtle_fields_are_context_only():
    fields=tb.TurtleBreakoutContext.__dataclass_fields__
    assert 'wyckoff_test_confirmed' in fields
    assert 'second_entry_ready' in fields
    assert tb.TurtleBreakoutContext().family == 'TURTLE_BREAKOUT_CONTEXT'

def test_pattern_failure_is_correlated_not_family():
    x=pf.PatternFailureContext(True,'ПРОВАЛ ПАТТЕРНА ПОДТВЕРЖДЁН','Голова и плечи','SHORT','LONG','H1',1.1,2,True,1,88,'x')
    assert x.family == 'CORRELATED_PRICE_ACTION_CONTEXT'
    assert pf.score_delta(x,1) == 2

def test_correlated_score_collapses_pattern_failure_bonus(monkeypatch):
    inside=SimpleNamespace(confirmed=False,state='',direction=1)
    auction=SimpleNamespace(state='',alignment=0)
    turtle=SimpleNamespace(state='TURTLE SOUP PLUS ONE',alignment=1)
    failure=SimpleNamespace(state='ПРОВАЛ ПАТТЕРНА ПОДТВЕРЖДЁН',alignment=1)
    monkeypatch.setattr(cs.inside_bar_context,'score_delta',lambda *a:0)
    monkeypatch.setattr(cs.auction_context,'score_delta',lambda *a:0)
    monkeypatch.setattr(cs.turtle_breakout_context,'score_delta',lambda *a:3)
    monkeypatch.setattr(cs.pattern_failure_context,'score_delta',lambda *a:2)
    total,meta=cs.score(inside,auction,turtle,1,failure)
    assert total == 3
    assert meta['duplicate_collapsed'] is True
