"""Общий слой поверх фактов модулей.

Модули по-прежнему находят свои события. Этот файл решает три вещи,
которые один сканер не видит:

1. Несколько модулей про одну и ту же пару и сторону = один факт, не пачка писем.
2. Если движение уже против старшей структуры — это откат, а не новый импульс.
3. Если путь до ближайшей цели уже в основном пройден — факт остаётся внутри,
   в Telegram как «новый вход» не уходит.
"""
from __future__ import annotations

import hashlib
import logging
import re

import config as cfg
import movement_progress
import ohlc_movement
import market_regime
import zigzag_scanner
import decision_journal
import evidence_uncertainty_context
import information_flow_context
import evidence_reliability_context
import layer11_change_point_context
import layer12_regime_memory
import layer13_drift_attribution
import layer14_adaptive_confidence
import layer15_event_sequence
import layer16_structured_intelligence
import layer17_market_state_graph
import layer18_scenario_integrity
import layer19_evidence_independence
import layer20_decision_readiness
import layer21_mathematical_core
import layer22_mathematical_stability
import layer23_mathematical_confidence
import layer24_mathematical_resilience
import layer25_information_value
import layer26_mathematical_coherence
import layer27_uncertainty_budget
import layer28_decision_margin
from analysis import analyze_tf

log = logging.getLogger("fxbot.context")

_SOURCE_MARKERS = (
    ("KILLER", "Killer"),
    ("ICT SILVER BULLET", "Silver Bullet"),
    ("POWER OF THREE", "AMD"),
    ("СНЯТИЕ ЛИКВИДНОСТИ", "Liquidity Sweep"),
    ("ORDER BLOCK", "Order Block"),
    ("BREAKER BLOCK", "Breaker Block"),
    ("ВЫХОД ИЗ ФАЗЫ", "Accumulation/Distribution"),
    ("ВЫХОД ИЗ ЗОНЫ КОНСОЛИДАЦИИ", "Consolidation"),
    ("CHAIN ENTRY", "Chain Entries"),
    ("ПАТТЕРН ПОДТВЕРЖДЁН", "Patterns"),
    ("QUASIMODO — QM", "Quasimodo"),
    ("ДИСБАЛАНС", "Disbalance"),
    ("IMBALANCE", "Imbalance/FVG"),
    ("СЕТКИ ФИБОНАЧЧИ", "Fibonacci"),
    ("FIB + SMC", "Fib+SMC"),
    ("ATS REVERSAL", "ATS"),
    ("SMART MONEY 62-26", "Smart Money 62-26"),
    ("ПРОБОЙ PDH", "Daily High/Low"),
    ("ПРОБОЙ PDL", "Daily High/Low"),
    ("СНЯТИЕ PDH", "Daily High/Low"),
    ("СНЯТИЕ PDL", "Daily High/Low"),
    ("ZIGZAG", "ZigZag"),
    ("ПРОБОЙ УРОВНЯ", "Levels"),
    ("ОТБОЙ ОТ", "Levels"),
    ("ЛОЖНЫЙ ПРОБОЙ", "Levels"),
    ("РЕТТЕСТ УРОВНЯ", "Levels"),
    ("СМЕНА РОЛИ", "Levels"),
    ("POC —", "POC"),
    ("CRT —", "CRT"),
    ("СТРУКТУРНЫЙ РЕТЕСТ", "Retest"),
)


def pair_of(text: str) -> str:
    match = re.search(r"(?:Пара:\s*|💱 Пара:\s*)([A-Z]{3}/[A-Z]{3})", text or "")
    if not match:
        match = re.search(r"(?:LONG|SHORT)\s+([A-Z]{3}/[A-Z]{3})", text or "")
    return match.group(1) if match else ""


def side_of(text: str) -> str:
    match = re.search(r"(?:^|\n)[🟢🔴]?\s*(LONG|SHORT)\s+[A-Z]{3}/[A-Z]{3}", text or "", re.I)
    if not match:
        match = re.search(
            r"(?:🧭\s*)?Направление(?:\s+(?:реакции|пробоя|разворота))?:\s*(LONG|SHORT)\b",
            text or "",
            re.I,
        )
    return match.group(1).upper() if match else ""


