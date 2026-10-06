"""Anti-duplicate scoring for correlated price-action/book contexts.

This layer never creates a signal or an independent KILLER family.  It only
prevents one closed-candle false-break/reclaim fact from receiving several
positive micro-bonuses under different names (Inside Bar, Auction, Turtle).
Negative contradictions are never capped or softened.
"""
from __future__ import annotations
import inside_bar_context
import auction_context
import turtle_breakout_context
import pattern_failure_context

_FALSE_TURTLE={"ЛОЖНЫЙ ПРОБОЙ / RECLAIM","ЛОВУШКА ПРОБОЯ","TURTLE SOUP PLUS ONE","ПОВТОРНЫЙ RECLAIM ПОДТВЕРЖДЁН","ПРОВАЛ ПРИНЯТОГО КАЧЕСТВЕННОГО ПРОБОЯ"}

def _same_false_break(inside_ctx, auction_ctx, turtle_ctx, direction:int)->bool:
    votes=0
    if inside_ctx and getattr(inside_ctx,"confirmed",False) and getattr(inside_ctx,"state","")=="FALSE_BREAK" and getattr(inside_ctx,"direction",0)==direction:
        votes+=1
    if auction_ctx and getattr(auction_ctx,"state","")=="REJECTION_RECLAIM" and getattr(auction_ctx,"alignment",0)>0:
        votes+=1
    if turtle_ctx and getattr(turtle_ctx,"state","") in _FALSE_TURTLE and getattr(turtle_ctx,"alignment",0)>0:
        votes+=1
    return votes>=2

def score(inside_ctx, auction_ctx, turtle_ctx, direction:int, pattern_failure_ctx=None):
    parts={
        "inside_bar":inside_bar_context.score_delta(inside_ctx,direction),
        "auction":auction_context.score_delta(auction_ctx,direction),
        "turtle":turtle_breakout_context.score_delta(turtle_ctx,direction),
    }
    if pattern_failure_ctx is not None:
        parts["pattern_failure"]=pattern_failure_context.score_delta(pattern_failure_ctx,direction)
    negatives=sum(v for v in parts.values() if v<0)
    positives=[v for v in parts.values() if v>0]
    duplicate=_same_false_break(inside_ctx,auction_ctx,turtle_ctx,direction) or bool(pattern_failure_ctx and getattr(pattern_failure_ctx,"alignment",0)>0 and positives)
    # When >=2 layers describe the same false-break lifecycle, only the strongest
    # positive micro-confirmation counts. Contradictions remain additive.
    positive=(max(positives) if positives else 0) if duplicate else sum(positives)
    return negatives+positive,{"duplicate_collapsed":duplicate,"parts":parts,"applied":negatives+positive}
