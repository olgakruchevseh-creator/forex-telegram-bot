"""Summarise GitHub Actions pytest-cov JSON alongside source/test inventory.

Read-only: never import trading modules, read API keys or modify bot state.
Coverage is executed lines/branches, NOT evidence of profitable signals.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from forex_module_audit import scan, group

ROOT = Path(__file__).resolve().parent
CRITICAL = (
    'analysis.py', 'bot.py', 'chart_snapshot.py', 'market_data_coherence.py',
    'data_quality_guard.py', 'signal_contract.py', 'signal_release_guard.py',
    'master_direction.py', 'killer_engine.py', 'killer_trap_guard.py',
    'currency_strength_math.py', 'multi_tf_currency_strength.py',
    'patterns.py', 'zigzag_scanner.py', 'levels.py', 'news.py',
    'ohlc_history_journal.py', 'historical_module_replay.py',
    'signal_journal.py', 'decision_journal.py', 'replay_calibration.py',
)


def _coverage_files(raw: dict) -> dict[str, dict]:
    """Map relative source file names to summaries; no fabricated 0 coverage."""
    result = {}
    for name, info in (raw.get('files') or {}).items():
        # The project currently stores all Python sources in the root.
        key = Path(name).name
        if not key.endswith('.py') or key.startswith('test_'):
            continue
        if key not in result or name == key:
            result[key] = info.get('summary') or {}
    return result


def render(coverage: dict, inventory: dict) -> str:
    files = _coverage_files(coverage)
    total = coverage.get('totals') or {}
    lines = [
        '# Forex — фактическое покрытие тестами', '',
        'Отчёт по **выполненным строкам и ветвям** (из pytest-cov), а не по упоминанию модулей в тестах.',
        'Покрытие **не** измеряет прибыльность, достоверность котировок либо полноту сценариев.', '',
        f"Исходных файлов по инвентаризации: **{inventory['counts']['source_files']}**; "
        f"тестовых: **{inventory['counts']['test_files']}**.",
        f"Глобальное покрытие, указанное pytest-cov: **{total.get('percent_covered', 'нет данных')}%**.",
        f"Исходных файлов, найденных в JSON: **{sum(m['file'] in files for m in inventory['modules'])}**. "
        'Отсутствующий в JSON файл обозначается «НЕ ИЗМЕРЕНО», а не 0%.', '',
        '## Критические компоненты', '',
        '| Модуль | Строки, % | Ветви, % | Прямых файлов тестов |',
        '|---|---:|---:|---:|',
    ]
    inventory_by_name = {row['file']: row for row in inventory['modules']}

    def format_row(name: str) -> str:
        summary = files.get(name)
        if summary is None:
            text = 'НЕ ИЗМЕРЕНО'; branches = 'НЕ ИЗМЕРЕНО'
        else:
            stmts = int(summary.get('num_statements') or 0)
            covered = int(summary.get('covered_lines') or 0)
            text = f'{(100 * covered / stmts):.1f}%' if stmts else 'нет исполняемых строк'
            branch_total = int(summary.get('num_branches') or 0)
            branch_covered = int(summary.get('covered_branches') or 0)
            branches = f'{(100 * branch_covered / branch_total):.1f}%' if branch_total else 'нет ветвей'
        return f"| `{name}` | {text} | {branches} | {inventory_by_name.get(name, {}).get('direct_test_count', 0)} |"

    lines.extend(format_row(name) for name in CRITICAL)
    lines.extend(['', '## Все исходные модули', '',
                  '| Исходный файл | Группа | Строки, % | Ветви, % |',
                  '|---|---|---:|---:|'])
    for row in inventory['modules']:
        name = row['file']
        summary = files.get(name)
        if summary is None:
            pc, pb = 'НЕ ИЗМЕРЕНО', 'НЕ ИЗМЕРЕНО'
        else:
            total_st = int(summary.get('num_statements') or 0)
            cover_st = int(summary.get('covered_lines') or 0)
            pc = f'{100*cover_st/total_st:.1f}%' if total_st else 'н/п'
            total_br = int(summary.get('num_branches') or 0)
            cover_br = int(summary.get('covered_branches') or 0)
            pb = f'{100*cover_br/total_br:.1f}%' if total_br else 'н/п'
        lines.append(f"| `{name}` | {row['group']} | {pc} | {pb} |")
    lines.extend(['', '## Интерпретация',
                  '- **Низкое покрытие:** требуется проверить, какие именно ветви не исполнялись. Не менять фильтры только ради процента.',
                  '- **Высокое покрытие:** не гарантирует правильности логики или отсутствия look-ahead.',
                  '- **НЕ ИЗМЕРЕНО:** не приравнивать к 0%; сначала уточнить конфигурацию измерения.',
                  '- Сценарии отказа Telegram, API и Railway проверять отдельно моками и безопасными тестами восстановления.',
                  '- `signal_context-1.py` — неимпортируемое имя резервной копии: не считать его активным модулем без анализа точек входа.',
                  '', 'Полные сведения по строкам в `coverage.xml` и `coverage.json`.', ''])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--coverage-json', default='coverage.json')
    parser.add_argument('--output', default='FOREX_TEST_COVERAGE_RU.md')
    args = parser.parse_args()
    report = scan(ROOT)
    cpath = Path(args.coverage_json)
    if not cpath.is_file():
        raise SystemExit(f'Не найден отчёт pytest-cov: {cpath}')
    raw = json.loads(cpath.read_text(encoding='utf-8'))
    text = render(raw, report)
    Path(args.output).write_text(text, encoding='utf-8')
    print(f"Глобальное покрытие pytest-cov: {(raw.get('totals') or {}).get('percent_covered', 'неизвестно')}%")
    print(f'Готов отчёт: {args.output}')


if __name__ == '__main__':
    main()
