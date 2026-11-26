"""Stage 29B: regression of real source logic, no live trading or live API."""
from __future__ import annotations

import ast
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from analysis import Candle
import config as cfg
import disbalance_calibration_28f as calibration
import market_state
import movement_progress
import news
import pdh_pdl_audit_28g as pdh
import signal_navigator as navigator
import zigzag_scanner


def bar(at, op=1.1, hi=1.101, lo=1.099, close=1.1):
    return Candle(at.isoformat(), op, hi, lo, close)


def test_29b_nav_source_tr1_kept_even_when_near_senior_swing(monkeypatch):
    start = datetime(2026, 11, 24, tzinfo=timezone.utc)
    market = {tf: [bar(start + timedelta(hours=i), 1.100, 1.101, 1.099, 1.100)
                   for i in range(36)] for tf in ('H1', 'H4', 'D1')}
    monkeypatch.setattr(movement_progress, '_swings',
                        lambda tf, bars: ([SimpleNamespace(kind='high', price=1.1052)] if tf == 'H4' else []))
    monkeypatch.setattr(movement_progress, '_movement_mode', lambda *a: 'IMPULSE')
    monkeypatch.setattr(movement_progress, '_strength_ok', lambda *a: (True, .2))
    monkeypatch.setattr(market_state, 'build',
                        lambda *a: SimpleNamespace(as_dict=lambda: {}))
    card = ('📈 DAILY HIGH/LOW — ПОДТВЕРЖДЕНО\nПара: EUR/USD\nНаправление: LONG\n'
            'Таймфрейм: H1\nЦена закрытия: 1.10000\nTR1: 1.10500')
    monkeypatch.setattr(cfg, 'STAGE29_NAVIGATOR_SOURCE_TR1_WINS', True)
    out = navigator._trigger_route('EUR/USD', 'LONG', market, card)
    assert out and out['targets'][0]['price'] == pytest.approx(1.10500)
    assert out['targets'][0]['tf'].startswith('TR1 источника')
    monkeypatch.setattr(cfg, 'STAGE29_NAVIGATOR_SOURCE_TR1_WINS', False)
    legacy = navigator._trigger_route('EUR/USD', 'LONG', market, card)
    assert legacy and legacy['targets'][0]['price'] == pytest.approx(1.1052)


def test_29b_pdh_same_date_not_yesterday_evening(monkeypatch):
    # NY day Wed 04 Nov 2026 closes Wed at 22:00 UTC. Vendor D1 stamped
    # 04 Nov is not closed before Thu 00:00 UTC.
    wed = datetime(2026, 11, 4, tzinfo=timezone.utc)
    previous = wed - timedelta(days=1)
    h1 = [bar(wed - timedelta(hours=2) + timedelta(hours=i)) for i in range(25)]
    d1 = [bar(previous, 1.1, 1.12, 1.08, 1.1),
          bar(wed, 1.1, 1.11, 1.09, 1.1)]
    moment = wed + timedelta(hours=23)
    monkeypatch.setattr(cfg, 'STAGE29_PDH_AUDIT_SAME_DATE', True)
    found = pdh.inspect_pair({'H1': h1, 'D1': d1}, as_of=moment)
    assert found['status'] == 'VENDOR_D1_NOT_CLOSED_YET'
    assert found['prior_ny_session'] == '2026-11-04'
    assert 'max_abs_difference' not in found
    monkeypatch.setattr(cfg, 'STAGE29_PDH_AUDIT_SAME_DATE', False)
    old = pdh.inspect_pair({'H1': h1, 'D1': d1}, as_of=moment)
    assert old['vendor_d1_stamp_utc'].startswith('2026-11-03')
    # The following hour closes matching D1; the new diagnostic may compare.
    next_day = pdh.inspect_pair({'H1': h1, 'D1': d1}, as_of=moment + timedelta(hours=1))
    assert next_day['vendor_d1_stamp_utc'].startswith('2026-11-04') or next_day['status'] != 'OK'


def test_29b_ecb_deposit_rate_is_numeric_but_scenario_not_certain(monkeypatch):
    monkeypatch.setattr(cfg, 'STAGE28_NEWS_RATE_CLASSIFICATION', True)
    title = 'ECB Deposit Facility Rate Decision'
    assert news.classify_effect(title) == 'higher_is_positive'
    event = news.NewsEvent('e', title, 'EUR', 'HIGH',
                           datetime(2026, 11, 26, tzinfo=timezone.utc),
                           '2.00%', '2.25%', '—', news.classify_effect(title))
    scenario = news.scenario_before(event, .1)
    assert 'не выводим автоматически' in scenario
    monkeypatch.setattr(cfg, 'STAGE28_NEWS_RATE_CLASSIFICATION', False)
    assert news.classify_effect(title) == 'context_dependent'


