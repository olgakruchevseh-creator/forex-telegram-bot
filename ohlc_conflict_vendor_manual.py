"""Manual, bounded Twelve Data follow-up of explicitly chosen conflicted H1.

The dry run uses ZERO API calls. Execution requires two deliberate flags and
at most 3 H1 labels. A new response from the *same vendor* cannot certify
which OHLC was historically available to the bot.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import sys

import config as cfg
from ohlc_history_journal import db_default
from ohlc_vendor_probe import audit_once

MAX_REQUESTS=3
REPORT_NAME='ohlc_vendor_conflicts_manual_observe.json'
MAX_SAVED_CASES=128


def _label(value: str, now: datetime) -> str:
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('нужна метка с UTC Z')
        dt=parsed.astimezone(timezone.utc)
        if dt.minute or dt.second or dt.microsecond or dt+timedelta(hours=1)>now:
            raise ValueError('нужна закрытая H1 по границе UTC-часа')
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    except (ValueError,TypeError,OverflowError) as exc:
        raise ValueError('неверная UTC-метка закрытой H1') from exc


def check(*, pair: str, timestamps: list[str], db_path: Path,
          execute: bool = False, approve_api_requests: bool = False,
          api_key: str = '', session=None, as_of:datetime|None=None,
          out_path:Path|None=None) -> dict:
    now=as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('нужно UTC-время')
    now=now.astimezone(timezone.utc)
    if pair not in cfg.PAIRS:
        raise ValueError('неподдерживаемая валютная пара')
    if not 1<=len(timestamps)<=MAX_REQUESTS:
        raise ValueError('разрешено 1–3 H1-метки за запуск')
    normalized=tuple(_label(ts,now) for ts in timestamps)
    if len(set(normalized))!=len(normalized):
        raise ValueError('повторные H1-метки запрещены')
    archive=Path(db_path)
    if not archive.is_file():
        raise ValueError('SQLite не найден')
    preview={'mode':'PLAN_ONLY','pair':pair,'timestamps':normalized,
             'max_api_requests':len(normalized),'new_requests':0,'changes_to_trading':False}
    if not execute:
        return preview
    if not approve_api_requests:
        raise ValueError('не подтверждены API-запросы')
    if not api_key.strip():
        raise ValueError('TWELVE_DATA_API_KEY не указан')
    cases=tuple((pair,'H1',t,'MANUAL_CONFLICT_RECHECK') for t in normalized)
    created_session=False
    if session is None:
        import requests
        session=requests.Session()
        created_session=True
    try:
        doc=audit_once(db_path=archive, session=session, api_key=api_key, now=now, cases=cases)
    finally:
        if created_session:
            session.close()
    # Retain prior per-hour checks across batches. The old implementation
    # replaced the complete report every time, losing evidence from earlier
    # explicit runs. Keep the latest observation per pair/TF/hour, bounded.
    dest=Path(out_path or archive.with_name(REPORT_NAME))
    history={}
    try:
        if dest.is_file() and dest.stat().st_size <= 250_000:
            old_doc=json.loads(dest.read_text(encoding='utf-8'))
            if isinstance(old_doc,dict) and old_doc.get('mode')=='OBSERVE_ONLY':
                for x in old_doc.get('cases',[]):
                    if isinstance(x,dict) and all(x.get(k) for k in ('pair','tf','archive_label_utc')):
                        key=(x['pair'],x['tf'],x['archive_label_utc'])
                        history[key]=x
    except (OSError,ValueError,TypeError,UnicodeError):
        history={}
    for item in doc['cases']:
        item=dict(item)
        item['checked_utc']=doc['requested_utc']
        history[(item['pair'],item['tf'],item['archive_label_utc'])]=item
    merged={**doc,'cases':list(history.values())[-MAX_SAVED_CASES:],
            'last_batch_size':len(doc['cases']),
            'note':'Каждая запись — последняя повторная проверка часа; старые OHLC не переписаны.'}
    dest.parent.mkdir(parents=True,exist_ok=True)
    staging=dest.with_name(dest.name+'.tmp')
    staging.write_text(json.dumps(merged,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(staging,dest)
    return {'mode':'EXECUTED','pair':pair,'timestamps':normalized,
            'max_api_requests':len(cases),'new_requests':sum(x['status'] not in
               ('ARCHIVE_BAR_MISSING','BAR_NOT_YET_CLOSED') for x in doc['cases']),
            'statuses':[{'utc':x['archive_label_utc'],'status':x['status']}
                        for x in doc['cases']], 'changes_to_trading':False,
            'saved_comparisons':len(merged['cases'])}


def main(argv=None)->int:
    ap=argparse.ArgumentParser(description='Ручная проверка 1–3 спорных закрытых H1')
    ap.add_argument('--pair',required=True)
    ap.add_argument('--timestamps',nargs='+',required=True)
    ap.add_argument('--db',type=Path,default=db_default())
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--approve-api-requests',action='store_true')
    args=ap.parse_args(argv)
    try:
        result=check(pair=args.pair,timestamps=args.timestamps,db_path=args.db,
                     execute=args.execute,approve_api_requests=args.approve_api_requests,
                     api_key=os.getenv('TWELVE_DATA_API_KEY',''))
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (ValueError,OSError,RuntimeError) as exc:
        # Vendor exceptions and API keys are never printed.
        print('Проверка H1 недоступна: '+(str(exc)[:150] if isinstance(exc,ValueError) else type(exc).__name__),
              file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
