"""Layer 18 — Scenario Integrity / Causal Reasoning Brain (OBSERVE_ONLY).

Audits the Layer-17 causal graph instead of creating another strategy.  It asks
whether the observed market story is internally coherent: ordered transitions,
missing bridge stages, side continuity, lifecycle health and context conflicts.

Important: Layer 17 currently carries canonical closed-candle states, not event
timestamps. Therefore this layer never invents clock-age/staleness.  It reports
lifecycle deterioration (THREATENED/CONFLICT/etc.) separately and exposes that
true timestamp freshness is unavailable until provenance contains timestamps.
"""
from __future__ import annotations

import config as cfg

_CHAIN = ("SWEEP", "SWEEP_RECLAIM", "CISD", "CHOCH", "MSS", "MARKET_STRUCTURE")
_RANK = {name: i for i, name in enumerate(_CHAIN)}
_BAD_STATES = {"CONFLICT", "THREATENED", "INVALID", "INVALIDATED", "CANCELLED", "CANCELED", "BROKEN"}


def _side(v):
    if isinstance(v, str):
        u = v.upper()
        return 1 if u in {"LONG", "BUY", "ЛОНГ"} else -1 if u in {"SHORT", "SELL", "ШОРТ"} else 0
    return 1 if v and v > 0 else -1 if v and v < 0 else 0


def _event_nodes(graph):
    nodes = graph.get("nodes") if isinstance(graph, dict) else []
    return [n for n in nodes if isinstance(n, dict) and n.get("event") in _RANK]


def assess(pair: str, side, graph: dict | None, structured: dict | None = None,
           event_sequence: dict | None = None) -> dict:
    if not getattr(cfg, "LAYER18_SCENARIO_INTEGRITY_ENABLED", True):
        return {"layer": 18, "observe_only": True, "state": "DISABLED", "trade_effect": False}

    graph = graph if isinstance(graph, dict) else {}
    structured = structured if isinstance(structured, dict) else {}
    event_sequence = event_sequence if isinstance(event_sequence, dict) else {}
    candidate = _side(side)
    nodes = _event_nodes(graph)
    diagnostics = []

    if not nodes:
        state = "NO_CAUSAL_FACTS"
        integrity = "UNKNOWN"
    else:
        # Work per side: opposite-side facts are diagnostic context and must not
        # be woven into the candidate's causal chain.
        by_side = {}
        for n in nodes:
            s = _side(n.get("side"))
            if s:
                by_side.setdefault(s, []).append(n)

        candidate_nodes = by_side.get(candidate, []) if candidate else []
        ordered = sorted(candidate_nodes, key=lambda n: (_RANK[n["event"]], str(n.get("id", ""))))
        present = []
        for n in ordered:
            if n["event"] not in present:
                present.append(n["event"])

        # A gap is only a bridge gap between observed stages.  Earlier optional
        # setup stages are not declared missing merely because a later expert
        # state exists (e.g. structure can be known without observing a sweep).
        missing_bridges = []
        if len(present) >= 2:
            lo, hi = min(_RANK[e] for e in present), max(_RANK[e] for e in present)
            missing_bridges = [e for e in _CHAIN[lo:hi + 1] if e not in present]
        if missing_bridges:
            diagnostics.append({"type": "MISSING_BRIDGE_STAGES", "events": missing_bridges})

        # Layer 16 canonical_sequence is stage ordered by design.  If a future
        # producer supplies a different order, expose it rather than silently
        # normalizing it.
        raw_sequence = graph.get("canonical_sequence")
        raw_sequence = [e for e in raw_sequence if e in _RANK] if isinstance(raw_sequence, list) else []
        if raw_sequence and any(_RANK[b] < _RANK[a] for a, b in zip(raw_sequence, raw_sequence[1:])):
            diagnostics.append({"type": "EVENT_ORDER_VIOLATION", "sequence": raw_sequence})

        degraded = []
        for n in candidate_nodes:
            st = str(n.get("state", "")).upper()
            if st in _BAD_STATES:
                degraded.append({"event": n.get("event"), "state": st, "timeframe": n.get("timeframe", "")})
        if degraded:
            diagnostics.append({"type": "LIFECYCLE_DETERIORATION", "facts": degraded})

        graph_conflicts = graph.get("conflicts") if isinstance(graph.get("conflicts"), list) else []
        if graph_conflicts:
            diagnostics.append({"type": "GRAPH_INTERNAL_CONFLICT", "count": len(graph_conflicts)})

        alignment = graph.get("family_alignment") if isinstance(graph.get("family_alignment"), dict) else {}
        opposed = alignment.get("opposed") if isinstance(alignment.get("opposed"), list) else []
        if opposed:
            diagnostics.append({"type": "OPPOSED_CONTEXT_FAMILIES", "families": sorted(set(opposed))})

        # Side discontinuity means causal stages exist on both sides.  It is
        # telemetry only: it can represent transition, pullback or stale state.
        active_sides = sorted(s for s, vals in by_side.items() if vals)
        if len(active_sides) > 1:
            diagnostics.append({"type": "SIDE_DISCONTINUITY", "sides": active_sides})

        severe = {"EVENT_ORDER_VIOLATION", "LIFECYCLE_DETERIORATION", "GRAPH_INTERNAL_CONFLICT"}
        types = {d["type"] for d in diagnostics}
        if types & severe:
            integrity = "CONFLICTED"
        elif "MISSING_BRIDGE_STAGES" in types or "SIDE_DISCONTINUITY" in types or "OPPOSED_CONTEXT_FAMILIES" in types:
            integrity = "PARTIAL"
        elif candidate_nodes:
            integrity = "COHERENT"
        else:
            integrity = "UNKNOWN"
        state = "INTEGRITY_READY"

    return {
        "layer": 18,
        "observe_only": True,
        "state": state,
        "pair": pair,
        "candidate_side": candidate,
        "integrity": integrity,
        "diagnostics": diagnostics,
        "diagnostic_types": [d["type"] for d in diagnostics],
        "timestamp_freshness": "UNAVAILABLE_NO_EVENT_TIMESTAMPS",
        "lifecycle_freshness_proxy_only": True,
        "layer17_state": graph.get("state"),
        "layer16_state": structured.get("state"),
        "layer15_state": event_sequence.get("state"),
        "closed_h1_policy_preserved": True,
        "m15_m5_confirmation_only": True,
        "trade_effect": False,
        "direction_claim": False,
        "threshold_effect": False,
        "probability_effect": False,
        "veto_effect": False,
        "basis": "LAYER17_GRAPH_INTEGRITY_NOT_NEW_SIGNAL",
    }
