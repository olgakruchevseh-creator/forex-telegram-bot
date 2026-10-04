from types import SimpleNamespace
import liquidity_map

C=lambda o,h,l,c,dt: SimpleNamespace(open=o,high=h,low=l,close=c,dt=dt)

def test_pool_has_hierarchy_and_post_event_fields():
    f=liquidity_map.LiquidityPool.__dataclass_fields__
    assert "hierarchy" in f and "post_event" in f

def test_fvg_is_not_liquidity_pool_source_by_design():
    # Raw stop-pool builder must not manufacture FVG as BSL/SSL.
    src=liquidity_map.__file__
    text=open(src,encoding="utf-8").read()
    raw=text[text.index("def _raw_pools"):text.index("def _significant_sr_zones")]
    assert '"FVG"' not in raw and "'FVG'" not in raw

def test_post_event_vocabulary_is_explicit():
    text=open(liquidity_map.__file__,encoding="utf-8").read()
    assert "SWEEP_RECLAIM" in text
    assert "BREAK_ACCEPTED" in text
    assert "BREAK_RETESTED" in text
