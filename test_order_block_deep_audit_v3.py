import tempfile
from pathlib import Path
from unittest.mock import patch
import order_block as ob


def test_order_block_has_geometry_and_thrust_defaults():
    b=ob.OrderBlock('id','EUR/USD','H1','LONG',1.10,1.11,1.12,'2026-01-01 10:00:00',80)
    assert b.equilibrium == 0.0
    assert b.thrust_atr == 0.0
    assert b.touch_count == 0


def test_delivery_ack_commits_only_pending_block():
    with tempfile.TemporaryDirectory() as d, patch.dict('os.environ', {'STATE_DIR': d}):
        b=ob.OrderBlock('id','EUR/USD','H1','LONG',1.10,1.11,1.12,'2026-01-01 10:00:00',80)
        text='ob-card'
        import hashlib
        digest=hashlib.sha256(text.encode()).hexdigest()[:20]
        ob._save({'bootstrapped':True,'blocks':{'id':ob.asdict(b)},'pending':{digest:{'block_id':'id','text':text}}})
        assert ob.mark_delivered(text) is True
        st=ob._load()
        assert st['blocks']['id']['retest_sent'] is True
        assert not st['pending']
        assert ob.mark_delivered(text) is False
