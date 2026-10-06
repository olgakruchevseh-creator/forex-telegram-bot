import layer20_decision_readiness as l20

def test_mature_is_diagnostic_only():
    x=l20.assess("EUR/USD","LONG",{"state":"READY"},{"facts":[{},{}]},
                   {"edges":[{}]},{"integrity":"COHERENT","timestamp_freshness":"UNAVAILABLE"},
                   {"independence":"MIXED"})
    assert x["readiness"]=="MATURE"
    assert not x["trade_effect"] and not x["veto_effect"] and not x["telegram_effect"]

def test_conflict_never_becomes_trade_veto():
    x=l20.assess("EUR/USD","LONG",{}, {"facts":[{},{}]}, {},
                   {"integrity":"CONFLICTED"},{"independence":"CONCENTRATED"})
    assert x["readiness"]=="CONFLICTED" and x["threshold_effect"] is False
