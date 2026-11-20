"""Stage 2N: the read-only Telegram preview has no vendor-client dependency."""
import ast
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import builtins

from analysis import Candle
from ohlc_history_journal import init_db
from ohlc_conflict_verify_workflow import plan, format_plan

NOW=datetime(2026,11,20,20,0,tzinfo=timezone.utc)


def test_preview_import_does_not_load_manual_vendor_client():
    source=Path(__file__).with_name('ohlc_conflict_verify_workflow.py').read_text(encoding='utf-8')
    tree=ast.parse(source)
    # Prove there is no eager import of the API-only component.
    eager=[node for node in tree.body if isinstance(node,ast.ImportFrom)
           and node.module=='ohlc_conflict_vendor_manual']
    assert eager==[]
    execute=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='execute')
    assert any(isinstance(n,ast.ImportFrom) and n.module=='ohlc_conflict_vendor_manual'
               for n in ast.walk(execute))


def test_preview_works_without_vendor_import_and_without_api_calls(tmp_path):
    path=tmp_path/'journal.sqlite'
    with init_db(path) as db:
        db.execute('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?)',(
            'EUR/USD','H1','2026-11-16T13:00:00Z',1.1,1.2,1.0,1.1100,
            'TEST','2026-11-16T14:00:00Z','2026-11-16T14:00:00Z'))
    cache=[Candle('2026-11-16T13:00:00Z',1.1,1.2,1.0,1.1101)]
    original=builtins.__import__
    def forbid_vendor(name,*args,**kwargs):
        if name=='ohlc_conflict_vendor_manual':
            raise ImportError('vendor client deliberately unavailable')
        return original(name,*args,**kwargs)
    with patch('builtins.__import__',side_effect=forbid_vendor):
        result=plan('EUR/USD', cache=cache, db_path=path, as_of=NOW)
        message=format_plan(result)
    assert result['state']=='OK' and result['api_requests']==1
    assert '1. ' in message and 'Twelve Data' in message


def test_bot_includes_safe_single_line_error_diagnostic():
    code=Path(__file__).with_name('bot.py').read_text(encoding='utf-8')
    ast.parse(code)
    section=code.split('async def cmd_ohlc_verify(',1)[1].split('async def cmd_patterns_formation(',1)[0]
    assert 'OHLC_VERIFY_COMMAND_FAILED pair=%s phase=%s error_type=%s' in section
    assert 'type(exc).__name__' in section
    assert "phase = 'PLAN_READ_ONLY'" in section
    assert 'ПОДТВЕРЖДАЮ' in section
    assert 'str(exc)' not in section  # don't log secrets
