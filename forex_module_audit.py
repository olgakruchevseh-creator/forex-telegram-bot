"""Read-only source/test inventory for Forex CI.

Does NOT import trading modules, use network, read secrets, change market
signals or touch Railway volumes. Reports *direct* test imports, not proof of
runtime coverage. For executed line/branch coverage see pytest-cov artifact.
"""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RISK_GROUPS = (
    ("ПУБЛИКАЦИЯ/ВРЕМЯ", ("bot", "analysis", "chart_snapshot", "market_data", "signal_release", "data_quality", "briefing_delivery")),
    ("РЕШЕНИЕ/БЛОКИРОВКИ", ("master", "killer", "signal_contract", "signal_context", "decision", "navigator", "trade_lifecycle", "evidence_families")),
    ("ЖУРНАЛ/КАЛИБРОВКА", ("journal", "replay", "calibration", "ohlc_history", "historical")),
    ("МАКРО/СИЛА ВАЛЮТ", ("news", "macro", "currency_strength", "briefing_strength")),
    ("СЕССИИ/ОТОБРАЖЕНИЕ", ("briefing", "session_projection", "echo", "next_pivot", "zigzag", "levels")),
)


def group(name: str) -> str:
    for label, prefixes in RISK_GROUPS:
        if name.startswith(prefixes):
            return label
    return "ПРОЧИЕ СТРАТЕГИИ/КОНТЕКСТ"


def _imports(tree: ast.AST) -> set[str]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split('.')[0])
    return found


def scan(root: Path = ROOT) -> dict:
    sources = sorted(p for p in root.glob('*.py') if not p.name.startswith('test_') and p.name != 'forex_module_audit.py')
    tests = sorted(root.glob('test_*.py'))
    direct = defaultdict(list)
    importers = defaultdict(list)
    parse_errors = []
    for path in [*sources, *tests]:
        try:
            tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=path.name)
            compile(tree, path.name, 'exec')
        except (SyntaxError, UnicodeError, ValueError) as exc:
            parse_errors.append({'file': path.name, 'reason': str(exc)})
            continue
        for imp in _imports(tree):
            (direct if path in tests else importers)[imp].append(path.name)
    result = []
    for path in sources:
        # A copied Python file with a hyphen is not an importable Python module
        # under its filename: flag it instead of silently treating as coverage.
        name = path.stem
        result.append({
            'file': path.name,
            'group': group(name),
            'importable_name': path.name == name + '.py' and name.isidentifier(),
            'direct_test_count': len(set(direct.get(name, []))),
            'direct_tests': sorted(set(direct.get(name, []))),
            'source_import_count': len(set(importers.get(name, []))),
            'source_importers': sorted(set(importers.get(name, []))),
            'status': 'DIRECT_TEST_IMPORT' if direct.get(name) else ('INDIRECT_OR_UNTESTED' if importers.get(name) else 'NO_STATIC_IMPORT'),
        })
    counts = {
        'source_files': len(sources),
        'test_files': len(tests),
        'directly_referenced_source_files': sum(r['direct_test_count'] > 0 for r in result),
        'source_files_without_direct_test_import': sum(r['direct_test_count'] == 0 for r in result),
        'source_files_without_any_static_import': sum(r['source_import_count'] == 0 for r in result),
        'nonimportable_filename_count': sum(not r['importable_name'] for r in result),
        'syntax_or_parse_errors': len(parse_errors),
    }
    return {'generated_utc': datetime.now(timezone.utc).isoformat(), 'counts': counts,
            'modules': result, 'parse_errors': parse_errors,
            'scope_note': 'Прямой импорт из теста НЕ равен проверке всех ветвей кода; косвенный импорт тоже не гарантирует покрытия.'}


def markdown(report: dict) -> str:
    c=report['counts']
    lines=['# Forex — автоматическая матрица покрытия модулей', '',
           'Снимок: '+report['generated_utc'], '',
           f"Исходных Python-файлов: **{c['source_files']}** · тестовых файлов: **{c['test_files']}**.",
           f"Прямо упомянуты в тестовых импортах: **{c['directly_referenced_source_files']}**; без прямого импорта: **{c['source_files_without_direct_test_import']}**.",
           f"Ошибок синтаксиса / разбора: **{c['syntax_or_parse_errors']}**.", '',
           '> Статический прямой импорт — лишь показатель наличия явной привязки теста. '
           'Для фактически исполненных строк и ветвей смотрите coverage.xml в артефактах GitHub Actions.','',
           '| Файл | Группа | Прямых тестовых файлов | Исходных импортёров | Статус |',
           '|---|---|---:|---:|---|']
    for row in report['modules']:
        name=row['file'].replace('|','\\|')
        lines.append(f"| `{name}` | {row['group']} | {row['direct_test_count']} | {row['source_import_count']} | {row['status']} |")
    lines.extend(['','## Приоритеты',
                  '1. Убедиться, что критические H1/M15/M5 операции используют только закрытые свечи; проверять сбои, не только штатный путь.',
                  '2. Проверять межмодульные конфликты H4/H1, независимо от итогового числа успешных тестов.',
                  '3. Проверять idempotency журналов/уведомлений при повторном запуске и недоступности данных.',
                  '4. Отдельно измерять walk-forward-полезность сигналов; unit-тесты не являются оценкой прибыльности.',
                  '', '## Отсутствует прямой импорт из тестов', ''])
    lines.extend('- `'+r['file']+'` (импортируют исходные модули: '+str(r['source_import_count'])+')'
                 for r in report['modules'] if r['direct_test_count']==0)
    if report['parse_errors']:
        lines.extend(['','## Синтаксические ошибки'] + [f"- `{x['file']}`: {x['reason']}" for x in report['parse_errors']])
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description='Create read-only Forex module audit matrix')
    parser.add_argument('--json', default='forex_module_audit.json')
    parser.add_argument('--markdown', default='forex_module_audit.md')
    args=parser.parse_args()
    report=scan()
    Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2),encoding='utf-8')
    Path(args.markdown).write_text(markdown(report),encoding='utf-8')
    for name, value in report['counts'].items():
        print(f'{name}={value}')
    print(f'Отчёты: {args.json}, {args.markdown}')
    if report['parse_errors']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
