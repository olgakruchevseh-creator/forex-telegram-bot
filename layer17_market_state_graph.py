"""Layer 17 — Market State Graph / Causal Context Brain (OBSERVE_ONLY).

Builds a deterministic graph from Layer 16 canonical expert facts and Layer 15
ordered-event telemetry. It does not create a trade direction, vote, veto,
probability adjustment, threshold change, or Telegram message.
"""
from __future__ import annotations

import config as cfg

_STAGE = {
    "SWEEP": 10, "SWEEP_RECLAIM": 20,
    "CISD": 30, "CHOCH": 40, "MSS": 50,
    "MARKET_STRUCTURE": 60,
}


def _side(v):
    if isinstance(v, str):
        u = v.upper()
        return 1 if u in {"LONG", "BUY", "ЛОНГ"} else -1 if u in {"SHORT", "SELL", "ШОРТ"} else 0
    return 1 if v and v > 0 else -1 if v and v < 0 else 0


def _node_id(i, fact):
    return "%s:%s:%s:%s:%d" % (
        fact.get("family", "UNKNOWN"), fact.get("event", "UNKNOWN"),
        fact.get("timeframe", ""), fact.get("state", ""), i,
    )


def assess(pair: str, side, structured: dict | None, event_sequence: dict | None = None) -> dict:
    if not getattr(cfg, "LAYER17_MARKET_STATE_GRAPH_ENABLED", True):
        return {"layer": 17, "observe_only": True, "state": "DISABLED", "trade_effect": False}

    candidate = _side(side)
    structured = structured if isinstance(structured, dict) else {}
    facts = structured.get("facts") if isinstance(structured.get("facts"), list) else []

    nodes = []
    for i, raw in enumerate(facts):
        if not isinstance(raw, dict):
            continue
        f = dict(raw)
        nodes.append({
            "id": _node_id(i, f),
            "family": f.get("family", "UNKNOWN"),
            "event": f.get("event", "UNKNOWN"),
            "side": _side(f.get("side", 0)),
            "timeframe": f.get("timeframe", ""),
            "state": f.get("state", ""),
            "source": f.get("source", "DIRECT_MODULE_STATE"),
            "stage": _STAGE.get(f.get("event"), 0),
        })

    # Causal edges are conservative: only known ordered structural/liquidity
    # transitions on the same non-zero side are connected. Context families
    # (REGIME/LOCATION) are attached as context, never as directional votes.
    ordered = sorted((n for n in nodes if n["stage"]), key=lambda n: (n["stage"], n["id"]))
    edges = []
    for a, b in zip(ordered, ordered[1:]):
        if a["stage"] < b["stage"] and a["side"] and a["side"] == b["side"]:
            edges.append({"from": a["id"], "to": b["id"], "relation": "PRECEDES_SUPPORTS"})

    # Family-level disagreement is telemetry, not a veto. Multiple facts from
    # the same correlated family are collapsed into one conflict record.
    conflicts = []
    by_family = {}
    for n in nodes:
        if n["side"]:
            by_family.setdefault(n["family"], set()).add(n["side"])
    for family, sides in sorted(by_family.items()):
        if len(sides) > 1:
            conflicts.append({"family": family, "type": "INTERNAL_SIDE_CONFLICT", "sides": sorted(sides)})

    aligned = sorted({n["family"] for n in nodes if candidate and n["side"] == candidate})
    opposed = sorted({n["family"] for n in nodes if candidate and n["side"] == -candidate})
    neutral = sorted({n["family"] for n in nodes if not n["side"]})

    seq = structured.get("event_sequence") if isinstance(structured.get("event_sequence"), list) else []
    l15_state = event_sequence.get("state") if isinstance(event_sequence, dict) else None
    l15_setup = event_sequence.get("setup_state") if isinstance(event_sequence, dict) else None

    return {
        "layer": 17,
        "observe_only": True,
        "state": "GRAPH_READY" if nodes else "NO_FACTS",
        "pair": pair,
        "candidate_side": candidate,
        "nodes": nodes,
        "edges": edges,
        "canonical_sequence": list(seq),
        "family_alignment": {"aligned": aligned, "opposed": opposed, "neutral": neutral},
        "conflicts": conflicts,
        "layer15_state": l15_state,
        "layer15_setup_state": l15_setup,
        "closed_h1_policy_preserved": True,
        "m15_m5_confirmation_only": True,
        "trade_effect": False,
        "direction_claim": False,
        "threshold_effect": False,
        "probability_effect": False,
        "veto_effect": False,
        "basis": "LAYER16_CANONICAL_FACTS_PLUS_LAYER15_ORDER",
    }
