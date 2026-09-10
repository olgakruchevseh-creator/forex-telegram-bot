import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import master_direction as master
import news
import bot
from analysis import Candle, PairStack, TfView


def view(key, side):
    structure = "бычья (HH + HL)" if side > 0 else "медвежья (LH + LL)"
    phase = "импульс / тренд вверх" if side > 0 else "импульс / тренд вниз"
    return TfView(key, key, 1.0, structure, phase, 30, side, None)


def stack(side=1, gap=.20):
    views = {key: view(key, side) for key in ("D1", "H4", "H1", "M15", "M5")}
    return PairStack("EUR/USD", 1.1, gap, views, side, side)


def trigger(side="LONG"):
    return f"━━━━━━━━\n⚡ ПРОБОЙ УРОВНЯ\n\n💱 Пара: EUR/USD\nНаправление реакции: {side}"


def amd_trigger(side="LONG"):
    return (f"🎯 AMD / POWER OF THREE — {side}\n💱 Пара: EUR/USD\n"
            f"Направление: {side}\n⚡ Подтверждённый выход: уровень")


class MasterDirectionTests(unittest.TestCase):
    def analyze(self, st=None, alerts=None, dxy=-1, events=None):
        st = st or stack()
        alerts = [trigger()] if alerts is None else alerts
        with patch.object(master, "build_stack", return_value=st), patch.object(
            master.zigzag_scanner, "analyze_symbol",
            return_value={"zigzag_directions": {"H4": 1}},
        ):
            return master.analyze_symbol(
                "EUR/USD", {}, {"EUR": .10, "USD": -.10}, alerts,
                dxy_bias=dxy, events=events or [], now_utc=datetime.now(timezone.utc),
            )

    def test_all_confirmations_create_long(self):
        result = self.analyze()
        self.assertIsNotNone(result)
        self.assertEqual(result["side"], "LONG")

    def test_opposite_h4_zigzag_alone_is_a_penalty(self):
        with patch.object(master, "build_stack", return_value=stack()), patch.object(
            master.zigzag_scanner, "analyze_symbol", return_value={"zigzag_directions": {"H4": -1}}
        ):
            result = master.analyze_symbol("EUR/USD", {}, {"EUR": .1, "USD": -.1}, [trigger()], -1, [])
        self.assertIsNotNone(result)
        self.assertEqual(1, result["conflict_groups"])
        self.assertLess(result["quality"], 94)

    def test_opposite_module_event_blocks(self):
        self.assertIsNone(self.analyze(alerts=[trigger("SHORT")]))

    def test_missing_module_trigger_blocks(self):
        self.assertIsNone(self.analyze(alerts=[]))

    def test_opposite_dxy_alone_is_a_penalty(self):
        result = self.analyze(dxy=1)
        self.assertIsNotNone(result)
        self.assertEqual(1, result["conflict_groups"])
        self.assertLess(result["quality"], 94)

    def test_high_news_within_hour_blocks(self):
        event = news.NewsEvent(
            "n", "NFP", "USD", "HIGH", datetime.now(timezone.utc) + timedelta(minutes=30),
            "—", "—", "—", "higher_is_positive",
        )
        self.assertIsNone(self.analyze(events=[event]))

    def test_almost_equal_strength_blocks(self):
        weak = stack(gap=.03)
        self.assertIsNone(self.analyze(st=weak))

    def test_opposite_module_is_one_penalty_group_without_echo(self):
        result = self.analyze(alerts=[trigger("LONG"), trigger("SHORT")])
        self.assertIsNotNone(result)
        self.assertEqual(0, result["conflict_groups"])

    def test_two_independent_conflict_groups_block(self):
        with patch.object(master, "build_stack", return_value=stack()), patch.object(
            master.zigzag_scanner, "analyze_symbol", return_value={"zigzag_directions": {"H4": -1}}
        ):
            result = master.analyze_symbol(
                "EUR/USD", {}, {"EUR": .1, "USD": -.1}, [trigger()], 1, []
            )
        self.assertIsNone(result)

    def test_master_direction_does_not_depend_on_echo(self):
        self.assertIsNotNone(self.analyze())

    def test_true_two_of_three_majority_is_accepted(self):
        st = stack()
        st.views["D1"] = view("D1", -1)
        self.assertIsNotNone(self.analyze(st=st))

    def test_confirmed_lower_timeframes_pass_as_pullback_inside_senior_opposite(self):
        st = stack()
        st.views["D1"] = view("D1", -1)
        st.views["H4"] = view("H4", -1)
        with patch.object(master, "build_stack", return_value=st), patch.object(
            master.zigzag_scanner, "analyze_symbol", return_value={"zigzag_directions": {"H4": -1}}
        ):
            result = master.analyze_symbol(
                "EUR/USD", {}, {"EUR": .1, "USD": -.1}, [trigger()], -1, []
            )
        self.assertIsNotNone(result)
        self.assertEqual("LONG", result["side"])
        self.assertEqual("SHORT", result["senior_side"])

    def test_neutral_h4_zigzag_is_allowed_with_penalty(self):
        with patch.object(master, "build_stack", return_value=stack()), patch.object(
            master.zigzag_scanner, "analyze_symbol", return_value={"zigzag_directions": {"H4": 0}}
        ):
            result = master.analyze_symbol(
                "EUR/USD", {}, {"EUR": .1, "USD": -.1}, [trigger()], -1, []
            )
        self.assertIsNotNone(result)
        self.assertEqual("RANGE", result["zigzag_h4"])

    def test_stale_opposite_module_only_reduces_quality(self):
        st = stack(gap=.09)
        result = self.analyze(st=st, alerts=[trigger("LONG"), trigger("SHORT")], dxy=0)
        self.assertIsNotNone(result)
        self.assertLess(result["quality"], self.analyze(st=st, dxy=0)["quality"])

    def test_echo_is_not_embedded_in_master_result(self):
        self.assertNotIn("echo", self.analyze())

    def test_output_is_recognized_by_global_antispam(self):
        text = master.format_message(self.analyze())
        self.assertIn("MASTER DIRECTION — LONG", text)
        self.assertIn("Пара: EUR/USD", text)
        self.assertIn("Направление: LONG", text)
        self.assertEqual(bot._alert_pair(text), "EUR/USD")
        self.assertEqual(bot._direct_signal_side(text), "LONG")

    def test_zigzag_message_direction_is_recognized(self):
        text = "↕️ ZIGZAG — СТРУКТУРА\nПара: EUR/USD\nОсновное направление: SHORT"
        self.assertEqual(-1, master._side(text))

    def test_local_amd_allows_h1_lag_when_m15_m5_and_strength_confirm(self):
        st = stack()
        st.views["H1"] = view("H1", -1)
        with patch.object(master, "build_stack", return_value=st):
            result = master.analyze_local_amd_symbol(
                "EUR/USD", {}, {"EUR": .10, "USD": -.10}, [amd_trigger()], events=[]
            )
        self.assertIsNotNone(result)
        self.assertTrue(result["local_early"])
        self.assertEqual("LONG", result["side"])

    def test_local_amd_requires_both_closed_lower_timeframes(self):
        st = stack()
        st.views["M5"] = view("M5", -1)
        with patch.object(master, "build_stack", return_value=st):
            result = master.analyze_local_amd_symbol(
                "EUR/USD", {}, {"EUR": .10, "USD": -.10}, [amd_trigger()], events=[]
            )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
