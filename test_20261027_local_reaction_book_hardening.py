from analysis import Candle, PairStack, Swing, TfView
import classic_pivot_points
import master_direction
import signal_navigator
import zigzag_correction_structure


def _view(tf, bias):
    return TfView(tf, tf, 1.0, "", "", 20.0, bias, None)


def _stack(d1, h4, h1):
    return PairStack("NZD/USD", 0.57, 0.3,
                     {"D1": _view("D1", d1), "H4": _view("H4", h4), "H1": _view("H1", h1)},
                     0, 0)


def _raw(text):
    # Master operates before the presentation layer translates LONG/SHORT.
    return f"Пара: NZD/USD\nНаправление: LONG\n{text}"


def test_local_reaction_cluster_requires_h1_and_parent_support():
    alerts = [
        _raw("СНЯТИЕ PDL И ВОЗВРАТ"),
        _raw("CHAIN ENTRY №1"),
        _raw("ПАТТЕРН ПОДТВЕРЖДЁН\nПаттерн: Молот / бычий Pin Bar"),
    ]
    allowed, cluster = master_direction._local_reaction_gate(_stack(-1, 0, 0), 0, 1, alerts, "NZD/USD")
    assert cluster is True
    assert allowed is False

    allowed, cluster = master_direction._local_reaction_gate(_stack(-1, 1, 1), 0, 1, alerts, "NZD/USD")
    assert cluster is True
    assert allowed is True


def test_structural_pattern_is_not_collapsed_into_local_reaction_cluster():
    alerts = [_raw("ПАТТЕРН ПОДТВЕРЖДЁН\nПаттерн: Двойное дно")]
    allowed, cluster = master_direction._local_reaction_gate(_stack(-1, 0, 0), 0, 1, alerts, "NZD/USD")
    assert allowed is True
    assert cluster is False


def test_daily_high_low_source_and_price_are_preserved():
    card = """Пара: NZD/USD
Направление: LONG
СНЯТИЕ PDL И ВОЗВРАТ
Цена подтверждения: 0.56814
Текущая цена: 0.57039
TR1: 0.56856
"""
    assert signal_navigator._source_name(card) == "Daily High/Low"
    assert signal_navigator._source_price(card) == 0.56814


def test_stale_same_side_route_is_not_reused_behind_its_anchor():
    old = {
        "status": "ACTIVE", "side": "LONG", "anchor": 0.57039,
        "targets": [{"price": 0.57111}, {"price": 0.57592}, {"price": 0.59070}],
    }
    assert signal_navigator._route_geometrically_compatible(old, 0.57060) is True
    assert signal_navigator._route_geometrically_compatible(old, 0.56814) is False


def test_classic_pivot_formula_is_read_only_context():
    levels = classic_pivot_points._levels(1.1100, 1.1000, 1.1060)
    pp = (1.1100 + 1.1000 + 1.1060) / 3
    assert abs(levels["PP"] - pp) < 1e-12
    assert abs(levels["R1"] - (2 * pp - 1.1000)) < 1e-12
    assert abs(levels["S1"] - (2 * pp - 1.1100)) < 1e-12


def test_zigzag_abc_uses_confirmed_pivots_and_candidate_cannot_signal():
    bars = [Candle(f"2026-10-26T{i:02d}:00:00+01:00", 1.0, 1.2, 0.8, 1.0) for i in range(8)]
    swings = [
        Swing(0, 1.1000, "low"),
        Swing(1, 1.1200, "high"),
        Swing(2, 1.1100, "low"),
        Swing(3, 1.1300, "high"),
        Swing(4, 1.1150, "low"),
        Swing(5, 1.1250, "high"),
        Swing(6, 1.1120, "low"),
    ]
    result = zigzag_correction_structure.build(swings, bars, main_side=1, adaptive_side=1, tolerance=0.0)
    corr = result["correction"]
    assert corr["status"] == "ABC_CONFIRMED"
    assert corr["endpoint_confirmed"] is True
    assert corr["candidate_can_signal"] is False
    assert result["direction_effect"] == "NONE"
    assert result["publication_effect"] == "NONE"


def test_daily_high_low_explicit_tr1_is_first_module_target():
    bars = []
    for i in range(30):
        base = 0.5670 + i * 0.00002
        bars.append(Candle(f"2026-10-20T{i % 24:02d}:00:00+02:00", base, base + 0.0006, base - 0.0005, base + 0.0001))
    card = """Пара: NZD/USD
Направление: LONG
СНЯТИЕ PDL И ВОЗВРАТ
Цена подтверждения: 0.56814
TR1: 0.56856
"""
    targets = signal_navigator._module_targets(
        "NZD/USD", "LONG", {"H1": bars}, card, 0.56814, 0.0010
    )
    assert targets
    assert abs(targets[0]["price"] - 0.56856) < 1e-12
    assert "TR1 источника" in targets[0]["tf"]
