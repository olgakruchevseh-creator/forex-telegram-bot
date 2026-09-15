import config as cfg
import signal_navigator as nav


def test_gate_defaults_are_conservative_but_do_not_require_large_candle_count_only():
    assert cfg.SIGNAL_MIN_REMAINING_H1 == 3
    assert cfg.SIGNAL_MIN_ROUTE_ATR >= 0.75
    assert cfg.SIGNAL_STRONG_ROUTE_ATR_OVERRIDE > cfg.SIGNAL_MIN_ROUTE_ATR


def test_nondirectional_cards_are_not_suppressed():
    got = nav.assess_new_signal_significance("информационная карточка", {}, {})
    assert got["eligible"] is True
    assert got["reason"] == "not_directional"
