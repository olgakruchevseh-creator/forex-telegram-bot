"""Unit tests of offline coverage reporting: no Telegram or market connection."""
from forex_coverage_report import _coverage_files, render


def test_coverage_parser_filters_tests_and_preserves_unmeasured():
    raw = {'files': {
        'analysis.py': {'summary': {'num_statements': 20, 'covered_lines': 10,
                                    'num_branches': 8, 'covered_branches': 2}},
        'test_analysis.py': {'summary': {'num_statements': 100, 'covered_lines': 100}},
    }, 'totals': {'percent_covered': 50}}
    records = _coverage_files(raw)
    assert 'analysis.py' in records and 'test_analysis.py' not in records
    inv = {'counts': {'source_files': 2, 'test_files': 1}, 'modules': [
        {'file': 'analysis.py', 'group': 'A', 'direct_test_count': 1},
        {'file': 'bot.py', 'group': 'B', 'direct_test_count': 0},
    ]}
    md = render(raw, inv)
    assert '| `analysis.py` | 50.0% | 25.0% | 1 |' in md
    assert '| `bot.py` | НЕ ИЗМЕРЕНО | НЕ ИЗМЕРЕНО | 0 |' in md
    assert 'не измерено' in md.lower()
