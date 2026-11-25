"""Stage 27E: all destructive transitions require external byte-for-byte proof."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

import config
import journal_tail
import storage_archive_27e as e


class Response:
    def __init__(self, status=200, data=b''):
        self.status_code = status
        self.data = data
        self.closed = False

    def iter_content(self, chunk_size=None):
        for i in range(0, len(self.data), 149):
            yield self.data[i:i + 149]

    def close(self):
        self.closed = True


def source(tmp_path, alias='decision', count=1500):
    base = e._ALLOWED[alias]
    p = tmp_path / (base[:-6] + '.20261125T200020518121Z.jsonl')
    rows = [{'event_id': f'id-{i}', 'blob': 'x' * 250} for i in range(count)]
    payload = ''.join(json.dumps(x) + '\n' for x in rows).encode('utf8')
    p.write_bytes(payload)
    return p, payload, rows


def patch_remote(monkeypatch, data: bytes, *, get_data=None, fail_put=False):
    import requests
    outgoing = []

    def put(url, data, **kw):
        assert url.startswith('https://')
        outgoing.append(data.read())
        return Response(503 if fail_put else 200)

    def get(url, **kw):
        assert url.startswith('https://')
        return Response(200, data if get_data is None else get_data)

    monkeypatch.setenv('STAGE27E_DECISION_PUT_URL', 'https://example.org/signed-put?secret=TEST')
    monkeypatch.setenv('STAGE27E_DECISION_GET_URL', 'https://example.org/signed-get?secret=TEST')
    monkeypatch.setattr(requests, 'put', put)
    monkeypatch.setattr(requests, 'get', get)
    return outgoing


def test_opt_in_requires_external_url_no_deletion(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    p, payload, _ = source(tmp_path)
    monkeypatch.delenv('STAGE27E_DECISION_PUT_URL', raising=False)
    with pytest.raises(e.SafetyAbort, match='HTTPS'):
        e.run('export', 'decision')
    with pytest.raises(e.SafetyAbort, match='внешней копии'):
        e.run('compact', 'decision')
    assert p.read_bytes() == payload and not p.with_suffix('.jsonl.gz').exists()


def test_full_external_get_sha_verified_then_compact(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    monkeypatch.setattr(e, '_disk_free', lambda root: 1_000_000_000)
    p, payload, rows = source(tmp_path)
    outgoing = patch_remote(monkeypatch, payload)
    text = e.run('export', 'decision')
    assert 'SHA-256' in text
    assert outgoing == [payload]
    assert p.is_file()
    mark = e._verified_manifest(tmp_path, p)
    assert mark['sha256'] == hashlib.sha256(payload).hexdigest()
    assert 'secret' not in str(mark)
    text = e.run('compact', 'decision')
    assert 'Освобождено' in text
    assert not p.exists() and p.with_suffix('.jsonl.gz').is_file()
    assert journal_tail.tail_jsonl(tmp_path/'decision_journal.jsonl', len(rows)) == rows


def test_mismatch_remote_get_rejects_export_and_keeps_source(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    p, payload, _ = source(tmp_path)
    patch_remote(monkeypatch, payload, get_data=payload + b'tamper')
    with pytest.raises(e.SafetyAbort, match='длиннее'):
        e.run('export', 'decision')
    assert p.read_bytes() == payload
    assert not e._mark_path(tmp_path, p).exists()


def test_bad_checksum_same_length_rejects(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    p, payload, _ = source(tmp_path)
    patch_remote(monkeypatch, payload, get_data=b'a' + payload[1:])
    with pytest.raises(e.SafetyAbort, match='SHA-256'):
        e.run('export', 'decision')
    assert p.read_bytes() == payload


def test_non_success_put_keeps_file(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    p, payload, _ = source(tmp_path)
    patch_remote(monkeypatch, payload, fail_put=True)
    with pytest.raises(e.SafetyAbort, match='PUT'):
        e.run('export', 'decision')
    assert p.read_bytes() == payload


def test_no_delete_if_source_changed_after_export(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    p, payload, _ = source(tmp_path)
    patch_remote(monkeypatch, payload)
    e.run('export', 'decision')
    p.write_bytes(payload + b'\n')
    with pytest.raises(e.SafetyAbort, match='изменился'):
        e.run('compact', 'decision')
    assert p.exists()


def test_disk_budget_rejects_uncompressible_or_low_disk(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    p, payload, _ = source(tmp_path)
    patch_remote(monkeypatch, payload)
    e.run('export', 'decision')
    monkeypatch.setattr(e, '_disk_free', lambda root: 310_000_000)
    with pytest.raises(e.SafetyAbort, match='Недостаточно'):
        e.run('compact', 'decision')
    assert p.read_bytes() == payload


def test_corrupt_local_gzip_preserves_raw_after_external_verification(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    p, payload, _ = source(tmp_path)
    patch_remote(monkeypatch, payload)
    e.run('export', 'decision')
    p.with_suffix('.jsonl.gz').write_bytes(b'broken-gzip')
    with pytest.raises(e.SafetyAbort, match='gzip'):
        e.run('compact', 'decision')
    assert p.read_bytes() == payload


def test_resume_after_verified_gzip_publish_before_raw_unlink(tmp_path, monkeypatch):
    import gzip
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    p, payload, _ = source(tmp_path)
    patch_remote(monkeypatch, payload)
    e.run('export', 'decision')
    with gzip.open(p.with_suffix('.jsonl.gz'), 'wb') as f:
        f.write(payload)
    assert 'Освобождено' in e.run('compact', 'decision')
    assert not p.exists()
    assert p.with_suffix('.jsonl.gz').exists()


def test_only_whitelisted_timestamped_legacy_files_selected(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    unknown = tmp_path / 'personal.20261125T200020518121Z.jsonl'
    unknown.write_bytes(b'must not touch')
    active = tmp_path / 'decision_journal.jsonl'
    active.write_bytes(b'active')
    p, _, _ = source(tmp_path)
    assert e._legacy(tmp_path, 'decision') == p
    with pytest.raises(e.SafetyAbort):
        e._legacy(tmp_path, '../personal')
    assert active.read_bytes() == b'active' and unknown.read_bytes() == b'must not touch'


def test_status_is_read_only_and_no_auto_export(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    p, payload, _ = source(tmp_path)
    result = e.status()
    assert 'НЕ подтверждена' in result
    assert p.read_bytes() == payload
    assert not (tmp_path / '.stage27e_verified').exists()


def test_feature_flag_blocks_manual_commands(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    monkeypatch.setattr(config, 'STAGE27E_ENABLE_MANUAL_OPERATIONS', False)
    p, payload, _ = source(tmp_path)
    with pytest.raises(e.SafetyAbort, match='отключён'):
        e.run('compact', 'decision')
    assert p.read_bytes() == payload


def test_second_process_operation_lock_rejects(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    with e._exclusive(tmp_path):
        with pytest.raises(e.SafetyAbort, match='Уже выполняется'):
            with e._exclusive(tmp_path):
                pass


def test_bot_command_exposes_owner_only_confirmed_handler():
    src = (Path(__file__).parent / 'bot.py').read_text(encoding='utf8')
    assert 'CommandHandler("storage_archive", _owner_handler(cmd_storage_archive))' in src
    assert "if not _owner_allowed(update):" in src
    assert "args[2] != 'CONFIRM'" in src


def test_large_gzip_hot_tail_avoids_full_decompression(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    monkeypatch.setattr(e, '_disk_free', lambda root: 1_000_000_000)
    p, payload, rows = source(tmp_path)
    patch_remote(monkeypatch, payload)
    e.run('export', 'decision')
    e.run('compact', 'decision')
    gz = p.with_suffix('.jsonl.gz')
    assert e._stat(gz)[0] > 0
    assert journal_tail._verified_fast_tail(gz, 100) is not None
    def forbid_full_gzip(*args, **kwargs):
        raise AssertionError('Full gzip decode should not be used')
    monkeypatch.setattr(journal_tail.gzip, 'open', forbid_full_gzip)
    assert journal_tail.tail_jsonl(tmp_path/'decision_journal.jsonl', 100) == rows[-100:]


def test_tampered_hot_tail_falls_back_to_authoritative_gzip(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    monkeypatch.setattr(e, '_disk_free', lambda root: 1_000_000_000)
    p, payload, rows = source(tmp_path)
    patch_remote(monkeypatch, payload)
    e.run('export', 'decision')
    e.run('compact', 'decision')
    gz = p.with_suffix('.jsonl.gz')
    sidecar = gz.with_name(gz.name + '.tail.jsonl')
    sidecar.write_bytes(b'garbled!')
    assert journal_tail._verified_fast_tail(gz, 4) is None
    assert journal_tail.tail_jsonl(tmp_path/'decision_journal.jsonl', 4) == rows[-4:]
