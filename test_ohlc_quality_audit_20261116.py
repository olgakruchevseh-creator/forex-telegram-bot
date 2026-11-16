"""Synthetic regression coverage; no trading signals or live vendor calls."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3

import pytest

import config as cfg
import ohlc_history_journal as archive
import ohlc_quality_audit as quality
import replay_calibration

BASE = datetime(2026, 11, 1, 0, tzinfo=timezone.utc)
AS_OF = datetime(2026, 11, 25, 0, tzinfo=timezone.utc)


def row(hour: int, price: float = 1.1) -> dict:
    return {'datetime': (BASE + timedelta(hours=hour)).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'open': price, 'high': price+0.0005, 'low': price-0.0005,
            'close': price+0.0002}


def source_db(tmp_path, *, hours=390, pairs=None, gap=None, source='BOT_SCAN_TWELVE_DATA_OR_CACHE', htf_offset=-4):
    path = tmp_path / 'forex_ohlc_history.sqlite'
    pairs = list(pairs if pairs is not None else cfg.PAIRS)
    grid = {n: row(n, 1.10 + n*0.00021) for n in range(hours) if n != gap}
    with archive.init_db(path) as db:
        for pair in pairs:
            archive.put_rows(db, pair, 'H1', list(grid.values()), source=source, as_of=AS_OF)
            d1 = []
            for stamp in (datetime(2026, 11, d, tzinfo=timezone.utc) for d in range(3, 15)):
                origin = stamp+timedelta(hours=htf_offset)
                n = int((origin-BASE)/timedelta(hours=1))
                if any(k not in grid for k in range(n,n+24)):
                    continue
                v = [grid[k] for k in range(n,n+24)]
                d1.append({'datetime': stamp.isoformat(), 'open':v[0]['open'],
                           'high':max(x['high'] for x in v),
                           'low':min(x['low'] for x in v), 'close':v[-1]['close']})
            archive.put_rows(db,pair,'D1',d1,source=source,as_of=AS_OF)
            w1 = []
            for stamp in (datetime(2026, 11, 9, tzinfo=timezone.utc),):
                n = int(((stamp+timedelta(hours=htf_offset))-BASE)/timedelta(hours=1))
                if any(k not in grid for k in range(n,n+168)):
                    continue
                v = [grid[k] for k in range(n,n+168)]
                w1.append({'datetime':stamp.isoformat(),'open':v[0]['open'],
                           'high':max(x['high'] for x in v),
                           'low':min(x['low'] for x in v),'close':v[-1]['close']})
            archive.put_rows(db,pair,'W1',w1,source=source,as_of=AS_OF)
    return path


def test_file_does_not_exist_is_never_created(tmp_path):
    target = tmp_path / 'missing.sqlite'
    with pytest.raises(FileNotFoundError):
        quality.audit(target)
    assert not target.exists()


def test_audit_flags_weekend_and_ambiguous_source(tmp_path):
    target = source_db(tmp_path)
    report = quality.audit(target)
    assert report['mode'] == 'OBSERVE_ONLY' and report['trade_effect'] is False
    assert report['weekend_open_utc']['H1']['rows'] > 0
    assert report['weekend_open_utc']['H1']['nonflat_rows'] == report['weekend_open_utc']['H1']['rows']
    assert report['source_provenance']['ambiguous_rows'] == report['counts']['total_rows']
    assert 'WEEKEND_INTRADAY_BARS_REQUIRE_SOURCE_CHECK' in report['flags']
    assert report['source_provenance']['can_distinguish_api_from_cache'] is False


def test_detects_minus_four_hour_daily_and_weekly_but_does_not_shift(tmp_path):
    target = source_db(tmp_path)
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    report = quality.audit(target)
    for item in ('D1_vs_H1','W1_vs_H1'):
        compare = report['mtf_comparison'][item]
        assert compare['label_offset_hours_for_best_match'] == -4
        assert compare['offset_is_provider_timezone_proof'] is False
        assert compare['best']['matching_windows'] == compare['best']['complete_windows']
        assert compare['label_at_zero_offset']['matching_windows'] < compare['best']['matching_windows']
    assert 'HIGH_TF_TIME_BASIS_DIFFERENCE' in report['flags']
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before


def test_genuine_utc_aligned_daily_weekly_do_not_trigger_shift(tmp_path):
    report = quality.audit(source_db(tmp_path, htf_offset=0))
    assert report['mtf_comparison']['D1_vs_H1']['label_offset_hours_for_best_match'] == 0
    assert report['mtf_comparison']['W1_vs_H1']['label_offset_hours_for_best_match'] == 0
    assert 'HIGH_TF_TIME_BASIS_DIFFERENCE' not in report['flags']


def test_missing_h1_is_never_filled_to_create_false_alignment(tmp_path):
    report = quality.audit(source_db(tmp_path, gap=190, pairs=[cfg.PAIRS[0]]))
    assert report['h1_coverage']['gaps_by_pair_including_weekends'][cfg.PAIRS[0]] == 1
    assert 'H1_CLOCK_GAPS_PRESENT' in report['flags']
    assert 'H1_PAIR_TIMESTAMPS_NOT_SYNCHRONIZED' in report['flags']


def test_no_matching_window_cannot_claim_anchor(tmp_path):
    report = quality.audit(source_db(tmp_path, hours=28, pairs=[cfg.PAIRS[0]]))
    assert report['mtf_comparison']['W1_vs_H1']['label_offset_hours_for_best_match'] is None
    assert report['mtf_comparison']['D1_vs_H1']['label_offset_hours_for_best_match'] is None


def test_observe_writes_only_its_json_does_not_mutate_database(tmp_path):
    target = source_db(tmp_path, pairs=[cfg.PAIRS[0]])
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    report = quality.observe(db_path=target)
    observed_file = tmp_path/'ohlc_quality_observe.json'
    assert observed_file.exists()
    assert json.loads(observed_file.read_text()) == report
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before
    assert not (tmp_path/'ohlc_quality_observe.json.tmp').exists()


def test_late_first_seen_is_not_claimed_to_be_known_at_decision(tmp_path):
    target = source_db(tmp_path)
    result = quality.audit(target)
    assert result['late_first_capture_rows']['H1'] > 0
    assert 'H1_FIRST_CAPTURE_AFTER_BAR_CLOSE' in result['flags']
    assert result['independent_vendor_verification'] is False


def test_replay_calibration_marks_statistics_unverified_without_changing_numbers():
    rows = [{'source': 'Patterns', 'pair': 'EUR/USD', 'status': 'SENT', 'regime': 'trend',
             'horizons_h1': {'3': {'mfe_atr': 1, 'mae_atr': 0.5}},
             'efficiency_3h': .667, 'targets_hit_8h': {'TR1': True},
             'timing_class': 'timely'}] * 25
    output = replay_calibration._build_calibration(rows)
    assert output['mode'] == 'OBSERVE_ONLY'
    assert output['verified_profitability_claim'] is False
    assert output['data_quality_grade'] == 'UNVERIFIED_SOURCE_AND_SESSION'
    assert next(iter(output['groups'].values()))['tr1_hit_rate_8h'] == 1
    assert next(iter(output['module_pair_regime_cells'].values()))['calibrated_tr1_probability'] == 1


def test_synthetic_one_pair_does_not_prove_weekly_timezone(tmp_path):
    report = quality.audit(source_db(tmp_path,pairs=[cfg.PAIRS[0]]))
    weekly = report['mtf_comparison']['W1_vs_H1']
    assert weekly['best']['matching_windows'] == 1
    assert weekly['label_offset_hours_for_best_match'] is None


def test_read_only_audit_returns_no_source_bars(tmp_path):
    target = source_db(tmp_path,pairs=[cfg.PAIRS[0]])
    output = json.dumps(quality.audit(target))
    assert 'apikey' not in output and 'secret' not in output
    assert '1.100' not in output  # aggregate counts, not original quotes


def test_bad_sqlite_is_not_recreated_or_overwritten(tmp_path):
    path=tmp_path/'bad.sqlite'; path.write_bytes(b'not a sqlite database')
    before=path.read_bytes()
    with pytest.raises(sqlite3.DatabaseError):
        quality.audit(path)
    assert path.read_bytes()==before