def source_name(text: str) -> str:
    upper = (text or "").upper()
    for marker, name in _SOURCE_MARKERS:
        if marker in upper:
            return name
    return "Модуль"


_VOLATILE_EVENT_LINE = re.compile(
    r"^(?:Текущая цена:|Качество:|Вероятность:|Killer Score:|"
    r"Уверенность(?: модели)?:|Статус решения:|Currency Strength:|"
    r"Контекст младших ТФ:).*$",
    re.M | re.I,
)


def event_fingerprint(text: str) -> str:
    """Stable id for one setup: pair/side/source + structural lines, not live price."""
    cleaned = _VOLATILE_EVENT_LINE.sub("", text or "")
    keep = []
    for raw in cleaned.splitlines():
        line = raw.strip()
        if not line or line.startswith("━"):
            continue
        keep.append(line)
        if len(keep) >= 10:
            break
    blob = "|".join([pair_of(text), side_of(text), source_name(text), *keep])
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def _quality(text: str) -> int:
    match = re.search(r"(?:Killer Score|Качество|Уверенность модели|Вероятность):\s*(\d{1,3})", text or "")
    return int(match.group(1)) if match else 70


def _tf_biases(by_tf: dict) -> dict[str, int]:
    views = {}
    for tf, minutes in (("D1", 1440), ("H4", 240), ("H1", 60), ("M15", 15), ("M5", 5)):
        bars = movement_progress.closed_candles(by_tf.get(tf) or [], minutes)
        views[tf] = analyze_tf(tf, tf, bars).bias if len(bars) >= 20 else 0
    return views


def _h4_zigzag(symbol: str, by_tf: dict) -> int:
    try:
        snap = zigzag_scanner.analyze_symbol(symbol, by_tf)
        return int((snap.get("zigzag_directions") or {}).get("H4", 0) or 0)
    except Exception:
        log.exception("context h4 zigzag %s", symbol)
        return 0


def _strength_gap(symbol: str, strength: dict) -> float:
    try:
        base, quote = symbol.split("/")
        return float(strength.get(base, 0)) - float(strength.get(quote, 0))
    except (TypeError, ValueError):
        return 0.0


def inspect(symbol: str, side: str, by_tf: dict, strength: dict) -> dict:
    """Снимок рынка для уже найденного модульного факта."""
    direction = 1 if side == "LONG" else -1
    views = _tf_biases(by_tf)
    h4_zz = _h4_zigzag(symbol, by_tf)
    gap = _strength_gap(symbol, strength)
    directed_gap = gap * direction
    mode = movement_progress._movement_mode(direction, views.get("D1", 0), views.get("H4", 0))
    route = movement_progress.analyze_for_side(symbol, by_tf, strength, side)
    if not route:
        route = movement_progress.analyze_progress(symbol, by_tf, strength)
        if route and route.get("side") != side:
            route = None
    progress = int((route or {}).get("progress") or 0)
    junior_n = sum(views[tf] == direction for tf in ("H1", "M15", "M5"))
    senior_n = sum(views[tf] == direction for tf in ("D1", "H4", "H1"))
    ohlc = {"available": False}
    if getattr(cfg, "OHLC_MOVEMENT_FILTER_ENABLED", True):
        ohlc = ohlc_movement.combine(
            [
                (tf, movement_progress.closed_candles(by_tf.get(tf) or [], minutes))
                for tf, minutes in (("H4", 240), ("H1", 60), ("M15", 15), ("M5", 5))
            ],
            direction,
        )
    against_h4 = bool(h4_zz and h4_zz != direction)
    regime = market_regime.analyze_symbol(symbol, by_tf)
    regime_name = regime.name if regime else "UNKNOWN"
    # A prolonged counter-move inside an objectively non-directional market is
    # RANGE/COMPRESSION, not an endlessly extending pullback.
    if regime_name in ("RANGE", "COMPRESSION"):
        mode = regime_name
    elif against_h4:
        mode = "PULLBACK"
    return {
        "symbol": symbol,
        "side": side,
        "mode": mode,
        "regime": regime_name,
        "gap": gap,
        "directed_gap": directed_gap,
        "h4_zz": h4_zz,
        "against_h4": against_h4,
        "views": views,
        "junior_n": junior_n,
        "senior_n": senior_n,
        "progress": progress,
        "route": route,
        "ohlc": ohlc,
        "weak_reversal": bool(ohlc.get("weak_reversal")),
    }


