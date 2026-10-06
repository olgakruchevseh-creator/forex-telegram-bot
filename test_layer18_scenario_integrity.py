import layer18_scenario_integrity as l18


def _graph(events, sides=None, states=None, opposed=None, conflicts=None):
    sides=sides or [1]*len(events); states=states or ["CONFIRMED"]*len(events)
    return {"state":"GRAPH_READY", "nodes":[
        {"id":f"n{i}","family":"STRUCTURE" if e=="MARKET_STRUCTURE" else "STRUCTURE_DELIVERY_SHIFT",
         "event":e,"side":sides[i],"timeframe":"H1","state":states[i]}
        for i,e in enumerate(events)],
        "canonical_sequence":events,
        "family_alignment":{"aligned":["STRUCTURE"],"opposed":opposed or [],"neutral":[]},
        "conflicts":conflicts or []}


def test_coherent_chain_is_observe_only():
    x=l18.assess("EUR/USD","LONG",_graph(["SWEEP_RECLAIM","CISD","CHOCH","MSS","MARKET_STRUCTURE"]),{}, {})
    assert x["integrity"] == "COHERENT"
    assert x["trade_effect"] is False and x["veto_effect"] is False
    assert x["probability_effect"] is False and x["direction_claim"] is False
    assert x["closed_h1_policy_preserved"] is True


def test_missing_bridge_is_partial_not_veto():
    x=l18.assess("EUR/USD","LONG",_graph(["SWEEP_RECLAIM","MSS","MARKET_STRUCTURE"]),{}, {})
    assert x["integrity"] == "PARTIAL"
    d=next(d for d in x["diagnostics"] if d["type"]=="MISSING_BRIDGE_STAGES")
    assert d["events"] == ["CISD","CHOCH"]
    assert x["veto_effect"] is False


def test_bad_lifecycle_is_conflicted():
    x=l18.assess("EUR/USD","LONG",_graph(["CHOCH","MSS"],states=["CONFLICT","CONFIRMED"]),{}, {})
    assert x["integrity"] == "CONFLICTED"
    assert "LIFECYCLE_DETERIORATION" in x["diagnostic_types"]


def test_opposite_context_and_side_discontinuity_are_diagnostics_only():
    g=_graph(["CHOCH","MSS"],sides=[1,-1],opposed=["LOCATION"])
    x=l18.assess("EUR/USD","LONG",g,{}, {})
    assert "SIDE_DISCONTINUITY" in x["diagnostic_types"]
    assert "OPPOSED_CONTEXT_FAMILIES" in x["diagnostic_types"]
    assert x["threshold_effect"] is False


def test_does_not_invent_timestamp_freshness():
    x=l18.assess("EUR/USD","LONG",_graph(["MARKET_STRUCTURE"]),{}, {})
    assert x["timestamp_freshness"] == "UNAVAILABLE_NO_EVENT_TIMESTAMPS"
    assert x["lifecycle_freshness_proxy_only"] is True


def test_out_of_order_sequence_is_detected():
    g=_graph(["MSS","CHOCH"])
    x=l18.assess("EUR/USD","LONG",g,{}, {})
    assert x["integrity"] == "CONFLICTED"
    assert "EVENT_ORDER_VIOLATION" in x["diagnostic_types"]
