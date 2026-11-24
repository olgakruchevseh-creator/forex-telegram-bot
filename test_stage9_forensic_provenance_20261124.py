"""Stage 9: preserve true FINAL-SIGNIFICANCE evidence without changing trade gates."""
from datetime import datetime, timezone
import json

import decision_journal
import daily_calibration_report as daily
import killer_audit
import signal_navigator


def _write(path, rows):
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')


def test_conflicting_usd_route_is_frozen_from_lifecycle_result():
    active = {
        'GBP/USD': {'status': 'ACTIVE', 'side': 'SHORT', 'start_h1': '2026-11-23T15:00:00+00:00',
                    'last_h1': '2026-11-23T16:00:00+00:00', 'anchor': 1.31,
                    'target': 1.305, 'updated_at': 1795440000, 'secret': 'excluded'},
    }
    snap = signal_navigator._correlated_route_snapshot(active, 'GBP/USD SHORT',
                                                         '2026-11-23T17:00:00+00:00')
    assert snap['status'] == 'CAPTURED'
    assert snap['pair'] == 'GBP/USD'
    assert snap['active_route']['side'] == 'SHORT'
    assert snap['active_route']['last_h1'] == '2026-11-23T16:00:00+00:00'
    assert snap['observed_h1_dt'] == '2026-11-23T17:00:00+00:00'
    assert 'secret' not in snap['active_route']
    # The gate does not need to resolve an unrelated or corrupted state.
    assert signal_navigator._correlated_route_snapshot(active, 'USD/JPY LONG')['status'] == 'COUNTERPART_NOT_RESOLVED'
    assert signal_navigator._correlated_route_snapshot(active, 'GBP/USD LONG')['status'] == 'COUNTERPART_NOT_RESOLVED'


def test_significance_context_excludes_unrelated_fields_and_is_observe_only(tmp_path, monkeypatch):
    raw = {'eligible': False, 'reason': 'correlated_usd_exposure',
           'trade_lifecycle_status': 'WATCH', 'trade_lifecycle_reason': 'correlated_usd_exposure',
           'remaining_h1': 2, 'route_atr': 1.8,
           'correlated_route_snapshot': {
              'pair':'GBP/USD', 'status':'CAPTURED', 'observe_only':True,
              'active_route': {'side':'SHORT','status':'ACTIVE','start_h1':'2026-11-23T15:00:00+00:00',
                               'secret': 'excluded'}, 'extra': 'excluded'},
           'PRIVATE_API_KEY': 'SHOULD_NEVER_WRITE', 'raw_market': {'huge': [1] * 900}}
    ctx = decision_journal.significance_observation_context(raw)
    assert ctx['final_significance_snapshot']['reason'] == 'correlated_usd_exposure'
    assert ctx['final_significance_snapshot']['correlated_route_snapshot']['pair'] == 'GBP/USD'
    assert 'secret' not in str(ctx)
    assert 'PRIVATE_API_KEY' not in str(ctx)
    assert 'raw_market' not in str(ctx)
    # Real stage record persists the snapshot, with unchanged BLOCKED verdict.
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    monkeypatch.setattr(decision_journal, '_already_written_stage', lambda *args: False)
    decision_journal.record_stage('Пара: EUR/USD\nНаправление: LONG\nСигнал: тест',
                                  {}, {}, 'BLOCKED', reason='final_significance_correlated_usd_exposure',
                                  ctx=ctx)
    row = json.loads((tmp_path / 'decision_journal.jsonl').read_text().splitlines()[0])
    assert row['status'] == 'BLOCKED'
    assert row['reason'] == 'final_significance_correlated_usd_exposure'
    assert row['final_significance_snapshot']['correlated_route_snapshot']['active_route']['side'] == 'SHORT'


def test_daily_report_does_not_mistake_navigator_snapshot_for_missing_targets(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    rec = {'event_id': 'one', 'recorded_utc': '2026-11-23T11:00:00+00:00',
           'status': 'SENT', 'pair': 'EUR/USD', 'side':'LONG', 'source':'Patterns',
           'target_source':'navigator_snapshot', 'targets':{'TR1':1.11}}
    _write(tmp_path / 'decision_journal.jsonl', [rec])
    _write(tmp_path / 'decision_replay.jsonl', [])
    report = daily._report('2026-11-23')
    assert 'KILLER ranges: — (отправленных Killer 0/1' in report
    assert 'Снимки целей: Navigator 1/1 · источник/контекст 0/1' in report
    assert 'альтернативные источники опубликованных TR' in report
    assert 'порог 5 семейств Killer — разные измерения' in report


def test_proxy_favorable_is_not_renamed_a_confirmed_filter_bug(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    row={'event_id':'b','recorded_utc':'2026-11-23T14:00:00+00:00',
         'status':'BLOCKED','pair':'USD/CAD','side':'LONG','source':'ZigZag',
         'observed_h1_dt':'2026-11-23T13:00:00+00:00','reason':'unified_h1_not_fresh'}
    _write(tmp_path/'decision_journal.jsonl', [row])
    _write(tmp_path/'decision_replay.jsonl', [{'decision_id':'b','horizons_h1':{'3':{'mfe_atr':1.1,'mae_atr':.1}}}])
    report=daily._report('2026-11-23')
    assert 'proxy-благоприятные 1' in report
    assert 'не доказанные ошибки фильтра' in report
    assert 'ошибочных блокировок' not in report


def test_killer_empty_scanner_facts_is_labelled_as_autonomous_hunter(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    _write(tmp_path/'killer_decisions.jsonl', [
        {'id':'a','status':'REJECTED','recorded_utc':'2026-11-23T12:00:00+00:00',
         'pair':'USD/CHF','side':'SHORT','h1_dt':'2026-11-23T11:00:00+00:00',
         'source_fact_hashes':[], 'reason':'not_enough_independent_families', 'family_count':0}
    ])
    text=killer_audit.daily_digest('2026-11-23')
    assert 'без исходных карт сканеров 1' in text
    assert 'совместимо с автономным Hunter' in text
    assert 'не доказательство потери данных' in text


def test_usd_corr_daily_audit_counts_only_frozen_counterparts(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    rows = [
      {'event_id':'old', 'status':'BLOCKED','recorded_utc':'2026-11-23T10:00:00+00:00',
       'reason':'final_significance_correlated_usd_exposure'},
      {'event_id':'new', 'status':'BLOCKED','recorded_utc':'2026-11-23T11:00:00+00:00',
       'reason':'final_significance_correlated_usd_exposure',
       'final_significance_snapshot':{'correlated_route_snapshot':{'status':'CAPTURED','pair':'GBP/USD'}}},
    ]
    _write(tmp_path/'decision_journal.jsonl', rows)
    _write(tmp_path/'decision_replay.jsonl', [])
    text = daily._report('2026-11-23')
    assert 'Аудит USD-экспозиции: блокировок 2' in text
    assert 'снимков конфликтующих маршрутов 1/2' in text
    assert 'GBP/USD 1' in text
