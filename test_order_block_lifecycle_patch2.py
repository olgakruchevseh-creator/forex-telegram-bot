import order_block as ob
from analysis import Candle


def _c(dt, o=1.10, c=1.101):
    return Candle(dt=dt, open=o, high=max(o,c)+.001, low=min(o,c)-.001, close=c)


def test_order_block_state_backward_compatible_last_h1_dt():
    b = ob.OrderBlock(block_id="x", symbol="EUR/USD", tf="H1", side="LONG",
        low=1.10, high=1.101, bos_level=1.102, created_dt="2026-10-07T08:00:00", quality=80)
    assert b.last_h1_dt == ""


def test_format_declares_h1_primary_and_m15_confirmation_only():
    event={"symbol":"EUR/USD","side":"LONG","tf":"H1","confirm_tf":"H1",
           "low":1.1000,"high":1.1010,"bos_level":1.1020,"close":1.1015,
           "fvg":True,"gap":.12,"quality":88,"confidence":84,
           "reaction_path":"sweep_reclaim","structure_confirmations":["MSS"]}
    text=ob.format_message(event)
    assert "Цена закрытия H1" in text
    assert "M15 только подтверждает" in text
    assert "закрытая H1" in text
