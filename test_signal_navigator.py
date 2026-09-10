import unittest
import os
import tempfile
from unittest.mock import patch

import signal_navigator
from analysis import Candle


def source(side="LONG"):
    return f"🧩 ПАТТЕРН ПОДТВЕРЖДЁН\n💱 Пара: EUR/USD\nНаправление: {side}"


def module_source(title, symbol, side, quality=85, probability=81):
    return (f"{title}\n💱 Пара: {symbol}\nНаправление: {side}\n"
            f"Качество: {quality}/100\nВероятность: {probability}%")


def master(side="LONG"):
    return {"symbol": "EUR/USD", "side": side, "quality": 90, "confidence": 86,
            "gap": .15 if side == "LONG" else -.15, "senior_n": 3, "junior_n": 3,
            "evidence": ["подтверждён паттерн"]}


def route(side="LONG"):
    return {"side": side, "mode": "IMPULSE", "anchor": 1.10, "current": 1.13,
            "target": 1.20, "target_tf": "H4", "progress": 30, "remaining": 70}


class SignalNavigatorTests(unittest.TestCase):
    def test_chain_entry_and_liquidity_are_supported_sources(self):
        chain = module_source("⛓️ CHAIN ENTRY №1", "USD/CHF", "LONG", 90, 86)
        liquidity = module_source("🧹 СНЯТИЕ ЛИКВИДНОСТИ — SHORT", "GBP/USD", "SHORT", 83, 79)
        self.assertEqual("Chain Entries", signal_navigator._source_name(chain))
        self.assertEqual("Liquidity Sweep", signal_navigator._source_name(liquidity))
        self.assertEqual(("USD/CHF", "LONG"),
                         (signal_navigator._pair(chain), signal_navigator._side(chain)))
        self.assertEqual(("GBP/USD", "SHORT"),
                         (signal_navigator._pair(liquidity), signal_navigator._side(liquidity)))

    def test_source_companion_is_built_without_master_veto(self):
        bars = [Candle(f"2026-09-09 {i:02d}:00:00", 1.10, 1.102, 1.098, 1.101) for i in range(30)]
        by_tf = {tf: bars for tf in ("D1", "H4", "H1", "M15", "M5")}
        with patch.object(signal_navigator.movement_progress, "_swings", return_value=[]), \
             patch.object(signal_navigator.movement_progress, "atr", return_value=.002):
            result = signal_navigator.build_source_companion(source(), {"EUR/USD": by_tf}, {"EUR": .6, "USD": .5})
        self.assertIsNotNone(result)
        self.assertIn("НАВИГАТОР СОПРОВОЖДАЕТ СИГНАЛ", result[0])
        self.assertIn("TR1 (H1 ATR)", result[0])

    def test_companion_reports_conflict_without_false_h1_confirmation(self):
        accepted = {**master("LONG"), "source_accepted": True,
                    "gap": -.15, "senior_n": 2, "junior_n": 1,
                    "zigzag_h4": "LONG",
                    "tf_biases": {"D1": 1, "H4": 1, "H1": -1, "M15": 0, "M5": 0}}
        text = signal_navigator.format_confirmed(
            accepted, signal_navigator._scale_route(route()), [source()])
        self.assertIn("ЕСТЬ ВСТРЕЧНЫЕ ФАКТОРЫ", text)
        self.assertIn("Сила относительно LONG: -0.15", text)
        self.assertIn("против направления", text)
        self.assertIn("H1: коррекция против маршрута LONG", text)
        self.assertNotIn("направление LONG подтверждено по закрытой H1", text)

    def test_route_percent_labels_are_unambiguous(self):
        text = signal_navigator.format_confirmed(
            master(), signal_navigator._scale_route(route()), [source()])
        self.assertIn("на 100% общего маршрута", text)
        self.assertIn("Общий маршрут до TR1 пройден", text)
        self.assertIn("Путь от старта до TR1 пройден", text)

    def test_builds_one_combined_card_when_everything_agrees(self):
        with patch.object(signal_navigator.movement_progress, "analyze_progress", return_value=route()):
            result = signal_navigator.build_confirmed([master()], {"EUR/USD": {}}, {}, [source()])
        self.assertEqual(1, len(result))
        text, sources = result[0]
        self.assertIn("ПОДТВЕРЖДЁННЫЙ НАВИГАТОР", text)
        self.assertIn("Направление: LONG 🟢", text)
        self.assertIn("Источники модулей: Patterns", text)
        self.assertIn("Качество: 90/100", text)
        self.assertIn("Общий маршрут до TR1 пройден: 30%", text)
        self.assertEqual([source()], sources)

    def test_blocks_when_route_disagrees(self):
        with patch.object(signal_navigator.movement_progress, "analyze_progress", return_value=route("SHORT")), \
             patch.object(signal_navigator.movement_progress, "analyze_for_side", return_value=None):
            self.assertEqual([], signal_navigator.build_confirmed([master()], {"EUR/USD": {}}, {}, [source()]))

    def test_blocks_when_structural_route_is_not_available(self):
        with patch.object(signal_navigator.movement_progress, "analyze_progress", return_value=None), \
             patch.object(signal_navigator.movement_progress, "analyze_for_side", return_value=None):
            self.assertEqual([], signal_navigator.build_confirmed([master()], {"EUR/USD": {}}, {}, [source()]))

    def test_uses_side_constrained_route_when_h1_route_lags(self):
        with patch.object(signal_navigator.movement_progress, "analyze_progress", return_value=route("SHORT")), \
             patch.object(signal_navigator.movement_progress, "analyze_for_side", return_value=route("LONG")):
            result = signal_navigator.build_confirmed([master()], {"EUR/USD": {}}, {}, [source()])
        self.assertEqual(1, len(result))

    def test_early_local_amd_is_blocked_after_35_percent(self):
        local = {**master(), "local_early": True, "senior_n": 1, "junior_n": 2}
        late_route = {**route(), "current": 1.146, "progress": 46, "remaining": 54}
        with patch.object(signal_navigator.movement_progress, "analyze_for_side", return_value=late_route):
            self.assertEqual([], signal_navigator.build_confirmed([local], {"EUR/USD": {}}, {}, [source()]))

    def test_early_local_amd_card_is_explicitly_local(self):
        local = {**master(), "local_early": True, "senior_n": 1, "junior_n": 2}
        early_route = {**route(), "current": 1.135, "progress": 35, "remaining": 65}
        with patch.object(signal_navigator.movement_progress, "analyze_for_side", return_value=early_route):
            result = signal_navigator.build_confirmed([local], {"EUR/USD": {}}, {}, [source()])
        self.assertEqual(1, len(result))
        self.assertIn("РАННИЙ ЛОКАЛЬНЫЙ СИГНАЛ", result[0][0])
        self.assertIn("Старший тренд ещё не подтверждён полностью", result[0][0])

    def test_confirmed_card_includes_next_pivot_projection(self):
        pivot = {"symbol": "EUR/USD", "kind": "high", "structure": "HH",
                 "zone_low": 1.19, "zone_high": 1.20, "bars_low": 2, "bars_high": 4,
                 "probability": 70, "aligned": 2, "available": 3}
        with patch.object(signal_navigator.movement_progress, "analyze_progress", return_value=route()), \
             patch.object(signal_navigator.next_pivot_projection, "analyze_symbol", return_value=pivot):
            result = signal_navigator.build_confirmed([master()], {"EUR/USD": {}}, {}, [source()])
        self.assertIn("Следующий pivot", result[0][0])
        self.assertIn("1.19000–1.20000", result[0][0])

    def test_candidate_waits_until_confirmed_card_is_delivered(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STATE_DIR": directory}):
            pooled = signal_navigator.remember_candidates([source()], "2026-09-09 10:00:00")
            self.assertEqual([source()], pooled)
            self.assertEqual([source()], signal_navigator.remember_candidates([], "2026-09-09 11:00:00"))
            text = signal_navigator.format_confirmed(master(), route(), [source()])
            signal_navigator.register_card(text, [source()])
            self.assertTrue(signal_navigator.mark_delivered(text))
            self.assertEqual([], signal_navigator.remember_candidates([], "2026-09-09 12:00:00"))

    def test_zigzag_main_direction_is_a_candidate(self):
        text = "↕️ ZIGZAG — СТРУКТУРА\nПара: EUR/USD\nОсновное направление: LONG"
        self.assertEqual("LONG", signal_navigator._side(text))
        self.assertEqual([text], signal_navigator.matching_sources("EUR/USD", "LONG", [text]))

    def test_delivered_card_becomes_active_scenario(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STATE_DIR": directory}):
            text = signal_navigator.format_confirmed(master(), route(), [source()])
            signal_navigator.remember_candidates([source()], "2026-09-09 10:00:00")
            signal_navigator.register_card(text, [source()], "2026-09-09 10:00:00")
            self.assertTrue(signal_navigator.mark_delivered(text))
            active = signal_navigator._load()["active"]["EUR/USD"]
            self.assertEqual("LONG", active["side"])
            self.assertEqual(1.20, active["target"])
            self.assertEqual("2026-09-09 10:00:00", active["last_h1"])

    def test_target_touch_closes_scenario_only_after_delivery(self):
        candles = [Candle(str(i), 1.15, 1.19, 1.14, 1.16) for i in range(29)]
        candles.append(Candle("2026-09-09 11:00:00", 1.19, 1.201, 1.18, 1.199))
        active = {"EUR/USD": {"key": "k", "symbol": "EUR/USD", "side": "LONG",
                  "anchor": 1.10, "target": 1.20, "target_tf": "H4", "sources": "Patterns",
                  "start_h1": "2026-09-09 10:00:00", "last_h1": "2026-09-09 10:00:00",
                  "last_progress": 50, "near_sent": False, "status": "ACTIVE"}}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STATE_DIR": directory}), \
             patch.object(signal_navigator.movement_progress, "closed_candles", return_value=candles), \
             patch.object(signal_navigator, "_opposite_confirmed", return_value=False):
            signal_navigator._save({"active": active})
            messages = signal_navigator.process_lifecycle({"EUR/USD": {"H1": candles}})
            self.assertEqual(1, len(messages))
            self.assertIn("МАРШРУТ ПОЛНОСТЬЮ ОТРАБОТАН", messages[0])
            self.assertIn("Пройдено расчётного пути: 100%", messages[0])
            self.assertIn("EUR/USD", signal_navigator._load()["active"])
            self.assertTrue(signal_navigator.mark_lifecycle_delivered(messages[0]))
            self.assertNotIn("EUR/USD", signal_navigator._load()["active"])

    def test_near_target_warning_is_silent(self):
        candles = [Candle(str(i), 1.15, 1.19, 1.14, 1.16) for i in range(29)]
        candles.append(Candle("2026-09-09 11:00:00", 1.18, 1.19, 1.17, 1.186))
        active = {"EUR/USD": {"key": "k", "symbol": "EUR/USD", "side": "LONG",
                  "anchor": 1.10, "target": 1.20, "target_tf": "H4", "sources": "Patterns",
                  "last_h1": "2026-09-09 10:00:00", "last_progress": 50,
                  "near_sent": False, "status": "ACTIVE"}}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STATE_DIR": directory}), \
             patch.object(signal_navigator.movement_progress, "closed_candles", return_value=candles), \
             patch.object(signal_navigator, "_opposite_confirmed", return_value=False):
            signal_navigator._save({"active": active})
            messages = signal_navigator.process_lifecycle({"EUR/USD": {"H1": candles}})
            self.assertEqual([], messages)

    def test_route_uses_one_scale_to_tr3(self):
        three = {**route(), "current": 1.13,
                 "targets": [{"price": 1.14, "tf": "H4"},
                             {"price": 1.17, "tf": "H4"},
                             {"price": 1.20, "tf": "D1"}]}
        scaled = signal_navigator._scale_route(three)
        self.assertEqual(30, scaled["progress"])
        self.assertEqual([40, 70, 100], [item["route_pct"] for item in scaled["targets"]])
        self.assertEqual(75, scaled["tr1_progress"])
        self.assertEqual(25, scaled["remaining"])

    def test_emoji_prefixed_close_is_used_as_route_anchor(self):
        self.assertEqual(1.38193, signal_navigator._float_line(
            "💵 Цена закрытия: 1.38193", "Цена закрытия"))

    def test_zero_of_three_is_only_local_reaction(self):
        weak = {**master(), "senior_n": 0, "junior_n": 0, "gap": .08,
                "source_accepted": True,
                "tf_biases": {"H1": 1}, "zigzag_h4": "RANGE"}
        text = signal_navigator.format_confirmed(weak, route("SHORT"), [source("SHORT")])
        self.assertIn("ЛОКАЛЬНАЯ РЕАКЦИЯ · ОСНОВНОЙ МАРШРУТ НЕ ПОДТВЕРЖДЁН", text)
        self.assertIn("Режим: ЛОКАЛЬНАЯ РЕАКЦИЯ", text)

    def test_same_side_confirmation_merges_sources_without_resetting_route(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STATE_DIR": directory}):
            old = signal_navigator._scenario_from_card(
                signal_navigator.format_confirmed(master(), route(), [source()]))
            old.update({"sources": "Levels", "status": "ACTIVE", "reached_count": 1,
                        "max_progress": 40, "last_progress": 40})
            signal_navigator._save({"active": {"EUR/USD": old}})
            pattern = source().replace("ПРОБОЙ УРОВНЯ", "ПАТТЕРН ПОДТВЕРЖДЁН")
            new_text = signal_navigator.format_confirmed(master(),
                                                         {**route(), "anchor": 1.15}, [pattern])
            signal_navigator.register_card(new_text, [pattern], "2026-09-09 11:00:00")
            self.assertTrue(signal_navigator.mark_delivered(new_text))
            merged = signal_navigator._load()["active"]["EUR/USD"]
            self.assertEqual(1.10, merged["anchor"])
            self.assertEqual(1, merged["reached_count"])
            self.assertIn("Levels", merged["sources"])
            self.assertIn("Patterns", merged["sources"])

    def test_new_card_is_blocked_when_tr1_is_already_more_than_35_percent(self):
        three = {**route(), "current": 1.13,
                 "targets": [{"price": 1.14, "tf": "H4"},
                             {"price": 1.17, "tf": "H4"},
                             {"price": 1.20, "tf": "D1"}]}
        with patch.object(signal_navigator.movement_progress, "analyze_progress", return_value=three), \
             patch.object(signal_navigator.movement_progress, "analyze_for_side", return_value=three):
            result = signal_navigator.build_confirmed([master()], {"EUR/USD": {}}, {}, [source()])
        self.assertEqual([], result)

    def test_tr1_delivery_activates_tr2_when_filters_are_clear(self):
        candles = [Candle(str(i), 1.15, 1.19, 1.14, 1.16) for i in range(29)]
        candles.append(Candle("2026-09-09 11:00:00", 1.19, 1.201, 1.18, 1.199))
        item = {"key": "k", "symbol": "EUR/USD", "side": "LONG", "anchor": 1.10,
                "target": 1.20, "target_tf": "H4",
                "targets": [{"name": "TR1", "tf": "H4", "price": 1.20},
                            {"name": "TR2", "tf": "D1", "price": 1.25}],
                "sources": "Patterns", "last_h1": "2026-09-09 10:00:00",
                "last_progress": 50, "reached_count": 0, "near_for": 0,
                "near_sent": False, "status": "ACTIVE"}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STATE_DIR": directory}), \
             patch.object(signal_navigator.movement_progress, "closed_candles", return_value=candles), \
             patch.object(signal_navigator, "_continuation_check", return_value=[]):
            signal_navigator._save({"active": {"EUR/USD": item}})
            messages = signal_navigator.process_lifecycle({"EUR/USD": {}}, {})
            self.assertIn("TR1 ПРОЙДЕН", messages[0])
            self.assertIn("Маршрут остаётся активным к TR2", messages[0])
            signal_navigator.mark_lifecycle_delivered(messages[0])
            self.assertEqual(1, signal_navigator._load()["active"]["EUR/USD"]["reached_count"])

    def test_tr1_warns_when_tr2_is_not_confirmed(self):
        item = {"symbol": "EUR/USD", "side": "LONG", "sources": "Levels",
                "target": 1.20, "target_tf": "H4"}
        text = signal_navigator._lifecycle_message(
            item, "TARGET_RISK", 1.20, 100, ["TR1"],
            {"name": "TR2", "tf": "D1", "price": 1.25},
            ["M15 больше не подтверждает LONG"],
        )
        self.assertIn("ПРОДОЛЖЕНИЕ ОСЛАБЛЕНО", text)
        self.assertIn("До TR2 путь пока не подтверждён полностью", text)

    def test_m5_can_register_tr1_and_tr2_before_next_h1_close(self):
        h1 = [Candle("2026-09-09 10:00:00", 1.14, 1.17, 1.13, 1.16)]
        m5 = [Candle("2026-09-09 10:05:00", 1.16, 1.205, 1.15, 1.20),
              Candle("2026-09-09 10:10:00", 1.20, 1.255, 1.19, 1.25)]
        item = {"key": "k", "symbol": "EUR/USD", "side": "LONG", "anchor": 1.10,
                "target": 1.20, "target_tf": "H4",
                "targets": [{"name": "TR1", "tf": "H4", "price": 1.20},
                            {"name": "TR2", "tf": "H4", "price": 1.25},
                            {"name": "TR3", "tf": "D1", "price": 1.30}],
                "sources": "Levels", "start_h1": "2026-09-09 10:00:00",
                "last_h1": "2026-09-09 10:00:00", "last_target_dt": "",
                "last_progress": 10, "reached_count": 0, "near_for": 0,
                "near_sent": False, "status": "ACTIVE"}
        market = {"EUR/USD": {"H1": h1, "M5": m5}}

        def closed(values, minutes):
            return h1 if minutes == 60 else (m5 if minutes == 5 else values)

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STATE_DIR": directory}), \
             patch.object(signal_navigator.movement_progress, "closed_candles", side_effect=closed), \
             patch.object(signal_navigator, "_continuation_check", return_value=[]):
            signal_navigator._save({"active": {"EUR/USD": item}})
            messages = signal_navigator.process_lifecycle(market, {})
            self.assertEqual(1, len(messages))
            self.assertIn("TR1 И TR2 ПРОЙДЕНЫ", messages[0])
            self.assertIn("Маршрут остаётся активным к TR3", messages[0])
            self.assertTrue(signal_navigator.mark_lifecycle_delivered(messages[0]))
            active = signal_navigator._load()["active"]["EUR/USD"]
            self.assertEqual(2, active["reached_count"])
            self.assertEqual("2026-09-09 10:10:00", active["last_target_dt"])


if __name__ == "__main__":
    unittest.main()