def verdict(ctx: dict) -> tuple[bool, str]:
    """False = не показывать как новый вход. Факт модуля уже сохранён сканером."""
    max_progress = int(getattr(cfg, "SIGNAL_INITIAL_MAX_PROGRESS_PCT", 35))
    min_junior = int(getattr(cfg, "CONTEXT_MIN_JUNIOR_AGREE", 2))
    min_gap = float(getattr(cfg, "CONTEXT_MIN_DIRECTED_GAP", 0.03))
    allow_pullback = bool(getattr(cfg, "CONTEXT_ALLOW_LABELED_PULLBACK", True))
    pullback_max = int(getattr(cfg, "CONTEXT_PULLBACK_MAX_PROGRESS_PCT", 25))

    if ctx.get("weak_reversal") and (
        ctx.get("against_h4")
        or str(ctx.get("structure_state") or "") in ("CONFIRMED", "SHIFT_CONFIRMED")
    ):
        return False, "weak_reversal"
    # Partial TF disagreement and ordinary strength mismatch are context, not
    # independent vetoes. Master already prices them into quality/probability.
    # Keep hard blocks only for objectively non-executable/false-reversal cases.
    if ctx["progress"] > max_progress and ctx["mode"] != "PULLBACK":
        return False, "late_tr1"
    if ctx["against_h4"] or ctx["mode"] == "PULLBACK":
        if not allow_pullback:
            return False, "pullback_blocked"
        if ctx["progress"] > pullback_max:
            return False, "late_pullback"
        if ctx["directed_gap"] < -float(getattr(cfg, "CONTEXT_PULLBACK_MAX_OPPOSITE_GAP", 0.12)):
            return False, "pullback_strength_crush"
        return True, "pullback"
    if ctx["junior_n"] < min_junior:
        return True, "junior_conflict_soft"
    if ctx["directed_gap"] < min_gap:
        return True, "strength_against_soft"
    return True, "impulse" if ctx["mode"] == "IMPULSE" else "local"


def _mode_line(ctx: dict, reason: str) -> str:
    zz = {1: "LONG", -1: "SHORT"}.get(int(ctx.get("h4_zz") or 0), "RANGE")
    if ctx["mode"] in ("RANGE", "COMPRESSION"):
        label = "БОКОВИК / RANGE" if ctx["mode"] == "RANGE" else "СЖАТИЕ / COMPRESSION"
        return (f"↔ Режим: {label}\n"
                f"H4 ZigZag: {zz} · младшие ТФ за {ctx['side']}: {ctx['junior_n']}/3\n"
                "Направленный откат не объявляем: рынок сейчас классифицирован как ненаправленный.")
    if reason == "pullback" or ctx["mode"] == "PULLBACK":
        return (
            "📉 Режим: ОТКАТ, не новый импульс\n"
            f"H4 ZigZag: {zz} · младшие ТФ за {ctx['side']}: {ctx['junior_n']}/3\n"
            "Это коррекция внутри старшего движения. Новый тренд отсюда не объявляем."
        )
    if ctx["mode"] == "IMPULSE":
        return (
            "📈 Режим: ИМПУЛЬС по H4\n"
            f"H4 ZigZag: {zz} · младшие ТФ: {ctx['junior_n']}/3 · сила: {ctx['directed_gap']:+.2f}"
        )
    return (
        f"↕ Режим: ЛОКАЛЬНОЕ движение\n"
        f"H4 ZigZag: {zz} · младшие ТФ: {ctx['junior_n']}/3 · сила: {ctx['directed_gap']:+.2f}"
    )


