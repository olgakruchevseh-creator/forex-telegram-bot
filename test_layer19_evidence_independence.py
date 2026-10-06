import layer19_evidence_independence as l19

def test_concentrated_family_is_visible_not_veto():
    st={"facts":[{"family":"STRUCTURE_DELIVERY_SHIFT","event":"CHOCH","side":1},
                 {"family":"STRUCTURE_DELIVERY_SHIFT","event":"MSS","side":1}]}
    x=l19.assess("EUR/USD","LONG",st,{}, {"integrity":"COHERENT"})
    assert x["independence"]=="CONCENTRATED"
    assert "CORRELATED_FAMILY_CONCENTRATION" in x["warnings"]
    assert x["trade_effect"] is False and x["veto_effect"] is False

def test_multiple_families_are_not_double_counted():
    st={"facts":[{"family":"STRUCTURE","event":"MARKET_STRUCTURE","side":1},
                 {"family":"LIQUIDITY","event":"SWEEP_RECLAIM","side":1},
                 {"family":"LOCATION","event":"PREMIUM_DISCOUNT","side":1}]}
    x=l19.assess("EUR/USD",1,st,{}, {"integrity":"COHERENT"})
    assert x["independence"]=="DIVERSE" and x["aligned_family_count"]==3
