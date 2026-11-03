"""Closed-H1 and source-route regressions for the new mandatory FX release gate."""
import unittest
import tempfile
import os
import ast
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from analysis import Candle
import signal_contract as contract
import signal_release_guard as release
import strategy_book_policy
import silver_bullet_ict


def clock():
    return datetime.now(timezone.utc).replace(minute=8, second=0, microsecond=0)


def candles(hour_end, count=70):
    out = []
    for i in range(count):
        opened = hour_end - timedelta(hours=count-i)
        px = 1.10 + 0.0002*(i % 10) + 0.00001*i
        out.append(Candle(opened.strftime('%Y-%m-%d %H:%M:%S'), px-0.0001,
                          px+0.0004, px-0.0004, px))
    return out


def market(hour_end=None):
    end = hour_end or clock().replace(minute=0)
    return {'EUR/USD': {'H1': candles(end), 'H4': [], 'D1': [], 'W1': []}}


def levels(side='LONG'):
    return f'⚡ ПРОБОЙ УРОВНЯ\n💱 Пара: EUR/USD\nНаправление пробоя: {side}\nTR1: 1.40000'


def silver(dt):
    return ('⚡ РЕШЕНИЕ: 🟢 ЛОНГ · EUR/USD\nОснование: 🥈 ICT SILVER BULLET\n'
            'Пара: EUR/USD\nНаправление: LONG\n'
            f'M5 событие (открытие UTC): {dt:%Y-%m-%d %H:%M:%S}\n'
            'Вход CE 50%: 1.10000\nОтмена / SL: 1.05000\nTR1: 1.20000')


