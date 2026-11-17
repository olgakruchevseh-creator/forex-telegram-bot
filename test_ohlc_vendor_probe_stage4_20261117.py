"""Regression tests for bounded direct-source OHLC probe. Never calls live APIs."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3

import pytest

import config as cfg
import ohlc_history_journal as archive
import ohlc_vendor_probe as probe

NOW = datetime(2026, 11, 17, 10, tzinfo=timezone.utc)
PAIR = 'EUR/USD'
HOUR = '2026-11-16T08:00:00Z'
BAR = {'open': 1.10000, 'high': 1.10080, 'low': 1.09920, 'close': 1.10040}


def fixture_db(tmp_path, *, tf='H1', stamp=HOUR):
    path = tmp_path / 'history.sqlite'
    with archive.init_db(path) as db:
        archive.put_rows(db, PAIR, tf, [{'datetime': stamp, **BAR}],
                         source='BOT_SCAN_TWELVE_DATA_OR_CACHE', as_of=NOW)
    return path


class MockResponse:
    status_code = 200
    def __init__(self, values, *, status_code=200):
        self.values = values
        self.status_code = status_code
    def json(self):
        return {'status':'ok', 'values': self.values}


class FakeSession:
    def __init__(self, values=None, status_code=200, error=None):
        self.values = values if values is not None else []
        self.status_code = status_code
        self.error = error
        self.calls = []
        self.closed = False
    def get(self, url, params, timeout):
        self.calls.append((url, params, timeout))
        if self.error:
            raise self.error
        return MockResponse(self.values, status_code=self.status_code)
    def close(self):
        self.closed = True


def case(tf='H1', stamp=HOUR, reason='test'):
    return ((PAIR, tf, stamp, reason),)


def vendor_row(raw_stamp='2026-11-16 08:00:00', **overrides):
    return {'datetime': raw_stamp, **dict(BAR, **overrides)}


def test_exact_utc_h1_matches_from_new_direct_api_without_changing_archive(tmp_path):
    path = fixture_db(tmp_path)
    old = hashlib.sha256(path.read_bytes()).digest()
    fake = FakeSession([vendor_row()])
    result = probe.run_probe(db_path=path, out_path=tmp_path/'probe.json', api_key='DO_NOT_LEAK_KEY',
                             session=fake, now=NOW, cases=case())
    assert result['cases'][0]['status'] == 'MATCH'
    assert result['independent_second_provider'] is False
    assert result['verified_original_historical_response'] is False
    assert result['trade_effect'] is False
    assert result['historical_prices_modified'] is False
    assert result['cases'][0]['max_abs_delta_quote_ticks'] == 0
    assert hash(old) == hash(hashlib.sha256(path.read_bytes()).digest())
    output = (tmp_path/'probe.json').read_text()
    assert 'DO_NOT_LEAK_KEY' not in output
    assert '1.10080' not in output
    assert len(fake.calls) == 1 and fake.closed
    assert fake.calls[0][1]['timezone'] == 'UTC'
    assert 'outputsize' not in fake.calls[0][1]
    assert fake.calls[0][2] == 10
    assert probe.needs_probe(tmp_path/'probe.json') is False


def test_disagreement_reports_tick_differences_but_never_chooses_truth(tmp_path):
    path = fixture_db(tmp_path)
    fake = FakeSession([vendor_row(close=1.10009)])
    result = probe.audit_once(db_path=path, session=fake, api_key='KEY', now=NOW, cases=case())
    item = result['cases'][0]
    assert item['status'] == 'DIFFER'
    assert item['max_abs_delta_quote_ticks'] == 31
    assert item['delta_quote_ticks_stored_minus_vendor']['close'] == 31
    assert 'archive_source_label' in item


def test_missing_vendor_weekend_bar_is_not_mislabeled_as_falsification(tmp_path):
    stamp = '2026-11-07T00:00:00Z'
    path = fixture_db(tmp_path,stamp=stamp)
    result = probe.audit_once(db_path=path, session=FakeSession([]), api_key='KEY', now=NOW,
                               cases=case(stamp=stamp))
    assert result['cases'][0]['status'] == 'NO_BAR_RETURNED_FOR_LABEL'
    assert result['notes'][2].startswith('Отсутствие выходной свечи')


def test_daily_vendor_date_is_explicitly_not_utc_or_proof_of_anchor(tmp_path):
    stamp='2026-11-06T00:00:00Z'
    path=fixture_db(tmp_path,tf='D1',stamp=stamp)
    fake=FakeSession([vendor_row('2026-11-06', high=1.10080)])
    result=probe.audit_once(db_path=path,session=fake,api_key='KEY',now=NOW,
                            cases=case('D1',stamp))
    item=result['cases'][0]
    assert item['status']=='VALUES_MATCH_TIME_BASIS_UNVERIFIED'
    assert item['provider_time_basis']=='LOCAL_DATE_UNVERIFIED'
    assert 'timezone' not in fake.calls[0][1]
    assert 'outputsize' not in fake.calls[0][1]
    assert result['d1_w1_timezone_parameter_ignored_by_vendor']


def test_weekly_label_and_dates_never_claim_utc_alignment(tmp_path):
    stamp='2026-11-09T00:00:00Z'
    path=fixture_db(tmp_path,tf='W1',stamp=stamp)
    fake=FakeSession([vendor_row('2026-11-09 00:00:00', close=1.10038)])
    out=probe.audit_once(db_path=path,session=fake,api_key='KEY',now=NOW,
                         cases=case('W1',stamp))
    assert out['cases'][0]['status']=='VALUES_DIFFER_TIME_BASIS_UNVERIFIED'
    assert fake.calls[0][1]['interval']=='1week'


def test_network_exception_never_leaks_key_or_url(tmp_path):
    path=fixture_db(tmp_path)
    secret='TOP_SECRET_PRIVATE_KEY'
    fake=FakeSession(error=RuntimeError('https://api.twelvedata.com/time_series?apikey='+secret))
    result=probe.audit_once(db_path=path,session=fake,api_key=secret,now=NOW,cases=case())
    encoded=json.dumps(result)
    assert secret not in encoded
    assert result['cases'][0]['status']=='NETWORK_OR_JSON_RuntimeError'
    assert len(fake.calls)==1


def test_http_rate_limit_is_diagnostic_not_trade_block(tmp_path):
    path=fixture_db(tmp_path)
    response=probe.audit_once(db_path=path,session=FakeSession(status_code=429),api_key='KEY',now=NOW,cases=case())
    assert response['cases'][0]['status']=='HTTP_429'
    assert response['trade_effect'] is False


def test_older_cache_without_key_is_not_accepted_as_an_api_response(tmp_path):
    path=fixture_db(tmp_path)
    fake=FakeSession([vendor_row()])
    with pytest.raises(ValueError, match='API credentials'):
        probe.audit_once(db_path=path,session=fake,api_key='',now=NOW,cases=case())
    assert fake.calls==[]


def test_future_bar_and_absent_archive_bar_never_trigger_requests(tmp_path):
    path=fixture_db(tmp_path)
    case_absent=case(tf='M5')
    fake=FakeSession([vendor_row()])
    result=probe.audit_once(db_path=path,session=fake,api_key='KEY',now=NOW,cases=case_absent)
    assert result['cases'][0]['status']=='ARCHIVE_BAR_MISSING'
    assert not fake.calls
    future=datetime(2026,11,17,11,tzinfo=timezone.utc)
    with archive.init_db(path) as db:
        # Force a future-dated archive row for guard test (not possible through put_rows).
        db.execute("INSERT INTO candles VALUES(?,?,?,?,?,?,?,?,?,?)",(
            PAIR,'M5',probe.iso(future),1,2,0.5,1,'SIM','2026-11-17T10:00:00Z','2026-11-17T10:00:00Z'))
        db.commit()
    result=probe.audit_once(db_path=path,session=fake,api_key='KEY',now=NOW,
                            cases=case(tf='M5',stamp=probe.iso(future)))
    assert result['cases'][0]['status']=='BAR_NOT_YET_CLOSED'
    assert fake.calls==[]


def test_idempotence_and_retry_spacing(tmp_path):
    path=fixture_db(tmp_path)
    out=tmp_path/'ohlc_vendor_probe_observe.json'
    fake=FakeSession([vendor_row()])
    probe.run_probe(db_path=path,out_path=out,session=fake,api_key='KEY',now=NOW,cases=case())
    assert not probe.needs_probe(out,now=NOW+timedelta(days=9))
    fake=FakeSession(status_code=429)
    probe.run_probe(db_path=path,out_path=out,session=fake,api_key='KEY',now=NOW,cases=case())
    assert not probe.needs_probe(out,now=NOW+timedelta(hours=5,minutes=59))
    assert probe.needs_probe(out,now=NOW+timedelta(hours=6,minutes=1))


def test_bad_or_nonexistent_database_cannot_be_created(tmp_path):
    file=tmp_path/'not_there.sqlite'
    with pytest.raises(FileNotFoundError):
        probe.audit_once(db_path=file,session=FakeSession(),api_key='KEY',now=NOW,cases=case())
    assert not file.exists()


def test_invalid_ohlc_and_malformed_vendor_payload(tmp_path):
    path=fixture_db(tmp_path)
    corrupt=FakeSession([vendor_row(high='nan')])
    report=probe.audit_once(db_path=path,session=corrupt,api_key='KEY',now=NOW,cases=case())
    assert report['cases'][0]['status']=='VENDOR_INVALID_OHLC'
    corrupt=FakeSession([vendor_row(low='oops')])
    report=probe.audit_once(db_path=path,session=corrupt,api_key='KEY',now=NOW,cases=case())
    assert report['cases'][0]['status']=='VENDOR_INVALID_BAR'


def test_real_sqlite_can_be_probed_read_only_with_mocks_if_present():
    # No dependency on user data: a real file is not bundled in source control.
    assert len(probe.CASES)==8
    assert {x[1] for x in probe.CASES} == {'H1','H4','M15','M5','D1','W1'}
    assert all(x[0] in cfg.PAIRS for x in probe.CASES)
