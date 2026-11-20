"""Opt-in, single-window, H1-only historical backfill for seven FX pairs.

NO automatic scheduling, Telegram calls, trading changes, silent API requests,
or overwrites of stored candles. Dry-run is strictly read-only. Explicit execution
stages/validates all seven vendor pages BEFORE one SQLite transaction.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys

import config as cfg
from ohlc_history_journal import db_default, iso, validate_row, _request_page
from patterns_history_integrity import definitely_trading_fx

MAX_DAYS = 7


def plan(*, start: date, end: date, db_path: Path, as_of: datetime | None = None) -> dict:
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('требуется timezone-aware UTC')
    if start > end or (end-start).days >= MAX_DAYS or end >= now.date():
        raise ValueError('разрешено не более 7 завершённых календарных дней в прошлом')
    beginning=datetime.combine(start, datetime.min.time(),tzinfo=timezone.utc)
    end_exclusive=datetime.combine(end+timedelta(days=1),datetime.min.time(),tzinfo=timezone.utc)
    expected=sum(definitely_trading_fx(beginning+timedelta(hours=i))
                 for i in range(int((end_exclusive-beginning)/timedelta(hours=1))))
    if not expected:
        raise ValueError('пилот только для окон с основными торговыми часами')
    if not Path(db_path).is_file():
        raise ValueError('постоянный SQLite-архив не найден')
    # Preflight without creating/altering the database.
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('SELECT opened_utc FROM candles LIMIT 1').fetchone()
    return {'start':start.isoformat(),'end':end.isoformat(), 'days':(end-start).days+1,
            'pair_count':len(cfg.PAIRS), 'max_api_requests':len(cfg.PAIRS),
            'expected_core_hours_per_pair':expected,
            'mode':'PLAN_ONLY', 'trade_effect':False, 'overwrites':False}


def run(*, start:date, end:date, db_path:Path, session=None,
        api_key:str='', execute:bool=False, approve_api_requests:bool=False,
        as_of:datetime|None=None) -> dict:
    now=as_of or datetime.now(timezone.utc)
    result=plan(start=start,end=end,db_path=db_path,as_of=now)
    if not execute:
        return result
    if not approve_api_requests:
        raise ValueError('нужно явно подтвердить запросы Twelve Data')
    if not api_key.strip():
        raise ValueError('нет TWELVE_DATA_API_KEY')
    created_session=False
    if session is None:
        import requests
        session=requests.Session()
        created_session=True
    beginning=datetime.combine(start,datetime.min.time(),tzinfo=timezone.utc)
    end_exclusive=datetime.combine(end+timedelta(days=1),datetime.min.time(),tzinfo=timezone.utc)
    staged={}
    try:
        # NO DB changes unless all seven requests and structural validation pass.
        for pair in cfg.PAIRS:
            values=_request_page(session,pair,'H1',beginning,end_exclusive,api_key,timeout=20)
            normalized={}
            for row in values:
                stamp,ohlc=validate_row(row,'H1',now)
                dt=datetime.fromisoformat(stamp.replace('Z','+00:00'))
                if not beginning <= dt < end_exclusive:
                    continue
                if stamp in normalized:
                    raise ValueError('поставщик вернул дублирующую H1-метку')
                normalized[stamp]=ohlc
            if not normalized:
                raise ValueError(f'{pair}: пустой ответ в указанном периоде')
            if len(normalized)>MAX_DAYS*24:
                raise ValueError('слишком много свечей на одну пару')
            missing=[]
            for n in range(int((end_exclusive-beginning)/timedelta(hours=1))):
                stamp=beginning+timedelta(hours=n)
                if definitely_trading_fx(stamp) and iso(stamp) not in normalized:
                    missing.append(stamp)
            if missing:
                raise ValueError(f'{pair}: не получено {len(missing)} основных торговых H1; '
                                 'корзина отклонена до записи SQLite')
            staged[pair]=normalized
    finally:
        if created_session:
            session.close()
    db_path=Path(db_path)
    added=identical=quarantined=0
    # One transaction for the complete basket. Original OHLC never updated.
    with closing(sqlite3.connect(db_path,timeout=15)) as db:
        db.execute('PRAGMA busy_timeout=15000')
        try:
            db.execute('BEGIN IMMEDIATE')
            for pair,series in staged.items():
                pair_inserted=pair_identical=pair_conflicts=0
                for stamp,values in sorted(series.items()):
                    old=db.execute('SELECT open,high,low,close FROM candles WHERE pair=? AND tf=? AND opened_utc=?',
                                   (pair,'H1',stamp)).fetchone()
                    if old is None:
                        db.execute('INSERT INTO candles VALUES(?,?,?,?,?,?,?,?,?,?)',
                                   (pair,'H1',stamp,*values,'Twelve Data',iso(now),iso(now)))
                        added+=1
                        pair_inserted+=1
                    elif all(abs(a-b) <= max(1e-12,abs(a)*1e-13) for a,b in zip(old,values)):
                        identical+=1
                        pair_identical+=1
                    else:
                        quarantined+=1
                        pair_conflicts+=1
                        db.execute('INSERT OR IGNORE INTO revisions '
                                   '(pair,tf,opened_utc,old_ohlc_json,new_ohlc_json,observed_utc,action) '
                                   'VALUES(?,?,?,?,?,?,?)',
                                   (pair,'H1',stamp,json.dumps(old),json.dumps(values),iso(now),'QUARANTINED'))
                db.execute('INSERT INTO ingest_runs '
                           '(created_utc,source,pair,tf,start_utc,end_utc,received,inserted,identical,conflicts,input_sha256) '
                           'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (iso(now),'MANUAL_H1_PILOT_TWELVE_DATA',pair,'H1',
                            min(series),max(series),len(series),pair_inserted,
                            pair_identical,pair_conflicts,None))
            db.commit()
        except Exception:
            db.rollback()
            raise
    return {**result,'mode':'EXECUTED', 'received':sum(map(len,staged.values())),
            'inserted':added,'identical':identical,'quarantined_conflicts':quarantined,
            'overwrites':False}


def main(argv=None)->int:
    parser=argparse.ArgumentParser(description='H1: ограниченная ручная дозагрузка; без автоматической торговли')
    parser.add_argument('--db',type=Path,default=db_default())
    parser.add_argument('--start-date',type=date.fromisoformat,required=True)
    parser.add_argument('--end-date',type=date.fromisoformat,required=True)
    parser.add_argument('--execute',action='store_true',help='явно разрешить запись архивных H1')
    parser.add_argument('--approve-api-requests',action='store_true',help='явно разрешить до 7 запросов к Twelve Data')
    args=parser.parse_args(argv)
    try:
        result=run(start=args.start_date,end=args.end_date,db_path=args.db,
                   api_key=os.getenv('TWELVE_DATA_API_KEY',''),execute=args.execute,
                   approve_api_requests=args.approve_api_requests)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (ValueError,OSError,sqlite3.Error,RuntimeError) as exc:
        # Never include credentials or request URLs in output.
        print('Ошибка H1-пилота: '+(str(exc)[:180] if isinstance(exc,ValueError) else type(exc).__name__),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
