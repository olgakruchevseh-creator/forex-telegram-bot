from datetime import datetime, timedelta, timezone

from analysis import Candle
import market_regime
import market_direction_regime
import weekly_bias_context
import strategy_book_policy


def _bars(n, start, step, tf_minutes, start_dt):
    out=[]
    px=float(start)
    for i in range(n):
        op=px
        cl=px+step
        wiggle=max(abs(step)*0.45, 0.0004)
        out.append(Candle((start_dt+timedelta(minutes=tf_minutes*i)).strftime('%Y-%m-%d %H:%M:%S'),
                          op, max(op,cl)+wiggle, min(op,cl)-wiggle, cl))
        px=cl
    return out


def _range_bars(n, center, amp, tf_minutes, start_dt):
    out=[]
    prev=center
    for i in range(n):
        cl=center + (amp if i%2==0 else -amp)
        op=prev
        out.append(Candle((start_dt+timedelta(minutes=tf_minutes*i)).strftime('%Y-%m-%d %H:%M:%S'),
                          op, max(op,cl)+amp*.4, min(op,cl)-amp*.4, cl))
        prev=cl
    return out


def _market():
    base=datetime(2025,1,6,tzinfo=timezone.utc)
    return {
        'W1': _bars(48, 0.50, 0.0016, 10080, base),
        'D1': _bars(120, 0.54, 0.00035, 1440, datetime(2026,5,1,tzinfo=timezone.utc)),
        'H4': _bars(180, 0.56, 0.00012, 240, datetime(2026,8,1,tzinfo=timezone.utc)),
        'H1': _bars(180, 0.565, 0.00004, 60, datetime(2026,10,10,tzinfo=timezone.utc)),
    }


def test_market_regime_has_causal_balance_layer():
    by_tf=_market()
    ctx=market_regime.analyze_symbol('NZD/USD',by_tf)
    assert ctx is not None
    assert ctx.balance_geometry is not None
    assert ctx.balance_geometry['causal'] is True
    assert ctx.balance_state in {'DIRECTIONAL','TRANSITION','BALANCE_FORMING','RANGE'}
    assert 0 <= ctx.balance_score <= 100


def test_adaptive_direction_uses_h4_parent_and_no_signal_ownership():
    ctx=market_direction_regime.analyze_symbol('NZD/USD',_market(),{'NZD':0.2,'USD':-0.1})
    assert ctx['main_direction'] == 'LONG'
    assert ctx['working_h1_direction'] == 'LONG'
    assert ctx['elder_hierarchy'] == 'H4_PARENT_H1_WORKING_LEG'
    assert ctx['can_create_signal'] is False
    assert ctx['targets_owner'] == 'EXISTING_PRICE_PATH_OR_SOURCE_MODULE'
    assert ctx['auction']['fake_volume_used'] is False


def test_weekly_bias_is_separate_soft_context_with_refs():
    ctx=weekly_bias_context.analyze_symbol('NZD/USD',_market(),events=[],as_of=datetime(2026,10,27,tzinfo=timezone.utc))
    assert ctx['available'] is True
    assert ctx['direction'] == 'LONG'
    assert ctx['separate_from_weekly_rhythm'] is True
    assert ctx['hard_veto'] is False
    assert ctx['probability_claim'] is False
    assert ctx['reference_levels']['is_price_path_target'] is False


def test_strategy_books_are_one_cycle_not_four_votes():
    direction=market_direction_regime.analyze_symbol('NZD/USD',_market(),{})
    weekly=weekly_bias_context.analyze_symbol('NZD/USD',_market(),events=[],as_of=datetime(2026,10,27,tzinfo=timezone.utc))
    alert='Пара: NZD/USD\nНаправление: ЛОНГ\nЦЕПНОЙ ВХОД №1\nBOS подтверждён'
    ctx=strategy_book_policy.assess_candidate('NZD/USD','LONG',[alert],direction,weekly_bias=weekly)
    assert ctx['primary_family'] == 'STRUCTURE_BREAKOUT'
    assert ctx['master_cycle']['sequence'] == ('DONNELLY','DALTON','ELDER','BROOKS')
    assert ctx['independent_vote_count'] == 0
    assert ctx['master_cycle']['independent_vote_count'] == 0
    assert ctx['hard_block'] is False
    assert -5 <= ctx['quality_delta'] <= 3


def test_range_geometry_does_not_become_false_trend_vote():
    by_tf=_market()
    by_tf['H1']=_range_bars(160,0.5700,0.0008,60,datetime(2026,10,15,tzinfo=timezone.utc))
    r=market_regime.analyze_symbol('NZD/USD',by_tf)
    assert r is not None
    assert r.balance_score >= 45
    # The new geometry is diagnostic; it never owns a directional trade vote.
    d=market_direction_regime.analyze_symbol('NZD/USD',by_tf,{})
    assert d['can_create_signal'] is False
