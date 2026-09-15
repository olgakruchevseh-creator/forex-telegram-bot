import inspect
import unittest
import signal_journal, signal_navigator, briefing, echo_projection, next_pivot_projection

class ContextIntegrationTests(unittest.TestCase):
    def test_journal_is_post_delivery_passive(self):
        src=inspect.getsource(signal_journal.record_sent)
        self.assertIn('market_state.build', src)
        self.assertNotIn('return None', src)
    def test_navigator_market_state_is_metadata(self):
        src=inspect.getsource(signal_navigator._trigger_route)
        self.assertIn('"market_state": shared_state', src)
    def test_briefing_collapses_market_state_to_one_group(self):
        src=inspect.getsource(briefing._briefing_context)
        self.assertIn('groups.append(ms_vote)', src)
    def test_echo_has_bounded_shared_state(self):
        src=inspect.getsource(echo_projection._context_score)
        self.assertIn('score -= 3', src); self.assertIn('score += 2', src)
    def test_next_pivot_market_state_is_metadata(self):
        src=inspect.getsource(next_pivot_projection.analyze_session_symbol)
        self.assertIn('result["market_state"]', src)

if __name__ == '__main__': unittest.main()