class SignalContractTests(unittest.TestCase):
    def test_silver_is_directional(self):
        txt = silver(clock())
        self.assertEqual('EUR/USD', contract.pair_of(txt))
        self.assertEqual('LONG', contract.side_of(txt))

    def test_edge_is_information_not_order(self):
        txt = '📐 EDGE H1 · EUR/USD · SHORT\nЭто не ордер.'
        self.assertTrue(contract.is_information_card(txt))
        self.assertEqual('EUR/USD', contract.pair_of(txt))
        self.assertEqual('', contract.side_of(txt))

    def test_neutral_level_remains_explicitly_informational(self):
        self.assertTrue(contract.is_information_card('📍 СИЛЬНЫЙ УРОВЕНЬ\nПара: EUR/USD'))
        self.assertFalse(contract.is_information_card('⚡ ПРОБОЙ УРОВНЯ\nПара: EUR/USD\nНаправление: LONG'))

    def test_commentary_cannot_override_explicit_short_header(self):
        txt = 'Пара: EUR/USD\nНаправление: SHORT\nH4: LONG и FVG LONG'
        self.assertEqual('SHORT', contract.side_of(txt))
        result = strategy_book_policy.assess_candidate('EUR/USD', 'LONG', [txt], {})
        self.assertEqual(('CONTEXT_ONLY',), result['families'])

    def test_known_source_registry_has_all_scanners(self):
        self.assertEqual(26, len(release.SOURCE_FAMILIES))
        self.assertEqual('blocks', release.SOURCE_FAMILIES['order_block'])
        self.assertEqual('blocks', release.SOURCE_FAMILIES['breaker_block'])
        self.assertEqual('imbalance', release.SOURCE_FAMILIES['bpr'])


    def test_real_bot_routes_match_source_family_registry(self):
        source = ast.parse((Path(__file__).parent / 'bot.py').read_text(encoding='utf-8'))
        registered = set()
        for node in ast.walk(source):
            if not isinstance(node, ast.Assign):
                continue
            if (any(isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                    and target.value.id == 'source_ids' for target in node.targets)
                    and isinstance(node.value, ast.Constant)):
                registered.add(node.value.value)
        self.assertEqual(set(release.SOURCE_FAMILIES), registered)


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.now = clock()
        self.end = self.now.replace(minute=0)
        self.market = market(self.end)
        self.mock_trap = patch.object(release.killer_trap_guard, 'inspect', return_value={
            'blocked': False, 'risk': 'NO_CONFIRMED_TRAP', 'checks': [{'status': 'PASS'}],
        }).start()
        self.addCleanup(patch.stopall)

    def check(self, text, sources=None, peers=None, **kwargs):
        return release.review(text, self.market, peers or [text], sources or {text: 'levels'},
                              now=kwargs.pop('now', self.now), **kwargs)

    def test_new_h1_allows_registered_source(self):
        result = self.check(levels())
        self.assertEqual('ALLOW', result.status)
        self.assertEqual('levels', result.source)

    def test_missing_side_fails_closed(self):
        text = '💱 Пара: EUR/USD\nКонтекст H4 LONG'
        self.assertEqual('BLOCK', self.check(text).status)

    def test_m5_stays_pending_before_next_h1(self):
        dt = self.end + timedelta(minutes=2)
        result = self.check(silver(dt), {silver(dt): 'silver_bullet_ict'})
        self.assertEqual('PENDING_H1', result.status)

    def test_m5_is_released_only_after_subsequent_closed_h1(self):
        dt = self.end - timedelta(minutes=47)
        text = silver(dt)
        result = self.check(text, {text: 'silver_bullet_ict'})
        self.assertEqual('ALLOW', result.status)

    def test_m5_without_event_time_cannot_pass(self):
        text = silver(self.end-timedelta(minutes=45)).replace(
            'M5 событие (открытие UTC): ', 'Неизвестное время: ')
        result = self.check(text, {text: 'silver_bullet_ict'})
        self.assertEqual('ltf_event_timestamp_unknown', result.reason)

    def test_h1_age_is_limited(self):
        result = self.check(levels(), now=self.end+timedelta(minutes=45))
        self.assertEqual('h1_not_fresh', result.reason)

    def test_confirmed_trap_blocks_any_source(self):
        self.mock_trap.return_value = {'blocked': True, 'reason': 'failed_upside_breakout',
                                       'risk': 'HIGH', 'checks': [{'status':'BLOCK'}]}
        text = levels()
        self.assertEqual('trap_failed_upside_breakout', self.check(text).reason)

    def test_each_family_is_single_voice(self):
        primary = levels()
        peers = [primary,
                 '💱 Пара: EUR/USD\nНаправление: LONG\nORDER BLOCK',
                 '💱 Пара: EUR/USD\nНаправление: LONG\nBREAKER BLOCK',
                 '💱 Пара: EUR/USD\nНаправление: SHORT\nZIGZAG']
        ids = {peers[0]:'levels',peers[1]:'order_block',peers[2]:'breaker_block',peers[3]:'zigzag_scanner'}
        ruling = self.check(primary, ids, peers)
        self.assertEqual('ALLOW', ruling.status)
        row = next(x for x in ruling.checks if x['check']=='family_arbitration')
        self.assertEqual(['blocks'], row['independent_support'])
        self.assertEqual(['structure'], row['independent_opposition'])
        self.assertEqual('WARN', row['status'])

    def test_h4_h1_both_opposite_is_hard_veto(self):
        with patch.object(release, 'analyze_tf', return_value=SimpleNamespace(bias=-1)):
            self.market['EUR/USD']['H4'] = candles(self.end, count=70)
            self.assertEqual('h4_h1_both_opposite', self.check(levels()).reason)

    def test_h4_only_opposite_is_warn_not_blanket_veto(self):
        def bias(tf, *args):
            return SimpleNamespace(bias=-1 if tf=='H4' else 1)
        with patch.object(release, 'analyze_tf', side_effect=bias):
            self.market['EUR/USD']['H4'] = candles(self.end, count=70)
            result = self.check(levels())
        self.assertEqual('ALLOW', result.status)
        mtf = next(x for x in result.checks if x['check']=='multi_tf_source')
        self.assertEqual('WARN', mtf['status'])

    def test_invalidation_already_breached_is_blocked(self):
        text='⚡ ПРОБОЙ УРОВНЯ\nПара: EUR/USD\nНаправление: LONG\nОтмена: 1.50000\nВход: 1.60000'
        self.assertEqual('stop_already_breached', self.check(text).reason)

    def test_silver_event_chart_survives_restart(self):
        with tempfile.TemporaryDirectory() as state_dir, patch.dict(os.environ, {'STATE_DIR': state_dir}):
            text = silver(self.end - timedelta(minutes=50))
            snapshot = Candle(self.end.strftime('%Y-%m-%d %H:%M:%S'), 1.10, 1.11, 1.09, 1.105)
            key = 'EUR/USD|LONG|NY|test'
            silver_bullet_ict._save({'pending': {key: {'text': text, 'event': {
                'symbol': 'EUR/USD', 'side': 'LONG', 'window': 'NY', 'fvg_low': 1.09,
                'fvg_high': 1.11, 'liquidity': 1.08,
            }, 'm5_bars': [snapshot.__dict__], 'saved_at': 100.0}}, 'sent': {}})
            silver_bullet_ict._PENDING_CARDS.pop(text, None)
            with patch.object(silver_bullet_ict, 'render_chart', return_value='image') as draw:
                self.assertEqual('image', silver_bullet_ict.image_for_alert(text))
                draw.assert_called_once()
            self.assertTrue(silver_bullet_ict.mark_delivered(text))
            self.assertEqual({}, silver_bullet_ict._load()['pending'])
            self.assertIn(key, silver_bullet_ict._load()['sent'])

if __name__ == '__main__':
    unittest.main()
