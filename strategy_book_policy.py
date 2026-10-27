"""Shared strategy-suitability policy for Forex modules.

Adapted from the SP500 book strategy layer.  The books are roles in one ordered
cycle, not independent votes:
Donnelly (macro risk) -> Dalton (auction/phase) -> Elder (MTF hierarchy) ->
Brooks (price-action/trap execution quality).

The result is a bounded soft coordination delta only.  It cannot create/flip a
signal, change targets, or hard-block an existing module lifecycle.
"""
from __future__ import annotations

import re
from typing import Mapping

FAMILY_LABELS_RU = {
    "TREND_CONTINUATION": "продолжение тренда",
    "STRUCTURE_BREAKOUT": "структурный пробой/ретест",
    "LIQUIDITY_REVERSAL": "ликвидностный разворот",
    "RANGE_AUCTION": "аукцион/уровни диапазона",
    "RANGE_PHASE": "фаза накопления/баланса",
    "STATISTICAL": "статистическая модель",
    "CONTEXT_ONLY": "контекст",
    "UNKNOWN_FAMILY": "не классифицировано",
}
FIT_DELTA = {
    "FAVORED": 2,
    "COMPATIBLE": 0,
    "CONFIRMATION_REQUIRED": -2,
    "REDUCED_SUITABILITY": -4,
    "CONTEXT_ONLY": 0,
}


def family_from_alert(text: str) -> str:
    u = (text or "").upper()
    if any(x in u for x in ("СНЯТИЕ PDL", "СНЯТИЕ PDH", "СНЯТИЕ ЛИКВИДНОСТИ", "LIQUIDITY SWEEP", "ЛОВУШ", "FALSE BREAK", "ЛОЖНЫЙ ПРОБОЙ")):
        return "LIQUIDITY_REVERSAL"
    if any(x in u for x in ("CHAIN ENTRY", "ЦЕПНОЙ ВХОД", "BOS", "CHOCH", "MSS", "РЕТТЕСТ", "ПРОБОЙ УРОВНЯ", "BREAKER", "ORDER BLOCK")):
        return "STRUCTURE_BREAKOUT"
    if any(x in u for x in ("POC", "VALUE AREA", "ОТБОЙ ОТ", "УДЕРЖАНИЕ УРОВНЯ", "RBS", "SBR")):
        return "RANGE_AUCTION"
    if any(x in u for x in ("AMD", "POWER OF THREE", "НАКОПЛЕНИ", "СЖАТИЕ", "РАСПРЕДЕЛЕНИ", "COMPRESSION")):
        return "RANGE_PHASE"
    if any(x in u for x in ("ZIGZAG", "ФИБОНАЧЧИ", "FIBONACCI", "ПРОДОЛЖЕНИЕ", "TREND", "DAILY HIGH/LOW")):
        return "TREND_CONTINUATION"
    if any(x in u for x in ("EDGE", "СТАТИСТ")):
        return "STATISTICAL"
    if "ПАТТЕРН" in u:
        # Candle reversals are usually reaction/reversal families; classical
        # continuation figures remain trend/structure context.
        if any(x in u for x in ("МОЛОТ", "PIN BAR", "ПИН-БАР", "ПОГЛОЩ", "УТРЕНН", "ВЕЧЕРН", "ХАРАМИ", "ПИНЦЕТ")):
            return "LIQUIDITY_REVERSAL"
        return "STRUCTURE_BREAKOUT"
    return "CONTEXT_ONLY"


def _phase(context: Mapping | None) -> str:
    c = context if isinstance(context, Mapping) else {}
    return str(c.get("phase") or "TRANSITION")


def _regime(context: Mapping | None) -> str:
    c = context if isinstance(context, Mapping) else {}
    row = c.get("market_regime") if isinstance(c.get("market_regime"), Mapping) else {}
    return str(row.get("name") or "UNKNOWN")


def _auction_acceptance(context: Mapping | None, direction: str) -> bool:
    c = context if isinstance(context, Mapping) else {}
    auc = c.get("auction") if isinstance(c.get("auction"), Mapping) else {}
    state = str(auc.get("state") or "")
    d = str(auc.get("direction") or "NEUTRAL")
    return state in {"ACCEPTANCE_ABOVE_VALUE", "ACCEPTANCE_BELOW_VALUE"} and d == direction


