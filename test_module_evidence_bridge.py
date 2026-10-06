import module_evidence_bus
import layer16_structured_intelligence as l16


def test_bus_deduplicates_family_event_tf_and_keeps_rich_fields():
    module_evidence_bus.clear()
    module_evidence_bus.publish('ORDER_BLOCK', {'symbol':'EUR/USD','side':'LONG','tf':'H1','quality':88,'confidence':84,'zone_low':1.1,'zone_high':1.101})
    module_evidence_bus.publish('ORDER_BLOCK', {'symbol':'EUR/USD','side':'LONG','tf':'H1','quality':90,'confidence':86,'zone_low':1.1,'zone_high':1.101})
    x=module_evidence_bus.snapshot('EUR/USD',1)
    assert len(x)==1 and x[0]['family']=='ZONE' and x[0]['quality']==90


def test_layer16_consumes_bridge_without_trade_effect(monkeypatch):
    module_evidence_bus.clear()
    module_evidence_bus.publish('FIB_SMC', {'symbol':'GBP/USD','side':'SHORT','tf':'H1','quality':91,'confidence':88,'h4_bias':-1})
    monkeypatch.setattr(l16.structure_context,'analyze_symbol',lambda *a,**k: None)
    monkeypatch.setattr(l16.liquidity_context,'analyze_symbol',lambda *a,**k: None)
    monkeypatch.setattr(l16.premium_discount,'analyze_symbol',lambda *a,**k: None)
    monkeypatch.setattr(l16.market_regime,'analyze_symbol',lambda *a,**k: None)
    for m in (l16.cisd,l16.choch,l16.mss): monkeypatch.setattr(m,'analyze_symbol',lambda *a,**k: None)
    x=l16.assess('GBP/USD','SHORT',{})
    f=next(f for f in x['facts'] if f['event']=='FIB_SMC')
    assert f['family']=='LOCATION' and f['quality']==91 and f['side']==-1
    assert x['trade_effect'] is False and x['threshold_effect'] is False
