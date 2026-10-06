import layer17_market_state_graph as l17


def _structured():
    return {"facts": [
        {"family":"LIQUIDITY","event":"SWEEP_RECLAIM","side":1,"timeframe":"H1","state":"RECLAIMED"},
        {"family":"STRUCTURE_DELIVERY_SHIFT","event":"CHOCH","side":1,"timeframe":"H1","state":"CONFIRMED"},
        {"family":"STRUCTURE_DELIVERY_SHIFT","event":"MSS","side":1,"timeframe":"H1","state":"CONFIRMED"},
        {"family":"STRUCTURE","event":"MARKET_STRUCTURE","side":1,"timeframe":"H4","state":"CONFIRMED"},
        {"family":"REGIME","event":"MARKET_REGIME","side":0,"timeframe":"H1","state":"TREND"},
    ], "event_sequence":["SWEEP_RECLAIM","CHOCH","MSS","MARKET_STRUCTURE"]}


def test_graph_is_observe_only_and_preserves_policy():
    x=l17.assess("EUR/USD","LONG",_structured(),{"state":"TRACKING"})
    assert x["state"] == "GRAPH_READY"
    assert x["trade_effect"] is False and x["direction_claim"] is False
    assert x["threshold_effect"] is False and x["probability_effect"] is False
    assert x["closed_h1_policy_preserved"] is True
    assert x["m15_m5_confirmation_only"] is True


def test_graph_links_ordered_same_side_events():
    x=l17.assess("EUR/USD","LONG",_structured(),{})
    assert len(x["edges"]) >= 2
    assert all(e["relation"] == "PRECEDES_SUPPORTS" for e in x["edges"])
    assert "LIQUIDITY" in x["family_alignment"]["aligned"]
    assert "REGIME" in x["family_alignment"]["neutral"]


def test_opposite_family_is_context_not_veto():
    s=_structured(); s["facts"].append({"family":"LOCATION","event":"PREMIUM_DISCOUNT","side":-1,"timeframe":"H4","state":"PREMIUM"})
    x=l17.assess("EUR/USD","LONG",s,{})
    assert "LOCATION" in x["family_alignment"]["opposed"]
    assert x["veto_effect"] is False


def test_internal_family_conflict_is_single_diagnostic_record():
    s=_structured(); s["facts"].append({"family":"STRUCTURE","event":"MARKET_STRUCTURE","side":-1,"timeframe":"H1","state":"THREATENED"})
    x=l17.assess("EUR/USD","LONG",s,{})
    assert len([c for c in x["conflicts"] if c["family"] == "STRUCTURE"]) == 1
