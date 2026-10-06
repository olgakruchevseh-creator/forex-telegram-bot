from analysis import Candle
import layer15_event_sequence as l15

def c(dt,o,h,l,c): return Candle(dt,o,h,l,c)
def test_layer15_is_passive_and_reads_existing_facts():
    r=l15.assess('EUR/USD','LONG',['СНЯТИЕ ЛИКВИДНОСТИ\nIMBALANCE / FVG'],{}, {})
    assert r['observe_only'] and not r['trade_effect'] and not r['direction_claim']
    assert 'SWEEP' in r['generic_facts'] and 'FVG' in r['generic_facts']

def test_monday_chain_needs_ordered_closed_h1_events():
    bars=[]
    # Monday 2026-10-05 range 1.1000..1.1100
    for i in range(8,16): bars.append(c(f'2026-10-05T{i:02d}:00:00+00:00',1.104,1.110,1.100,1.105))
    # Tuesday: sweep/reclaim then strong bullish displacement; three-candle bull FVG.
    bars += [c('2026-10-06T08:00:00+00:00',1.103,1.104,1.098,1.102),
             c('2026-10-06T09:00:00+00:00',1.102,1.104,1.101,1.103),
             c('2026-10-06T10:00:00+00:00',1.104,1.112,1.106,1.111),
             c('2026-10-06T11:00:00+00:00',1.111,1.114,1.110,1.113)]
    r=l15.assess('EUR/USD','LONG',[],{'H1':bars},{})
    assert r['monday_sequence']['state'] in ('FVG_FORMED','SEQUENCE_CONFIRMED')
    assert 'MONDAY_EXTREME_SWEEP_RECLAIM' in r['monday_sequence']['steps']