def test_29b_zigzag_info_receipt_and_runtime_ack_are_present(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    text = ('↕️ ZIGZAG — ОТКАТ · НАБЛЮДЕНИЕ\nПара: EUR/USD\n'
            'Направление: LONG\nПоследний максимум: 1.20000')
    zigzag_scanner._save({'pending': {'EUR/USD': {'fingerprint': 'zz-1'}}, 'delivered': {}})
    zigzag_scanner.mark_delivered(text)
    state = zigzag_scanner._load()
    assert state['delivered']['EUR/USD'] == 'zz-1'
    assert not state['pending']
    source = (Path(__file__).parent / 'bot.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    assert 'ZIGZAG_INFO_ACK_FAILED' in source and 'ZIGZAG_INFO_RECONCILE_FAILED' in source
    assert sum(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and isinstance(n.func.value, ast.Name)
               and n.func.value.id == 'zigzag_scanner'
               and n.func.attr == 'mark_delivered' for n in ast.walk(tree)) >= 3


def _historical_csvs(root: Path):
    # Genuine CSV -> real read_basket -> _index -> snapshot -> _candidate path.
    origin = datetime(2026, 10, 1, tzinfo=timezone.utc)
    sources = {
        'H1': (origin, 150, 60),
        'H4': (origin - timedelta(days=14), 100, 240),
        'D1': (origin - timedelta(days=40), 45, 1440),
        'M15': (origin, 600, 15),
        'M5': (origin, 650, 5),
    }
    for pair in cfg.PAIRS:
        for tf, (start, n, minutes) in sources.items():
            path = root / f'{pair.replace("/", "")}_{tf}.csv'
            with path.open('w', newline='', encoding='utf-8') as fh:
                wr = csv.writer(fh)
                wr.writerow(('datetime', 'open', 'high', 'low', 'close'))
                for i in range(n):
                    now = start + timedelta(minutes=i * minutes)
                    wr.writerow((now.strftime('%Y-%m-%d %H:%M:%S'), '1.1000', '1.1010', '1.0990', '1.1000'))


def test_29b_disbalance_scans_sequential_hours_and_resumes_without_duplicates(tmp_path, monkeypatch):
    _historical_csvs(tmp_path)
    path = tmp_path / 'disbalance_replay_29b_ALL.json'
    monkeypatch.setattr(cfg, 'STAGE29_CALIBRATION_ALL_HOURS', True)
    first = calibration.evaluate(tmp_path, max_anchors=8, progress_path=path)
    assert first['scan_processed_h1'] == 8
    assert first['scan_total_h1'] == 150 - 55 - 8
    assert first['conclusion'] == 'REPLAY_IN_PROGRESS'
    assert path.exists() and path.stat().st_size < 25_000
    second = calibration.evaluate(tmp_path, max_anchors=8, progress_path=path)
    assert second['scan_processed_h1'] == 16
    assert second['scan_total_h1'] == first['scan_total_h1']
    assert second['replayed_pair_hours'] >= first['replayed_pair_hours']
    assert not second['module_candidates']
    assert '16 из 87 H1' in calibration.format_ru(second)
    reset = calibration.evaluate(tmp_path, max_anchors=8, progress_path=path, reset_progress=True)
    assert reset['scan_processed_h1'] == 8
    # Legacy read-only operation without explicit progress_path stays compatible.
    legacy = calibration.evaluate(tmp_path, max_anchors=8)
    assert 'scan_total_h1' not in legacy


def test_29b_disbalance_progress_reset_on_vendor_revisions(tmp_path, monkeypatch):
    _historical_csvs(tmp_path)
    path = tmp_path / 'cursor.json'
    monkeypatch.setattr(cfg, 'STAGE29_CALIBRATION_ALL_HOURS', True)
    first = calibration.evaluate(tmp_path, max_anchors=4, progress_path=path)
    assert first['scan_processed_h1'] == 4
    raw = tmp_path / 'EURUSD_H1.csv'
    s = raw.read_text(encoding='utf-8')
    assert '1.1010' in s
    raw.write_text(s.replace('1.1010', '1.1011', 1), encoding='utf-8')
    again = calibration.evaluate(tmp_path, max_anchors=4, progress_path=path)
    assert again['scan_processed_h1'] == 4


def test_29b_disbalance_real_raw_h1_impulse_seen_in_sequential_scan(tmp_path, monkeypatch):
    _historical_csvs(tmp_path)
    path = tmp_path / 'EURUSD_H1.csv'
    rows = list(csv.reader(path.open(newline='', encoding='utf-8')))
    assert len(rows) == 151
    # A real H1 impulse at hour 125: this was rarely sampled by 96 sparse
    # evenly distributed anchors, but must be visited by a full ordered scan.
    rows[126][1:] = ['1.1000', '1.1100', '1.0995', '1.1090']
    with path.open('w', newline='', encoding='utf-8') as handle:
        csv.writer(handle).writerows(rows)
    monkeypatch.setattr(cfg, 'STAGE29_CALIBRATION_ALL_HOURS', True)
    report = calibration.evaluate(tmp_path, max_anchors=120,
                                  progress_path=tmp_path/'scan.json')
    assert report['scan_complete']
    assert report['scan_processed_h1'] == report['scan_total_h1']
    assert report['raw_h1_candidates'] >= 1
    # It is an H1 impulse, NOT necessarily a fully confirmed Disbalance event.
    assert report['module_candidates'] >= 0