def _fit(family: str, context: Mapping | None, candidate_direction: str) -> tuple[str, list[str]]:
    phase = _phase(context)
    regime = _regime(context)
    c = context if isinstance(context, Mapping) else {}
    main = str(c.get("main_direction") or "NEUTRAL")
    accepted = _auction_acceptance(c, candidate_direction)
    counter = main in {"LONG", "SHORT"} and candidate_direction in {"LONG", "SHORT"} and candidate_direction != main

    if family == "TREND_CONTINUATION":
        if phase == "CONTINUATION" and regime in {"TREND", "EXPANSION", "HIGH_VOLATILITY"} and not counter:
            return "FAVORED", ["направленный H4/H1 режим соответствует продолжению тренда"]
        if phase in {"CONTINUATION", "PULLBACK"} and not counter:
            return "COMPATIBLE", ["режим совместим с продолжением, но преимущество умеренное"]
        if phase in {"BALANCE_FORMING", "RANGE"} or regime in {"RANGE", "COMPRESSION"}:
            return "REDUCED_SUITABILITY", ["баланс/сжатие ухудшает качество trend-continuation входа"]
        return "CONFIRMATION_REQUIRED", ["сложный откат или конфликт H4 требует дополнительной геометрии"]

    if family == "STRUCTURE_BREAKOUT":
        if accepted:
            return "FAVORED", ["Dalton-style acceptance за Value Area поддерживает пробой"]
        if phase == "CONTINUATION" and regime in {"TREND", "EXPANSION"}:
            return "COMPATIBLE", ["направленный режим совместим со структурным пробоем"]
        if phase in {"BALANCE_FORMING", "RANGE"} or regime == "COMPRESSION":
            return "CONFIRMATION_REQUIRED", ["пробой из баланса требует принятия закрытыми H1"]
        return "CONFIRMATION_REQUIRED", ["структурный setup требует собственного подтверждения"]

    if family == "LIQUIDITY_REVERSAL":
        if phase in {"COMPLEX_PULLBACK", "BALANCE_FORMING", "RANGE"}:
            return "FAVORED" if not counter else "COMPATIBLE", ["режим допускает mean-reversion/ликвидностный разворот"]
        if phase == "CONTINUATION" and regime in {"TREND", "EXPANSION"} and counter:
            return "REDUCED_SUITABILITY", ["контртрендовый разворот против направленного H4 требует сильного подтверждения"]
        return "CONFIRMATION_REQUIRED", ["локальная реакция не равна подтверждённому развороту старшего направления"]

    if family in {"RANGE_AUCTION", "RANGE_PHASE"}:
        if phase in {"BALANCE_FORMING", "RANGE"} or regime in {"RANGE", "COMPRESSION"}:
            return "FAVORED", ["баланс/диапазон соответствует аукционной/фазовой логике"]
        if phase == "CONTINUATION" and regime in {"TREND", "EXPANSION"}:
            return "REDUCED_SUITABILITY", ["устойчивое расширение уменьшает пригодность диапазонной стратегии"]
        return "COMPATIBLE", ["переходный режим допускает фазовую интерпретацию"]

    if family == "STATISTICAL":
        return "COMPATIBLE", ["статистический модуль обязан опираться на собственный OOS edge"]
    return "CONTEXT_ONLY", ["контекстный слой не является отдельной стратегией входа"]


