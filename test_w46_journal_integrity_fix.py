"""W46 audit regressions: provenance, causal snapshots, bounded replay, dedupe."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from analysis import Candle
import decision_journal
import journal_tail
import replay_calibration
import signal_context
import signal_journal
import signal_release_guard


def test_tail_reader_does_not_truncate_unicode_and_uses_last_rows(tmp_path):
    p = tmp_path / 'journal.jsonl'
    p.write_text(''.join(json.dumps({'n': i, 'label': 'шорт'}, ensure_ascii=False)+'\n' for i in range(15)), encoding='utf-8')
    assert [r['n'] for r in journal_tail.tail_jsonl(p, 3)] == [12, 13, 14]
    assert [r['label'] for r in journal_tail.tail_jsonl(p, 3)] == ['шорт']*3
    assert journal_tail.tail_jsonl(tmp_path / 'missing.jsonl') == []


def test_source_provenance_and_market_snapshot_follow_actual_card(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    bar = Candle('2026-11-10 10:00:00',1.34,1.35,1.33,1.34263)
    monkeypatch.setattr(signal_journal, '_h1', lambda by_tf: [bar])
    monkeypatch.setattr(signal_journal, 'atr', lambda *args: 0.0009707)
    snapshot = SimpleNamespace(regime=SimpleNamespace(name='RANGE'),
          liquidity=SimpleNamespace(target_side='BSL'), exhaustion=SimpleNamespace(score=20.0))
    monkeypatch.setattr(signal_journal.market_state, 'build', lambda *a: snapshot)
    delivered = 'Пара: GBP/USD\nНаправление: LONG\nZigZag · вторичный контекст\nКачество: 98/100'
    primary = 'IMBALANCE — подтверждено\nПара: GBP/USD\nНаправление: LONG'
    assert signal_journal.record_sent(delivered, {'GBP/USD':{}}, bar.dt, source_text=primary)
    r = next(iter(json.loads((tmp_path/'signal_journal_state.json').read_text())['records'].values()))
    assert r['source'] == 'Imbalance/FVG'
    assert r['market_regime'] == 'RANGE'
    assert r['liquidity_target'] == 'BSL'
    expected='|'.join(['GBP/USD','LONG','Imbalance/FVG',bar.dt,signal_context.event_fingerprint(primary)])
    assert r['scenario_id'] == hashlib.sha256(expected.encode()).hexdigest()[:24]


def test_sent_history_preserves_telegram_delivered_and_replay_sees_it(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    monkeypatch.setattr(decision_journal,'_base',lambda *a,**kw:{'event_id':'sent1','status':'SENT','pair':'GBP/USD','source':'Patterns'})
    monkeypatch.setattr(decision_journal,'_killer_gate_observation',lambda *a:None)
    decision_journal.record_sent('CARD', {})
    rows=journal_tail.tail_jsonl(tmp_path/'decision_sent_history.jsonl')
    assert len(rows)==1 and rows[0]['event_id']=='sent1'
    # A large candidate tail has no SENT records. The dedicated history survives.
    (tmp_path/'decision_journal.jsonl').write_text(''.join(json.dumps({'event_id':f'blocked{i}', 'status':'BLOCKED'})+'\n' for i in range(80)))
    assert 'sent1' in {r['event_id'] for r in replay_calibration._records(limit=2)}


def test_unified_gate_journals_one_event_once_without_losing_archives(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    signal_release_guard._RECORDED_DECISIONS.clear()
    signal_release_guard._RECORDED_FIFO.clear()
    verdict=signal_release_guard.Decision('BLOCK','h1_not_fresh','GBP/USD','LONG','patterns','structure','2026-11-13T22:00:00+00:00')
    signal_release_guard.journal(verdict,'same-event')
    signal_release_guard.journal(verdict,'same-event')
    assert len((tmp_path/'unified_signal_decisions.jsonl').read_text().splitlines())==1
    # Rotating must preserve an archived copy rather than overwrite `previous`.
    current=tmp_path/'unified_signal_decisions.jsonl'
    current.write_bytes(b'x' * 5_000_100)
    signal_release_guard.journal(verdict,'new-event')
    assert len(list(tmp_path.glob('unified_signal_decisions.*Z.jsonl')))==1
    assert len(current.read_text().splitlines())==1
