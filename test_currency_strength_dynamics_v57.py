import signal_navigator as sn


def test_navigator_strength_rising_supports_without_veto(monkeypatch):
    monkeypatch.setattr(sn, 'currency_strength_dynamics', lambda *a, **k: {
        'gaps': [0.01, 0.03, 0.06, 0.09], 'state': 'RISING', 'delta': 0.08, 'crossed': False,
    })
    market = {'EUR/USD': {'H1': [object()]}}
    ctx = sn._strength_dynamics_context('EUR/USD', 'LONG', market)
    assert ctx['adjustment'] == 2
    assert 'усиливается' in ctx['label']


def test_navigator_strength_single_jump_is_not_trend(monkeypatch):
    monkeypatch.setattr(sn, 'currency_strength_dynamics', lambda *a, **k: {
        'gaps': [0.02, 0.021, 0.019, -0.08], 'state': 'STABLE', 'delta': -0.10, 'crossed': False,
    })
    market = {'EUR/USD': {'H1': [object()]}}
    ctx = sn._strength_dynamics_context('EUR/USD', 'LONG', market)
    assert ctx['adjustment'] == 0
    assert 'без устойчивого' in ctx['label']


def test_navigator_strength_persistent_cross_is_context_risk(monkeypatch):
    monkeypatch.setattr(sn, 'currency_strength_dynamics', lambda *a, **k: {
        'gaps': [0.08, 0.01, -0.04, -0.07], 'state': 'FALLING', 'delta': -0.15, 'crossed': True,
    })
    market = {'EUR/USD': {'H1': [object()]}}
    ctx = sn._strength_dynamics_context('EUR/USD', 'LONG', market)
    assert ctx['risk_cross'] is True
    assert ctx['adjustment'] == -3
    assert 'риск маршрута' in ctx['label']