def stamp(text: str, ctx: dict, reason: str, allies: list[str]) -> str:
    names = []
    seen = set()
    for raw in [text] + allies:
        name = source_name(raw)
        if name not in seen:
            seen.add(name)
            names.append(name)
    support = " · ".join(names[1:]) if len(names) > 1 else "нет, факт одиночный"
    block = [
        "",
        "—— контекст соседних модулей ——",
        _mode_line(ctx, reason),
        f"Подтверждают тот же факт: {support}",
    ]
    if ctx.get("progress"):
        block.append(f"Пройдено до ближайшей структурной цели: {ctx['progress']}%")
    return text.rstrip() + "\n" + "\n".join(block)


def prepare(alerts: list[str], market: dict, strength: dict) -> tuple[list[dict], list[tuple[str, str]]]:
    """Склеивает факты одной идеи и отсекает опоздавшие/ложные входы.

    Возвращает бандлы для Telegram и список (причина, текст) внутренних отказов.
    """
    if not getattr(cfg, "SIGNAL_CONTEXT_ENABLED", True):
        return [{"primary": text, "allies": [], "pair": pair_of(text), "side": side_of(text)} for text in alerts], []

    grouped: dict[tuple[str, str], list[str]] = {}
    neutral: list[str] = []
    for text in alerts:
        pair, side = pair_of(text), side_of(text)
        if not pair or not side:
            neutral.append(text)
            continue
        grouped.setdefault((pair, side), []).append(text)

    # Layer 8: passive pair-level evidence uncertainty. It cannot alter verdicts.
    pair_uncertainty = {}
    if getattr(cfg, "EVIDENCE_UNCERTAINTY_ENABLED", True):
        for pair_name in {p for p, _s in grouped}:
            pair_uncertainty[pair_name] = evidence_uncertainty_context.assess(
                grouped.get((pair_name, "LONG"), []),
                grouped.get((pair_name, "SHORT"), []),
            )

    dropped: list[tuple[str, str]] = []
    winners: dict[str, dict] = {}
    for (pair, side), texts in grouped.items():
        texts = sorted(texts, key=_quality, reverse=True)
        killer = next((item for item in texts if "🏹🎯 KILLER" in item), None)
        if killer:
            texts = [killer] + [item for item in texts if item != killer]
        primary, allies = texts[0], texts[1:]
        ctx = inspect(pair, side, market.get(pair) or {}, strength)
        if pair in pair_uncertainty:
            ctx["evidence_uncertainty"] = pair_uncertainty[pair]
        # Layer 9: lagged information-flow telemetry. Observe-only; computed before
        # learning the current batch, so it cannot use the present to explain itself.
        if getattr(cfg, "INFORMATION_FLOW_ENABLED", True):
            try:
                ctx["information_flow"] = information_flow_context.observe(pair, side, texts)
            except Exception:
                log.exception("INFORMATION_FLOW_CONTEXT_SKIPPED pair=%s", pair)
        # Layer 10: reliability/calibration-readiness synthesis. Passive telemetry only.
        if getattr(cfg, "EVIDENCE_RELIABILITY_ENABLED", True):
            try:
                ctx["evidence_reliability"] = evidence_reliability_context.assess(
                    ctx.get("evidence_uncertainty"), ctx.get("information_flow")
                )
            except Exception:
                log.exception("EVIDENCE_RELIABILITY_CONTEXT_SKIPPED pair=%s", pair)
        # Layer 11: persistent concept-drift / change-point telemetry. OBSERVE_ONLY.
        if getattr(cfg, "LAYER11_CHANGE_POINT_ENABLED", True):
            try:
                ctx["change_point"] = layer11_change_point_context.assess(
                    pair, ctx, ctx.get("evidence_reliability")
                )
            except Exception:
                log.exception("LAYER11_CHANGE_POINT_SKIPPED pair=%s", pair)
        # Layer 12: recurring-context memory. OBSERVE_ONLY; consumes Layer 11, never trades.
        if getattr(cfg, "LAYER12_REGIME_MEMORY_ENABLED", True):
            try:
                ctx["regime_memory"] = layer12_regime_memory.assess(pair, ctx, ctx.get("change_point"))
            except Exception:
                log.exception("LAYER12_REGIME_MEMORY_SKIPPED pair=%s", pair)
        # Layer 13: drift attribution + sparse context-transition memory. OBSERVE_ONLY.
        if getattr(cfg, "LAYER13_DRIFT_ATTRIBUTION_ENABLED", True):
            try:
                ctx["drift_attribution"] = layer13_drift_attribution.assess(
                    pair, ctx, ctx.get("change_point"), ctx.get("regime_memory")
                )
            except Exception:
                log.exception("LAYER13_DRIFT_ATTRIBUTION_SKIPPED pair=%s", pair)
        # Layer 14: adaptive confidence calibration from mature replay outcomes. OBSERVE_ONLY.
        if getattr(cfg, "LAYER14_ADAPTIVE_CONFIDENCE_ENABLED", True):
            try:
                ctx["adaptive_confidence"] = layer14_adaptive_confidence.assess(pair, ctx)
            except Exception:
                log.exception("LAYER14_ADAPTIVE_CONFIDENCE_SKIPPED pair=%s", pair)
        # Layer 15: ordered event/setup lifecycle. OBSERVE_ONLY; no new vote or signal.
        if getattr(cfg, "LAYER15_EVENT_SEQUENCE_ENABLED", True):
            try:
                ctx["event_sequence"] = layer15_event_sequence.assess(
                    pair, side, texts, market.get(pair) or {}, ctx
                )
            except Exception:
                log.exception("LAYER15_EVENT_SEQUENCE_SKIPPED pair=%s", pair)
        # Layer 16: direct structured expert states. OBSERVE_ONLY; no vote/veto/signal.
        if getattr(cfg, "LAYER16_STRUCTURED_INTELLIGENCE_ENABLED", True):
            try:
                ctx["structured_intelligence"] = layer16_structured_intelligence.assess(
                    pair, side, market.get(pair) or {}, ctx
                )
                # Layer 15 remains backward compatible, but downstream consumers can
                # now use direct facts instead of parsing Telegram text.
                if isinstance(ctx.get("event_sequence"), dict):
                    ctx["event_sequence"]["structured_facts"] = ctx["structured_intelligence"].get("facts", [])
                    ctx["event_sequence"]["structured_sequence"] = ctx["structured_intelligence"].get("event_sequence", [])
            except Exception:
                log.exception("LAYER16_STRUCTURED_INTELLIGENCE_SKIPPED pair=%s", pair)
        # Layer 17: causal graph over canonical Layer 16 facts + Layer 15 order. OBSERVE_ONLY.
        if getattr(cfg, "LAYER17_MARKET_STATE_GRAPH_ENABLED", True):
            try:
                ctx["market_state_graph"] = layer17_market_state_graph.assess(
                    pair, side, ctx.get("structured_intelligence"), ctx.get("event_sequence")
                )
            except Exception:
                log.exception("LAYER17_MARKET_STATE_GRAPH_SKIPPED pair=%s", pair)
        # Layer 18: causal scenario-integrity audit over Layer 17. OBSERVE_ONLY.
        if getattr(cfg, "LAYER18_SCENARIO_INTEGRITY_ENABLED", True):
            try:
                ctx["scenario_integrity"] = layer18_scenario_integrity.assess(
                    pair, side, ctx.get("market_state_graph"),
                    ctx.get("structured_intelligence"), ctx.get("event_sequence")
                )
            except Exception:
                log.exception("LAYER18_SCENARIO_INTEGRITY_SKIPPED pair=%s", pair)
        # Layer 19: audit independent-vs-correlated evidence families. OBSERVE_ONLY.
        if getattr(cfg, "LAYER19_EVIDENCE_INDEPENDENCE_ENABLED", True):
            try:
                ctx["evidence_independence"] = layer19_evidence_independence.assess(
                    pair, side, ctx.get("structured_intelligence"), ctx.get("market_state_graph"),
                    ctx.get("scenario_integrity")
                )
            except Exception:
                log.exception("LAYER19_EVIDENCE_INDEPENDENCE_SKIPPED pair=%s", pair)
        # Layer 20: final diagnostic synthesis of the brain stack. OBSERVE_ONLY.
        if getattr(cfg, "LAYER20_DECISION_READINESS_ENABLED", True):
            try:
                ctx["decision_readiness"] = layer20_decision_readiness.assess(
                    pair, side, ctx.get("event_sequence"), ctx.get("structured_intelligence"),
                    ctx.get("market_state_graph"), ctx.get("scenario_integrity"),
                    ctx.get("evidence_independence")
                )
            except Exception:
                log.exception("LAYER20_DECISION_READINESS_SKIPPED pair=%s", pair)
        # Layer 21: unified mathematical coordinates. OBSERVE_ONLY.
        if getattr(cfg, "LAYER21_MATHEMATICAL_CORE_ENABLED", True):
            try:
                ctx["mathematical_core"] = layer21_mathematical_core.assess(
                    pair, side, ctx.get("structured_intelligence"), ctx.get("scenario_integrity"),
                    ctx.get("evidence_independence"), ctx.get("decision_readiness"),
                    ctx.get("adaptive_confidence")
                )
            except Exception:
                log.exception("LAYER21_MATHEMATICAL_CORE_SKIPPED pair=%s", pair)
        # Layer 22: mathematical robustness / consensus diagnostics. OBSERVE_ONLY.
        if getattr(cfg, "LAYER22_MATHEMATICAL_STABILITY_ENABLED", True):
            try:
                ctx["mathematical_stability"] = layer22_mathematical_stability.assess(
                    pair, side, ctx.get("structured_intelligence"), ctx.get("mathematical_core")
                )
            except Exception:
                log.exception("LAYER22_MATHEMATICAL_STABILITY_SKIPPED pair=%s", pair)
        # Layer 23: confidence in the mathematical conclusion itself. OBSERVE_ONLY.
        if getattr(cfg, "LAYER23_MATHEMATICAL_CONFIDENCE_ENABLED", True):
            try:
                ctx["mathematical_confidence"] = layer23_mathematical_confidence.assess(
                    pair, side, ctx.get("structured_intelligence"), ctx.get("mathematical_core"),
                    ctx.get("mathematical_stability")
                )
            except Exception:
                log.exception("LAYER23_MATHEMATICAL_CONFIDENCE_SKIPPED pair=%s", pair)
        # Layer 24: perturbation / timeframe-conflict resilience diagnostics. OBSERVE_ONLY.
        if getattr(cfg, "LAYER24_MATHEMATICAL_RESILIENCE_ENABLED", True):
            try:
                ctx["mathematical_resilience"] = layer24_mathematical_resilience.assess(
                    pair, side, ctx.get("structured_intelligence"), ctx.get("mathematical_core"),
                    ctx.get("mathematical_stability"), ctx.get("mathematical_confidence")
                )
            except Exception:
                log.exception("LAYER24_MATHEMATICAL_RESILIENCE_SKIPPED pair=%s", pair)
        # Layer 25: information value / evidence novelty diagnostics. OBSERVE_ONLY.
        if getattr(cfg, "LAYER25_INFORMATION_VALUE_ENABLED", True):
            try:
                ctx["information_value"] = layer25_information_value.assess(
                    pair, side, ctx.get("structured_intelligence"), ctx.get("mathematical_core"),
                    ctx.get("mathematical_stability"), ctx.get("mathematical_confidence"),
                    ctx.get("mathematical_resilience")
                )
            except Exception:
                log.exception("LAYER25_INFORMATION_VALUE_SKIPPED pair=%s", pair)
        # Layer 26: cross-estimator mathematical coherence diagnostics. OBSERVE_ONLY.
        if getattr(cfg, "LAYER26_MATHEMATICAL_COHERENCE_ENABLED", True):
            try:
                ctx["mathematical_coherence"] = layer26_mathematical_coherence.assess(
                    pair, ctx.get("mathematical_core"), ctx.get("mathematical_stability"),
                    ctx.get("mathematical_confidence"), ctx.get("mathematical_resilience"),
                    ctx.get("information_value")
                )
            except Exception:
                log.exception("LAYER26_MATHEMATICAL_COHERENCE_SKIPPED pair=%s", pair)
        # Layer 27: residual mathematical uncertainty decomposition. OBSERVE_ONLY.
        if getattr(cfg, "LAYER27_UNCERTAINTY_BUDGET_ENABLED", True):
            try:
                ctx["uncertainty_budget"] = layer27_uncertainty_budget.assess(
                    pair, ctx.get("mathematical_core"), ctx.get("mathematical_stability"),
                    ctx.get("mathematical_confidence"), ctx.get("mathematical_resilience"),
                    ctx.get("information_value"), ctx.get("mathematical_coherence")
                )
            except Exception:
                log.exception("LAYER27_UNCERTAINTY_BUDGET_SKIPPED pair=%s", pair)
        # Layer 28: mathematical distance-to-boundary / joint-shock margin. OBSERVE_ONLY.
        if getattr(cfg, "LAYER28_DECISION_MARGIN_ENABLED", True):
            try:
                ctx["decision_margin"] = layer28_decision_margin.assess(
                    pair, ctx.get("mathematical_core"), ctx.get("mathematical_stability"),
                    ctx.get("mathematical_confidence"), ctx.get("mathematical_resilience"),
                    ctx.get("information_value"), ctx.get("mathematical_coherence"),
                    ctx.get("uncertainty_budget")
                )
            except Exception:
                log.exception("LAYER28_DECISION_MARGIN_SKIPPED pair=%s", pair)
        ok, reason = verdict(ctx)
        # Passive audit trail: this call cannot alter the verdict or Telegram flow.
        try:
            for candidate in texts:
                decision_journal.record_decision(candidate, market, strength,
                    "ALLOWED" if ok else "BLOCKED", reason, texts[1:] if candidate == primary else [], ctx)
        except Exception:
            log.exception("DECISION_JOURNAL_CONTEXT_SKIPPED pair=%s", pair)
        if not ok:
            for text in texts:
                dropped.append((reason, text))
            continue
        bundle = {
            "primary": stamp(primary, ctx, reason, allies),
            "source_text": primary,
            "allies": allies,
            "pair": pair,
            "side": side,
            "reason": reason,
            "ctx": ctx,
        }
        prev = winners.get(pair)
        if prev:
            # Одна пара — одна сторона. Противника выкидываем, если он слабее контекста.
            prev_score = prev["ctx"]["directed_gap"] * 10 + prev["ctx"]["junior_n"]
            new_score = ctx["directed_gap"] * 10 + ctx["junior_n"]
            if new_score <= prev_score:
                try:
                    for candidate in texts:
                        decision_journal.record_decision(candidate, market, strength, "BLOCKED", "opposite_weaker", allies, ctx)
                except Exception:
                    log.exception("DECISION_JOURNAL_OPPOSITE_SKIPPED pair=%s", pair)
                dropped.append(("opposite_weaker", primary))
                for text in allies:
                    dropped.append(("opposite_weaker", text))
                continue
            try:
                decision_journal.record_decision(prev["source_text"], market, strength, "BLOCKED", "opposite_weaker", prev["allies"], prev["ctx"])
            except Exception:
                log.exception("DECISION_JOURNAL_OPPOSITE_SKIPPED pair=%s", pair)
            dropped.append(("opposite_weaker", prev["source_text"]))
            for text in prev["allies"]:
                dropped.append(("opposite_weaker", text))
        winners[pair] = bundle

    keep = list(winners.values())
    for text in neutral:
        keep.append({"primary": text, "source_text": text, "allies": [], "pair": "", "side": "", "reason": "neutral"})
    return keep, dropped
