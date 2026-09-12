import ats_reversal_point as ats

def test_module_contract():
    assert hasattr(ats,'process_market')
    assert hasattr(ats,'image_for_alert')
    assert ats._price('USD/JPY',153.12345)=='153.123'

def test_card_says_confirmed_not_wait():
    e={'symbol':'EUR/USD','side':'LONG','extreme':1.1,'trigger':1.11,'close':1.12,'dt':'x','quality':85,'confidence':81,'gap':.2,'strength_ok':True,'tf':'M15'}
    text=ats._format(e)
    assert 'РАЗВОРОТ ПОДТВЕРЖДЁН' in text
    assert 'ОЖИДАН' not in text
