import continuation_liquidity_context as clc


def test_context_is_not_signal_source():
    assert not hasattr(clc, "process_market")
    assert not hasattr(clc, "format_message")


def test_four_forms_are_one_context_not_families():
    fields=clc.ContinuationLiquidityContext.__dataclass_fields__
    assert "formations" in fields
    assert "families" not in fields


def test_score_is_bounded_context_bonus():
    c=clc.ContinuationLiquidityContext(1,"H1",True,True,("RANGE","IDM","EQUAL_HIGHS_LOWS","TRENDLINE_STRUCTURE"),1.2,True,True,True,True,True,"READY")
    assert 0 < clc.score_delta(c,1) <= 4
    assert clc.score_delta(c,-1) == 0


def test_lifecycle_requires_every_confirmation():
    c=clc.ContinuationLiquidityContext(1,"H1",True,True,("RANGE",),1.2,True,False,True,True,False,"PD_ARRAY_REACTION")
    assert not c.ready
    assert "PD_ARRAY" in c.stage
