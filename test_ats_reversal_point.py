import ats_reversal_point as ats

def test_module_contract():
    assert hasattr(ats,'process_market')
    assert hasattr(ats,'image_for_alert')
    assert ats._price('USD/JPY',153.12345)=='153.123'

def test_card_says_confirmed_not_wait():
    e={'symbol':'EUR/USD','side':'LONG','extreme':1.1,'trigger':1.11,'close':1.12,'dt':'x','quality':85,'confidence':81,'gap':.2,'strength_ok':True,'tf':'H1','market_regime':'TREND','pullback_mode':'TRANSITION','pullback_bars':2,'pullback_move_atr':0.4,'pullback_efficiency':0.5,'m15_bias':0,'m5_bias':0}
    text=ats._format(e)
    assert 'РАЗВОРОТ ПОДТВЕРЖДЁН' in text
    assert 'ОЖИДАН' not in text


def test_card_uses_h1_as_owner_and_ltf_only_confirmation():
    e={'symbol':'EUR/USD','side':'SHORT','extreme':1.2,'trigger':1.19,'close':1.18,'dt':'x','quality':88,'confidence':84,'gap':-.2,'strength_ok':True,'tf':'H1','market_regime':'TREND','pullback_mode':'PULLBACK','pullback_bars':3,'pullback_move_atr':.9,'pullback_efficiency':.6,'m15_bias':-1,'m5_bias':0}
    text=ats._format(e)
    assert 'закрытая H1-свеча' in text
    assert 'M15/M5:' in text and 'только подтверждение' in text
    assert 'закрытой M15-свечой подтвердила' not in text
