"""Evidence-driven Stage 5 presentation / observer regressions. Trade gates unchanged."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sqlite3

from analysis import Candle
import briefing_delivery
import briefing_level_watch as watch
import briefing
import signal_navigator
from telegram_locale import localize_telegram

START=datetime(2026,11,23,8,tzinfo=timezone.utc)
PUB=START+timedelta(hours=1,minutes=5)

def bar(hour, o, hi, lo, cl):
    return Candle((START+timedelta(hours=hour)).strftime('%Y-%m-%d %H:%M:%S'),o,hi,lo,cl)

def setup(tmp_path, kind='support', lo=1.099, hi=1.100):
    f=tmp_path/'w.sqlite3'
    row={'label':'S1' if kind=='support' else 'R1','low':lo,'high':hi}
    snap={'as_of_h1':bar(0,1.1,1.1,1.1,1.1).dt,'current_price':1.1010,
          'supports':[row] if kind=='support' else [],
          'resistances':[row] if kind=='resistance' else [],
          'current_zones':[]}
    assert watch.register_briefing('s','EUR/USD',snap,published_at=PUB,path=f)==1
    return f

def events(f):
    with sqlite3.connect(f) as db:
        return [t[0] for t in db.execute('SELECT report FROM watch_events ORDER BY h1, rowid')]

def test_no_invented_new_break_when_price_already_below_support(tmp_path):
    f=setup(tmp_path)
    candles=[bar(1,1.0968,1.0980,1.0950,1.0968),
             bar(2,1.0968,1.0970,1.0950,1.0960),
             bar(3,1.0960,1.0962,1.0940,1.0950)]
    watch.observe_market({'EUR/USD':{'H1':candles}},now=START+timedelta(hours=5),path=f)
    r='\n'.join(events(f))
    assert 'Удержание цены ниже поддержки' in r
    assert 'Подтверждённый новый пробой' not in r

def test_no_false_return_when_price_remains_below_zone(tmp_path):
    f=setup(tmp_path)
    candles=[bar(1,1.1009,1.101,1.0995,1.1005),
             bar(2,1.1005,1.1006,1.0960,1.0970),
             bar(3,1.0970,1.0975,1.0950,1.0960)]
    # First genuine crossing, two consecutive directional closes.
    watch.observe_market({'EUR/USD':{'H1':candles}},now=START+timedelta(hours=5),path=f)
    assert any('Подтверждённый новый пробой' in x for x in events(f))
    assert not any('Неудачный пробой' in x for x in events(f))

def test_bearish_attempt_followed_by_green_below_support_is_not_false_return(tmp_path):
    f=setup(tmp_path)
    candles=[bar(1,1.1009,1.101,1.0995,1.1005),
             bar(2,1.1005,1.1006,1.0960,1.0970),
             bar(3,1.0960,1.0985,1.0959,1.0980)]
    watch.observe_market({'EUR/USD':{'H1':candles}},now=START+timedelta(hours=5),path=f)
    assert not any('Неудачный пробой' in x for x in events(f))

def test_actual_return_after_attempt_is_labeled_correctly(tmp_path):
    f=setup(tmp_path)
    candles=[bar(1,1.1009,1.101,1.0995,1.1005),
             bar(2,1.1005,1.1006,1.0960,1.0970),
             bar(3,1.0970,1.0998,1.0968,1.0995)]
    watch.observe_market({'EUR/USD':{'H1':candles}},now=START+timedelta(hours=5),path=f)
    assert any('Неудачный пробой: H1 вернулась внутрь зоны' in x for x in events(f))

def test_live_settlement_delay_does_not_consume_unsettled_h1(tmp_path):
    f=setup(tmp_path)
    candles=[bar(1,1.1005,1.101,1.1003,1.1005),
             bar(2,1.1005,1.1007,1.0995,1.1004)]
    market={'EUR/USD':{'H1':candles}}
    assert watch.observe_market(market,now=START+timedelta(hours=3,minutes=1),path=f,settle_minutes=5)==0
    assert watch.observe_market(market,now=START+timedelta(hours=3,minutes=6),path=f,settle_minutes=5)==1

def test_existing_watch_database_migrates_without_deleting_rows(tmp_path):
    f=setup(tmp_path)
    with sqlite3.connect(f) as conn:
        conn.execute('ALTER TABLE watched DROP COLUMN candidate_crossed')
        conn.execute('ALTER TABLE watched DROP COLUMN hold_reported')
    with watch._open(f) as conn:
        columns={c[1] for c in conn.execute('PRAGMA table_info(watched)')}
        assert 'candidate_crossed' in columns and 'hold_reported' in columns
        assert conn.execute('SELECT count(*) FROM watched').fetchone()[0]==1

def test_caption_keeps_method_warning_even_with_many_patterns():
    pair='\n'.join(['━━━ 💱 GBP/USD ━━━','⚪ НАПРАВЛЕНИЕ: НЕЙТРАЛЬНО',
                   'W1 БОКОВИК · D1 ШОРТ · H4 ШОРТ · H1 ЛОНГ · M15 ШОРТ · M5 ШОРТ',
                   '⚠️ РАСХОЖДЕНИЕ МЕТОДОВ: H1: таблица ЛОНГ ≠ адаптивная ШОРТ · разные модели, не второе подтверждение',
                   'Согласие D1/H4/H1: 2/3 ШОРТ'])
    image='\n'.join('M15: фигуры ' + ('B'*200) for _ in range(7))
    c=briefing_delivery.compact_pair_caption(pair,image)
    assert 'РАСХОЖДЕНИЕ МЕТОДОВ' in c
    assert briefing_delivery._telegram_len(c) <= 1024

def test_leader_score_cannot_masquerade_as_probability():
    b=SimpleNamespace(symbol='EUR/USD',side='SHORT',confidence=92,news_near=False)
    text='\n'.join(briefing.format_leaders([b]))
    assert '92/100' in text and '92%' not in text
    assert 'оценка согласования' in text

def test_navigator_nonsensical_majority_removed_with_one_of_three(monkeypatch):
    from test_signal_navigator import master, route, source
    s=master('LONG');s['junior_n']=1
    output=signal_navigator.format_confirmed(s,signal_navigator._scale_route(route()),[source()])
    assert 'младшие TF подтверждают лишь 1/3' in output
    assert 'НЕ большинство младших TF' in output
    assert 'Цели Навигатора рассчитаны отдельно' in output

def test_reclaimed_translation_has_no_mixed_ru_english_suffix():
    txt=localize_telegram('BSL (RECLAIMED) · SSL (RECLAIMED)')
    assert 'ВОЗВРАТ ЗА УРОВЕНЬed' not in txt
    assert 'УРОВЕНЬ ВОЗВРАЩЁН' in txt
