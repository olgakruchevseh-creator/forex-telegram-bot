from types import SimpleNamespace as NS
import ict_liquidity_route as r

def patch(monkeypatch,pd=1,idm=1,res='OPEN',struct=1,state='CONFIRMED'):
    monkeypatch.setattr(r.premium_discount,'analyze_symbol',lambda *a: NS(alignment=pd,position='DISCOUNT'))
    monkeypatch.setattr(r.idm,'analyze_symbol',lambda *a: NS(alignment=idm))
    monkeypatch.setattr(r.liquidity_context,'analyze_symbol',lambda *a: NS(residual_state=res,erl_target=1.2))
    monkeypatch.setattr(r.structure_context,'analyze_symbol',lambda *a: NS(state=state))
    monkeypatch.setattr(r.structure_context,'alignment',lambda *a: struct)

def test_route_confirmed_only_when_sequence_is_coherent(monkeypatch):
    patch(monkeypatch)
    x=r.analyze_symbol('EUR/USD',{},1,[])
    assert x.ready and x.state=='ROUTE_CONFIRMED' and r.score_delta(x)==2

def test_touch_or_location_alone_is_not_confirmation(monkeypatch):
    patch(monkeypatch,idm=0,struct=0,state='FORMING')
    x=r.analyze_symbol('EUR/USD',{},1,[])
    assert not x.ready and x.alignment==0

def test_unswept_idm_or_exhausted_erl_is_conflict(monkeypatch):
    patch(monkeypatch,idm=-1,res='EXHAUSTED')
    x=r.analyze_symbol('EUR/USD',{},1,[])
    assert not x.ready and x.state=='ROUTE_CONFLICT' and r.score_delta(x)==-3
