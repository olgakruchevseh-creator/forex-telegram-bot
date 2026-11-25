"""Volume inventory is read-only, bounded and reports real files."""
from pathlib import Path

import volume_status as v


def test_inventory_counts_and_ranks_files_without_modifying_them(tmp_path):
    (tmp_path / 'killer_decisions.jsonl').write_bytes(b'x' * 300)
    (tmp_path / 'ohlc.sqlite').write_bytes(b'x' * 200)
    folder = tmp_path / 'pattern_chart_cache'
    folder.mkdir()
    (folder / 'orphan.png').write_bytes(b'x' * 100)
    result = v.inventory(tmp_path, top_n=2)
    assert result['file_count'] == 3 and result['file_bytes'] == 600
    assert [n for _, n in result['top']] == ['killer_decisions.jsonl', 'ohlc.sqlite']
    assert (tmp_path / 'killer_decisions.jsonl').stat().st_size == 300
    assert (folder / 'orphan.png').exists()


def test_inventory_respects_time_or_count_limit(tmp_path):
    for i in range(4):
        (tmp_path / f'{i}.jsonl').write_text('a')
    data = v.inventory(tmp_path, max_files=2)
    assert data['file_count'] == 2 and data['limited'] is True


def test_inventory_skips_symlink_escape(tmp_path):
    external = tmp_path.parent / (tmp_path.name + '-outside.txt')
    external.write_bytes(b'not included')
    (tmp_path / 'outside-link').symlink_to(external)
    assert v.inventory(tmp_path)['file_count'] == 0
    external.unlink()


def test_unconfigured_state_is_explained(monkeypatch):
    monkeypatch.delenv('STATE_DIR', raising=False)
    monkeypatch.delenv('RAILWAY_VOLUME_MOUNT_PATH', raising=False)
    assert 'не заданы' in v.report_ru()
