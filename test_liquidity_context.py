import liquidity_context
from analysis import Candle

def bars(n=50,start=1.1000,step=.0002):
    out=[]
    for i in range(n):
        o=start+i*step; c=o+step*.6
        out.append(Candle(dt=str(1700000000+i*14400),open=o,high=o+.0005,low=o-.0004,close=c))
    return out

def test_shared_context_has_dealing_range_and_erl_route():
    ctx=liquidity_context.analyze_symbol('EUR/USD',{'H4':bars()},1,[])
    assert ctx is not None
    assert ctx.dealing_high>ctx.dealing_low
    assert 'ERL HIGH' in ctx.route
    assert ctx.residual_state in {'OPEN','LOW','EXHAUSTED'}

def test_exhausted_or_low_never_gets_positive_bonus():
    from dataclasses import replace
    ctx=liquidity_context.analyze_symbol('EUR/USD',{'H4':bars()},1,[])
    assert liquidity_context.score_delta(replace(ctx,residual_state='LOW')) < 0
    assert liquidity_context.score_delta(replace(ctx,residual_state='EXHAUSTED')) < 0
