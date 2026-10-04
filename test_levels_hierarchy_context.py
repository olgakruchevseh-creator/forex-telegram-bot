import unittest
import levels


class LevelHierarchyContextTests(unittest.TestCase):
    def zone(self, tfs, tests=0, wicks=0, closes=0, strength=90):
        return levels.Zone("z", "EUR/USD", "support", 1.10, 1.101, 1.1005, tfs, strength, 4, levels._now(), 0, "", 0, "активна", [], tests_recent=tests, false_wicks=wicks, closes_beyond=closes)

    def test_d1_h4_is_senior_and_fresh(self):
        c=levels.level_context(self.zone(["D1","H4"], tests=1))
        self.assertEqual(c["rank"], "СТАРШИЙ")
        self.assertEqual(c["maturity"], "СВЕЖИЙ")
        self.assertGreaterEqual(c["confluence"], 4)

    def test_repeated_tests_weaken_level(self):
        fresh=levels.level_context(self.zone(["H4","H1"], tests=1))
        tired=levels.level_context(self.zone(["H4","H1"], tests=5, wicks=2))
        self.assertEqual(tired["maturity"], "ОСЛАБЛЕННЫЙ")
        self.assertLess(tired["reliability"], fresh["reliability"])

    def test_message_exposes_level_context(self):
        z=self.zone(["D1","H4"], tests=1)
        text=levels.build_message(z, "bounce_sup", "Реакция подтверждена.", "LONG")
        self.assertIn("Класс уровня: СТАРШИЙ · СВЕЖИЙ", text)
        self.assertIn("Надёжность уровня:", text)

if __name__ == "__main__": unittest.main()
