import layer16_structured_intelligence as l16


def test_layer16_contract_never_trades(monkeypatch):
    monkeypatch.setattr(l16.structure_context,'analyze_symbol',lambda *a,**k: None)
    monkeypatch.setattr(l16.liquidity_context,'analyze_symbol',lambda *a,**k: None)
    monkeypatch.setattr(l16.choch,'analyze_symbol',lambda *a,**k: None)
    monkeypatch.setattr(l16.mss,'analyze_symbol',lambda *a,**k: None)
    monkeypatch.setattr(l16.cisd,'analyze_symbol',lambda *a,**k: None)
    monkeypatch.setattr(l16.premium_discount,'analyze_symbol',lambda *a,**k: None)
    monkeypatch.setattr(l16.market_regime,'analyze_symbol',lambda *a,**k: None)
    x=l16.assess('EUR/USD','LONG',{})
    assert x['observe_only'] is True
    assert x['trade_effect'] is False
    assert x['threshold_effect'] is False
    assert x['direction_claim'] is False
    assert x['basis']=='DIRECT_EXPERT_STATES_NOT_TEXT_PARSING'


def test_layer16_sequence_is_causal_and_deduplicated(monkeypatch):
    class X: pass
    st=X(); st.side=1; st.timeframe='H1'; st.state='SHIFT_CONFIRMED'; st.sequence='LL → LH → HL → HH'; st.hierarchy_state='SWING_ONLY'; st.swing_side=1; st.swing_timeframe='H1'; st.substructure_side=0; st.substructure_timeframe=''
    monkeypatch.setattr(l16.structure_context,'analyze_symbol',lambda *a,**k: st)
    monkeypatch.setattr(l16.liquidity_context,'analyze_symbol',lambda *a,**k: None)
    def shift(event):
        x=X(); x.side=1; x.timeframe='H1'; x.level=1.1; x.displacement_atr=.9; x.alignment=1; return x
    monkeypatch.setattr(l16.cisd,'analyze_symbol',lambda *a,**k: shift('CISD'))
    monkeypatch.setattr(l16.choch,'analyze_symbol',lambda *a,**k: shift('CHOCH'))
    monkeypatch.setattr(l16.mss,'analyze_symbol',lambda *a,**k: shift('MSS'))
    monkeypatch.setattr(l16.premium_discount,'analyze_symbol',lambda *a,**k: None)
    monkeypatch.setattr(l16.market_regime,'analyze_symbol',lambda *a,**k: None)
    x=l16.assess('EUR/USD','LONG',{})
    assert x['event_sequence']==['CISD','CHOCH','MSS','MARKET_STRUCTURE']
    assert all(f['source']=='DIRECT_MODULE_STATE' for f in x['facts'])
