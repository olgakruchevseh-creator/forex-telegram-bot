import unittest
import auction_context, multi_tf_narrative, setup_memory
class ContextPackage86Tests(unittest.TestCase):
    def test_contexts_are_not_signal_families(self):
        self.assertEqual(auction_context.AuctionContext().family,'AUCTION_CONTEXT')
        self.assertEqual(multi_tf_narrative.Narrative(1,'MIXED',0,0,0,0,{}).family,'MULTI_TF_NARRATIVE')
        self.assertEqual(setup_memory.SetupAnalog().family,'SETUP_MEMORY')
    def test_setup_memory_is_neutral_without_history(self):
        self.assertEqual(setup_memory.score_delta(setup_memory.SetupAnalog()),0)
if __name__=='__main__': unittest.main()
