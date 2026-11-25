"""Stage 27D: regression tests for a data-preserving railway volume patch."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import config as cfg
import decision_journal
import journal_storage as storage
import journal_tail
import killer_audit
import volume_status


def _params(monkeypatch, *, segment=1024, max_compress=4096):
    monkeypatch.setattr(cfg, 'STAGE27D_JOURNAL_PROTECTION', True, raising=False)
    monkeypatch.setattr(cfg, 'STAGE27D_JOURNAL_SEGMENT_BYTES', segment, raising=False)
    monkeypatch.setattr(cfg, 'STAGE27D_AUTO_COMPRESS_MAX_BYTES', max_compress, raising=False)
    monkeypatch.setattr(cfg, 'STAGE27D_COMPRESS_NEW_SEGMENTS', True, raising=False)
    monkeypatch.setattr(cfg, 'STAGE27D_DISK_RESERVE_BYTES', 0, raising=False)


def test_rotation_preserves_all_json_rows_and_recent_crosses_archive(tmp_path, monkeypatch):
    _params(monkeypatch)
    p=tmp_path/'decision_journal.jsonl'
    rows=[{'event_id': f'id-{i}', 'data': 'x'*200} for i in range(25)]
    for row in rows:
        assert storage.append_text(p, json.dumps(row)+'\n')
    archive=storage.archive_files(p)
    assert archive
    assert any(x.name.endswith('.gz') for x in archive)
    assert journal_tail.tail_jsonl(p, 25) == rows
    assert journal_tail.tail_jsonl_fields(p, 5, ('event_id',)) == [{'event_id':f'id-{i}'} for i in range(20,25)]


def test_oversized_existing_file_is_only_renamed_not_rewritten(tmp_path, monkeypatch):
    _params(monkeypatch, segment=1024, max_compress=1024)
    p=tmp_path/'decision_journal.jsonl'
    old=json.dumps({'event_id':'old', 'blob':'o'*5000})+'\n'
    p.write_text(old,encoding='utf8')
    assert storage.append_text(p,json.dumps({'event_id':'new'})+'\n')
    archive=storage.archive_files(p)
    assert len(archive)==1 and archive[0].suffix=='.jsonl'
    assert archive[0].read_text(encoding='utf8')==old
    assert [r['event_id'] for r in journal_tail.tail_jsonl(p, 2)]==['old','new']
    assert p.stat().st_size < 1024


def test_gzip_verification_failure_keeps_plain_source(tmp_path, monkeypatch):
    _params(monkeypatch)
    p=tmp_path/'decision_journal.20261125T200000000001Z.jsonl'
    p.write_text('{"id":1}\n',encoding='utf8')
    # Existing destination: cannot overwrite; original must be untouched.
    p.with_suffix('.jsonl.gz').write_bytes(b'existing')
    assert storage._compress_verified(p) is False
    assert p.read_text(encoding='utf8')=='{"id":1}\n'


def test_no_deletion_when_compression_disabled(tmp_path, monkeypatch):
    _params(monkeypatch)
    monkeypatch.setattr(cfg,'STAGE27D_COMPRESS_NEW_SEGMENTS',False)
    p=tmp_path/'killer_decisions.jsonl'
    p.write_text('y'*1200+'\n')
    storage.append_text(p,'new\n')
    assert storage.archive_files(p)[0].read_text()=='y'*1200+'\n'


def test_critical_and_noncritical_low_space_policy(tmp_path, monkeypatch):
    _params(monkeypatch)
    monkeypatch.setattr(cfg,'STAGE27D_DISK_RESERVE_BYTES',10**30)
    p=tmp_path/'decision_journal.jsonl'
    assert storage.append_text(p,'drop\n') is False
    assert not p.exists()
    assert storage.append_text(p,'sent\n',critical=True)
    assert p.read_text()=='sent\n'


def test_journal_event_dedup_after_restart_across_rotated_boundary(tmp_path, monkeypatch):
    _params(monkeypatch, segment=1024, max_compress=1024)
    monkeypatch.setattr(decision_journal,'_path',lambda:tmp_path/'decision_journal.jsonl')
    path=decision_journal._path()
    old={'event_id':'id-old','status':'BLOCKED','payload':'x'*1800}
    assert storage.append_text(path,json.dumps(old)+'\n')
    assert storage.append_text(path,json.dumps({'event_id':'id-new'})+'\n')
    monkeypatch.setattr(decision_journal,'_SEEN_PATH',None)
    assert decision_journal._append(old) is False
    assert decision_journal._append({'event_id':'id-third','status':'SENT'}) is True
    assert [r['event_id'] for r in journal_tail.tail_jsonl(path,3)] == ['id-old','id-new','id-third']


def test_killer_dedup_and_tail_over_archived_segments(tmp_path, monkeypatch):
    _params(monkeypatch,segment=1024,max_compress=1024)
    monkeypatch.setenv('STATE_DIR',str(tmp_path))
    killer_audit._CACHE.clear(); killer_audit._ORDER.clear()
    big={'id':'old', 'payload':'x'*1800}
    assert killer_audit._write('killer_decisions.jsonl',big)
    assert killer_audit._write('killer_decisions.jsonl',{'id':'new'})
    killer_audit._CACHE.clear(); killer_audit._ORDER.clear()
    assert killer_audit._write('killer_decisions.jsonl',big) is False
    assert [r['id'] for r in killer_audit._tail(tmp_path/'killer_decisions.jsonl',2)] == ['old','new']


def test_storage_report_is_read_only_and_warns_at_80pct(tmp_path, monkeypatch):
    p=tmp_path/'decision_journal.20261125T200000000001Z.jsonl'
    p.write_bytes(b'x'*512)
    monkeypatch.setattr(volume_status.os,'statvfs',lambda base: type('V',(),dict(f_blocks=100,f_frsize=100,f_bavail=18,f_bfree=18))())
    report=volume_status.report_ru(tmp_path)
    assert 'Volume ≥80%' in report
    assert 'части журналов: 1' in report
    assert p.read_bytes()==b'x'*512


def test_storage_off_flag_retains_legacy_append_behavior(tmp_path,monkeypatch):
    _params(monkeypatch)
    monkeypatch.setattr(cfg,'STAGE27D_JOURNAL_PROTECTION',False)
    p=tmp_path/'decision_journal.jsonl'
    p.write_bytes(b'x'*2500)
    assert storage.append_text(p,'ok\n')
    assert p.read_bytes().endswith(b'ok\n') and not storage.archive_files(p)


def test_partial_publish_never_duplicates_same_archive_stamp(tmp_path):
    raw=tmp_path/'decision_journal.20261125T200000000001Z.jsonl'
    raw.write_text('{"event_id":"once"}\n')
    with gzip.open(raw.with_suffix('.jsonl.gz'),'wt') as f:
        f.write(raw.read_text())
    p=tmp_path/'decision_journal.jsonl'
    archived=storage.archive_files(p)
    assert archived == [raw]
    assert journal_tail.tail_jsonl(p,20) == [{'event_id':'once'}]
