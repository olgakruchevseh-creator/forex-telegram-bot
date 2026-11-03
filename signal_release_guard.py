"""Mandatory source-by-source H1 + trap + family gate before Master and Telegram.

One scanner is one causal family, even when its description mentions MSS/FVG/OB.
UNKNOWN auxiliary views never count as corroboration or an automatic veto.
The historical/research layers remain OBSERVE_ONLY. All hard vetoes here are
observable candle facts, parse failures, freshness or an invalidated entry.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config as cfg
from analysis import atr, closed_candles, analyze_tf
import killer_trap_guard
from signal_contract import pair_of, side_of, is_information_card, source_timeframe, event_close_utc

log = logging.getLogger("fxbot.release_guard")

# Every scanning module belongs to exactly one independent primary family.
SOURCE_FAMILIES = {
    "disbalance": "imbalance", "imbalance": "imbalance", "bpr": "imbalance",
    "quasimodo_engine": "structure", "consolidation_zone": "levels",
    "accumulation_distribution": "session", "amd_power_of_three": "session",
    "weekly_rhythm_context": "context", "crt_candle_range": "session",
    "liquidity_sweep": "liquidity", "poc_profile": "levels",
    "order_block": "blocks", "breaker_block": "blocks",
    "smart_money_62_26": "smc", "silver_bullet_ict": "session",
    "daily_high_low": "liquidity", "chain_entries": "blocks",
    "retest_confirmation": "structure", "fibonacci_grid": "smc",
    "ats_reversal_point": "reversal", "fib_smc": "smc", "levels": "levels",
    "zigzag_scanner": "structure", "edge_estimator": "context",
    "patterns": "structure", "killer_engine": "meta",
}

SOURCE_MARKERS = (
    ("KILLER —", "killer_engine"), ("ICT SILVER BULLET", "silver_bullet_ict"),
    ("📐 EDGE H1", "edge_estimator"), ("РИТМ НЕДЕЛИ — КОНТЕКСТ", "weekly_rhythm_context"),
    ("BALANCED PRICE RANGE", "bpr"), ("QUASIMODO", "quasimodo_engine"),
    ("ДИСБАЛАНС", "disbalance"), ("IMBALANCE", "imbalance"),
    ("ПАТТЕРН ПОДТВЕРЖДЁН", "patterns"), ("AMD / POWER OF THREE", "amd_power_of_three"),
    ("CANDLE RANGE THEORY", "crt_candle_range"), ("СНЯТИЕ ЛИКВИДНОСТИ", "liquidity_sweep"),
    ("ORDER BLOCK", "order_block"), ("BREAKER BLOCK", "breaker_block"),
    ("SMART MONEY 62", "smart_money_62_26"), ("CHAIN ENTRY", "chain_entries"),
    ("FIB + SMC", "fib_smc"), ("ATS REVERSAL", "ats_reversal_point"),
    ("ZIGZAG", "zigzag_scanner"), ("POC —", "poc_profile"),
    ("ВЫХОД ИЗ ЗОНЫ КОНСОЛИДАЦИИ", "consolidation_zone"),
    ("ВЫХОД ИЗ ФАЗЫ", "accumulation_distribution"),
    ("РЕТЕСТ", "retest_confirmation"), ("ФИБОНАЧЧИ", "fibonacci_grid"),
    ("ПРОБОЙ PDH", "daily_high_low"), ("ПРОБОЙ PDL", "daily_high_low"),
    ("СНЯТИЕ PDH", "daily_high_low"), ("СНЯТИЕ PDL", "daily_high_low"),
    ("ПРОБОЙ УРОВНЯ", "levels"), ("ОТБОЙ ОТ", "levels"),
)


def infer_source(text: str) -> str:
    # Legacy cards, including recovered persisted outbox entries.
    lines = [line.strip().upper() for line in (text or "").splitlines() if line.strip() and not line.strip().startswith("━")]
    header = "\n".join(lines[:5])
    for marker, source in SOURCE_MARKERS:
        if marker in header:
            return source
    return ""


@dataclass(frozen=True)
class Decision:
    status: str  # ALLOW, BLOCK, PENDING_H1, INFO
    reason: str
    pair: str
    side: str
    source: str
    family: str
    h1_closed_at: str = ""
    checks: tuple = ()

    @property
    def allowed(self):
        return self.status == "ALLOW"


def _h1_context(by_tf: dict, now: datetime):
    bars = closed_candles((by_tf or {}).get("H1") or [], 60)
    if len(bars) < 25:
        return bars, None, "h1_insufficient"
    try:
        raw = str(bars[-1].dt).replace("T", " ")[:19]
        opened = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        end = opened + timedelta(hours=1)
    except (ValueError, TypeError):
        return bars, None, "h1_time_invalid"
    age = (now - end).total_seconds() / 60
    max_minutes = max(1, int(getattr(cfg, "UNIFIED_H1_RELEASE_WINDOW_MINUTES", 16)))
    if age < 0:
        return bars, end, "h1_not_closed"
    if age > max_minutes:
        return bars, end, "h1_not_fresh"
    return bars, end, ""


def _family_view(pair, side, text, peers, ids):
    supporting = set()
    opposing = set()
    own_source = ids.get(text) or infer_source(text)
    for card in peers:
        if card == text or pair_of(card) != pair:
            continue
        s = side_of(card)
        source = ids.get(card) or infer_source(card)
        family = SOURCE_FAMILIES.get(source, "")
        if not family or family in ("meta", "context") or source == own_source:
            continue
        if s == side:
            supporting.add(family)
        elif s in ("LONG", "SHORT"):
            opposing.add(family)
    return supporting, opposing


def _price_from_line(text: str, name: str):
    for line in (text or "").splitlines():
        if re.search(name, line, re.I):
            m = re.search(r":\s*([0-9]+(?:[.,][0-9]+)?)\b", line)
            if m:
                try:
                    return float(m.group(1).replace(",", "."))
                except (ValueError, OverflowError):
                    return None
    return None


def _execution_check(text, side, bars):
    if not bars:
        return None
    stop = _price_from_line(text, r"^(?:Отмена(?:\s*/\s*SL)?|Стоп(?:-лосс)?|SL)\s*:")
    if stop is None:
        return None  # no artificial stop can be inferred from a source card
    close = float(bars[-1].close)
    if not math.isfinite(stop) or stop <= 0:
        return "invalid_stop"
    # An invalidation breached on the latest closed H1 cannot be published.
    if side == "LONG" and close <= stop:
        return "stop_already_breached"
    if side == "SHORT" and close >= stop:
        return "stop_already_breached"
    entry = _price_from_line(text, r"^(?:Вход(?:\s+CE\s+50%)?|Цена входа)\s*:")
    if entry is not None and (entry <= 0 or not math.isfinite(entry)
                              or (entry - stop) * (1 if side == "LONG" else -1) <= 0):
        return "invalid_entry_stop_geometry"
    return None


def review(text: str, market: dict, peers=(), source_ids=None, *, now=None, cache=None) -> Decision:
    """Pure verdict for one card, with optional per-scan trap cache.

    `peers` may only contain observed detector facts, never projected future bars.
    """
    source_ids = source_ids or {}
    source = source_ids.get(text) or infer_source(text)
    family = SOURCE_FAMILIES.get(source, "")
    pair, side = pair_of(text), side_of(text)
    if is_information_card(text):
        return Decision("INFO", "analytics_not_order", pair, side, source, family)
    if not pair or side not in ("LONG", "SHORT") or pair not in cfg.PAIRS:
        return Decision("BLOCK", "unrecognized_trade_identity", pair, side, source, family)
    if not family or family == "context":
        return Decision("BLOCK", "unregistered_trade_source", pair, side, source, family)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    by_tf = (market or {}).get(pair) or {}
    bars, end, h1_error = _h1_context(by_tf, now)
    checks = []
    tf = source_timeframe(text, source)
    event_end = event_close_utc(text, source)
    if tf in ("M5", "M15") and event_end is None:
        return Decision("BLOCK", "ltf_event_timestamp_unknown", pair, side, source, family,
                        end.isoformat() if end else "", tuple(checks))
    # An intrahour M5/M15 event is queued until a NEW H1 that contains it closes.
    # This is checked before the H1 release window, which is normally expired
    # during the middle of a live hour.
    if end is not None and event_end is not None and event_end > end + timedelta(seconds=2):
        if event_end > now + timedelta(seconds=2):
            return Decision("BLOCK", "future_event_timestamp", pair, side, source, family, end.isoformat(), tuple(checks))
        return Decision("PENDING_H1", "event_requires_later_closed_h1", pair, side, source, family,
                        end.isoformat(), tuple(checks))
    if h1_error:
        return Decision("BLOCK", h1_error, pair, side, source, family, end.isoformat() if end else "",
                        ({"check": "closed_h1", "status": "BLOCK", "reason": h1_error},))
    checks.append({"check": "closed_h1", "status": "PASS", "close": end.isoformat()})
    if event_end is not None:
        if (end - event_end).total_seconds() > 90 * 60:
            return Decision("BLOCK", "event_too_old", pair, side, source, family, end.isoformat(), tuple(checks))
        checks.append({"check": "event_confirmation", "status": "PASS", "source_tf": tf,
                       "event_close": event_end.isoformat()})
    elif tf == "UNKNOWN":
        return Decision("BLOCK", "unknown_source_timeframe", pair, side, source, family, end.isoformat(), tuple(checks))
    else:
        checks.append({"check": "event_confirmation", "status": "PASS", "source_tf": tf,
                       "note": "H1-confirmed detector contract"})
    stop_error = _execution_check(text, side, bars)
    if stop_error:
        return Decision("BLOCK", stop_error, pair, side, source, family, end.isoformat(), tuple(checks))
    checks.append({"check": "invalidation", "status": "PASS", "note": "checked if supplied"})
    # Individual HTF/H1 alignment: the Master votes per pair, so each source
    # must separately prove it is not simply signalling against BOTH H4/H1.
    tf_key = ("mtf", pair, end.isoformat())
    if cache is not None and tf_key in cache:
        views = cache[tf_key]
    else:
        views = {}
        for timeframe, minutes in (("W1", 10080), ("D1", 1440), ("H4", 240), ("H1", 60)):
            candles = closed_candles(by_tf.get(timeframe) or [], minutes)
            try:
                views[timeframe] = int(analyze_tf(timeframe, timeframe, candles).bias) if len(candles) >= 20 else 0
            except (ValueError, TypeError, ArithmeticError):
                views[timeframe] = 0
        if cache is not None:
            cache[tf_key] = views
    wanted = 1 if side == "LONG" else -1
    opposed = views.get("H4") == -wanted and views.get("H1") == -wanted
    checks.append({"check": "multi_tf_source", "status": "BLOCK" if opposed else
                   ("WARN" if views.get("H4") == -wanted else "PASS"),
                   "directions": views,
                   "note": "H4 disagreement alone may be a confirmed H1 pullback"})
    if opposed:
        return Decision("BLOCK", "h4_h1_both_opposite", pair, side, source, family, end.isoformat(), tuple(checks))
    supporters, opponents = _family_view(pair, side, text, peers, source_ids)
    checks.append({"check": "family_arbitration", "status": "WARN" if opponents else "PASS",
                   "independent_support": sorted(supporters), "independent_opposition": sorted(opponents),
                   "note": "one causal family per source; Master arbitrates contradictions"})
    # The actual trap engine is independent of which module produced this text.
    # Its H1 candle / double-trap / false-breakout veto is mandatory.
    key = (pair, side, end.isoformat())
    try:
        if cache is not None and key in cache:
            trap = cache[key]
        else:
            trap = killer_trap_guard.inspect(pair, side, by_tf)
            if cache is not None:
                cache[key] = trap
    except Exception:
        log.exception("UNIFIED_TRAP_CHECK_FAILED pair=%s side=%s", pair, side)
        return Decision("BLOCK", "trap_engine_error", pair, side, source, family, end.isoformat(), tuple(checks))
    trap_unknown = any(row.get("status") == "UNKNOWN" for row in trap.get("checks", []))
    checks.append({"check": "trap_matrix", "status": "BLOCK" if trap.get("blocked") else
                   ("WARN" if trap_unknown else "PASS"),
                   "risk": trap.get("risk"), "rows": trap.get("checks", [])})
    if trap.get("blocked"):
        return Decision("BLOCK", "trap_" + str(trap.get("reason") or "confirmed"), pair, side, source,
                        family, end.isoformat(), tuple(checks))
    return Decision("ALLOW", "confirmed_h1_family_traps_reviewed", pair, side, source, family,
                    end.isoformat(), tuple(checks))


def journal(decision: Decision, event_id: str = "") -> None:
    if not getattr(cfg, "UNIFIED_SIGNAL_JOURNAL_ENABLED", True):
        return
    root = Path(os.getenv("STATE_DIR", "") or Path(__file__).resolve().parent)
    path = root / "unified_signal_decisions.jsonl"
    payload = {**asdict(decision), "event_id": event_id,
               "reviewed_at_utc": datetime.now(timezone.utc).isoformat()}
    try:
        root.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 5_000_000:
            path.replace(root / "unified_signal_decisions.previous.jsonl")
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":")) + "\n")
    except OSError:
        log.exception("UNIFIED_SIGNAL_JOURNAL_FAILED")