def _master_cycle(context: Mapping | None, candidate_direction: str, family: str,
                  weekly_bias: Mapping | None, turtle_context=None, pattern_failure=None) -> dict:
    c = context if isinstance(context, Mapping) else {}
    weekly = weekly_bias if isinstance(weekly_bias, Mapping) else {}
    auc = c.get("auction") if isinstance(c.get("auction"), Mapping) else {}
    main = str(c.get("main_direction") or "NEUTRAL")
    working = str(c.get("working_h1_direction") or "NEUTRAL")
    phase = _phase(c)

    macro = weekly.get("macro_week_risk") if isinstance(weekly.get("macro_week_risk"), Mapping) else {}
    donnelly_state = "CALENDAR_RISK_PRESENT" if (macro.get("high", 0) or macro.get("medium", 0)) else "NO_MAJOR_CALENDAR_RISK_IN_CONTEXT"
    donnelly = {
        "author": "Brent Donnelly",
        "role": "FX_MACRO_RISK_ONLY",
        "state": donnelly_state,
        "directional_vote": False,
        "counted_as_independent_vote": False,
        "can_block": False,
    }

    accepted = _auction_acceptance(c, candidate_direction)
    dalton = {
        "author": "James Dalton",
        "role": "AUCTION_AND_PHASE",
        "state": "ACCEPTANCE_CONFIRMED" if accepted else str(auc.get("state") or "UNAVAILABLE"),
        "phase": phase,
        "counted_as_independent_vote": False,
        "can_block": False,
    }

    if main not in {"LONG", "SHORT"}:
        elder_state = "PARENT_DIRECTION_UNRESOLVED"
    elif candidate_direction == main:
        elder_state = "WITH_H4_PARENT"
    elif working == candidate_direction and phase in {"PULLBACK", "COMPLEX_PULLBACK"}:
        elder_state = "H1_COUNTERLEG_INSIDE_H4"
    else:
        elder_state = "COUNTER_TO_H4_PARENT"
    elder = {
        "author": "Alexander Elder",
        "role": "MTF_HIERARCHY",
        "state": elder_state,
        "main_h4_direction": main,
        "working_h1_direction": working,
        "m15_m5_can_override_h1": False,
        "counted_as_independent_vote": False,
        "can_block": False,
    }

    turtle_score = getattr(turtle_context, "score", None)
    failure_score = getattr(pattern_failure, "score", None)
    brooks_state = "PRICE_ACTION_CONTEXT"
    if failure_score is not None and float(failure_score) >= 65:
        brooks_state = "PATTERN_FAILURE_RISK"
    elif turtle_score is not None and float(turtle_score) >= 65:
        brooks_state = "FOLLOW_THROUGH_SUPPORTED"
    brooks = {
        "author": "Al Brooks",
        "role": "PRICE_ACTION_EXECUTION_QUALITY",
        "state": brooks_state,
        "counted_as_independent_vote": False,
        "can_block": False,
    }
    return {
        "sequence": ("DONNELLY", "DALTON", "ELDER", "BROOKS"),
        "stages": {"DONNELLY": donnelly, "DALTON": dalton, "ELDER": elder, "BROOKS": brooks},
        "independent_vote_count": 0,
        "hard_block": False,
        "probability_claim": False,
        "targets_owner": "EXISTING_PRICE_PATH_OR_SOURCE_MODULE",
    }


def assess_candidate(symbol: str, candidate_direction: str, alerts: list[str],
                     context: Mapping | None, weekly_bias: Mapping | None = None,
                     turtle_context=None, pattern_failure=None) -> dict:
    matching = []
    for text in alerts or []:
        if symbol not in (text or ""):
            continue
        u = (text or "").upper()
        markers = ("LONG", "ЛОНГ") if candidate_direction == "LONG" else (("SHORT", "ШОРТ") if candidate_direction == "SHORT" else ())
        if markers and not any(marker in u for marker in markers):
            continue
        matching.append(text)
    families = []
    for text in matching:
        fam = family_from_alert(text)
        if fam not in families:
            families.append(fam)
    if not families:
        families = ["CONTEXT_ONLY"]

    # Use the most conservative bounded delta among actual families so several
    # correlated module descriptions cannot stack book bonuses.
    rows = []
    for fam in families:
        fit, reasons = _fit(fam, context, candidate_direction)
        rows.append({"family": fam, "fit_code": fit, "reasons_ru": tuple(reasons), "delta": FIT_DELTA.get(fit, 0)})
    primary = min(rows, key=lambda x: x["delta"])
    delta = int(primary["delta"])

    weekly = weekly_bias if isinstance(weekly_bias, Mapping) else {}
    wdir = str(weekly.get("direction") or "NEUTRAL")
    if wdir in {"LONG", "SHORT"}:
        if candidate_direction == wdir and primary["family"] in {"TREND_CONTINUATION", "STRUCTURE_BREAKOUT"}:
            delta += 1
        elif candidate_direction != wdir and primary["family"] == "TREND_CONTINUATION":
            delta -= 1
    delta = max(-5, min(3, delta))

    return {
        "symbol": symbol,
        "candidate_direction": candidate_direction,
        "families": tuple(families),
        "primary_family": primary["family"],
        "family_label_ru": FAMILY_LABELS_RU.get(primary["family"], FAMILY_LABELS_RU["UNKNOWN_FAMILY"]),
        "fit_code": primary["fit_code"],
        "quality_delta": delta,
        "details": tuple(rows),
        "weekly_bias_direction": wdir,
        "master_cycle": _master_cycle(context, candidate_direction, primary["family"], weekly, turtle_context, pattern_failure),
        "correlated_books_are_not_votes": True,
        "independent_vote_count": 0,
        "hard_block": False,
        "can_create_signal": False,
        "can_flip_direction": False,
        "targets_owner": "EXISTING_PRICE_PATH_OR_SOURCE_MODULE",
        "probability_claim": False,
        "live_effect": "SOFT_COORDINATION_ONLY",
    }
