"""Shared quality contract for a *real* market-structure break.

This layer does not discover a second BOS/MSS/CHOCH.  It scores an already
identified structural break using the confirmation sequence used by the live
modules:

HTF rejection / important HTF location -> displacement -> closed structural
break -> fresh interest zone (FVG / origin block) -> later H1 retest/reclaim.

Only closed candles are read.  M15/M5 may add timing context elsewhere, but this
contract never lets them replace the H1 close used by the publishing modules.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Iterable

from analysis import Candle, atr, closed_candles

_TF_MINUTES = {"W1": 10080, "D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}
_HIGHER = {"H1": ("H4", "D1", "W1"), "H4": ("D1", "W1"), "D1": ("W1",)}


@dataclass(frozen=True)
class StructureBreakQuality:
    side: str
    break_tf: str
    score: int
    sign_count: int
    confirmed: bool
    htf_rejection: bool
    htf_zone: bool
    displacement: bool
    structure_break: bool
    interest_zone: bool
    retest_reclaim: bool
    displacement_atr: float = 0.0
    body_ratio: float = 0.0
    interest_zone_type: str = ""
    htf_timeframe: str = ""
    reason: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _time_key(value: str) -> float:
    text = str(value or "").strip()
    if not text:
        return float("-inf")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except Exception:
            continue
    return float("-inf")


def _bars(by_tf: dict, tf: str) -> list[Candle]:
    if tf not in _TF_MINUTES:
        return []
    return closed_candles((by_tf or {}).get(tf) or [], _TF_MINUTES[tf])


def _index_at_or_before(seq: list[Candle], dt: str) -> int:
    cutoff = _time_key(dt)
    if cutoff == float("-inf"):
        return len(seq) - 1
    eligible = [i for i, bar in enumerate(seq) if _time_key(bar.dt) <= cutoff]
    return eligible[-1] if eligible else -1


def _htf_reaction(by_tf: dict, side: int, break_tf: str, break_dt: str) -> tuple[bool, bool, str]:
    """Return (rejection, important-zone, timeframe) from closed senior candles."""
    for tf in _HIGHER.get(break_tf, ("H4", "D1")):
        seq = _bars(by_tf, tf)
        idx = _index_at_or_before(seq, break_dt)
        if idx < 12:
            continue
        seq = seq[:idx + 1]
        av = atr(seq, 14)
        if av <= 0:
            continue
        for i in range(len(seq) - 1, max(10, len(seq) - 5), -1):
            c = seq[i]
            prior = seq[max(0, i - 12):i]
            if len(prior) < 8:
                continue
            rng = max(c.high - c.low, 1e-12)
            if side > 0:
                level = min(x.low for x in prior)
                wick = min(c.open, c.close) - c.low
                zone = c.low <= level + 0.18 * av
                rejection = zone and c.close > level + 0.05 * av and wick / rng >= 0.22
            else:
                level = max(x.high for x in prior)
                wick = c.high - max(c.open, c.close)
                zone = c.high >= level - 0.18 * av
                rejection = zone and c.close < level - 0.05 * av and wick / rng >= 0.22
            if rejection:
                return True, True, tf
            if zone:
                return False, True, tf
    return False, False, ""


def _interest_zone(seq: list[Candle], break_i: int, side: int) -> tuple[bool, str]:
    """Find a fresh structural FVG or the last opposite origin candle around break."""
    if break_i < 1:
        return False, ""
    # A structural FVG becomes knowable only after the next closed candle.
    if break_i + 1 < len(seq):
        a, c = seq[break_i - 1], seq[break_i + 1]
        if side > 0 and c.low > a.high:
            return True, "FVG"
        if side < 0 and c.high < a.low:
            return True, "FVG"
    # Origin block fallback: last opposite candle immediately before displacement.
    for c in reversed(seq[max(0, break_i - 4):break_i]):
        opposite = c.close < c.open if side > 0 else c.close > c.open
        if not opposite:
            continue
        b = seq[break_i]
        if side > 0 and b.close > c.high:
            return True, "ORDER_BLOCK_ORIGIN"
        if side < 0 and b.close < c.low:
            return True, "ORDER_BLOCK_ORIGIN"
    return False, ""


def assess(
    by_tf: dict,
    side: str | int,
    level: float,
    break_dt: str,
    *,
    break_tf: str = "H1",
    retest_dt: str = "",
) -> StructureBreakQuality:
    s = 1 if side in (1, "LONG", "ЛОНГ") else -1 if side in (-1, "SHORT", "ШОРТ") else 0
    side_name = "LONG" if s > 0 else "SHORT" if s < 0 else ""
    seq = _bars(by_tf, break_tf)
    break_i = next((i for i, c in enumerate(seq) if str(c.dt) == str(break_dt)), -1)
    if break_i < 0:
        break_i = _index_at_or_before(seq, break_dt)
    if s == 0 or break_i < 0 or break_i >= len(seq):
        return StructureBreakQuality(side_name, break_tf, 0, 0, False, False, False, False, False, False, False,
                                     reason="нет точной закрытой свечи структурного пробоя")

    b = seq[break_i]
    av = atr(seq[:break_i + 1], 14)
    body = abs(b.close - b.open)
    rng = max(b.high - b.low, 1e-12)
    disp_atr = body / av if av > 0 else 0.0
    body_ratio = body / rng
    directional = (b.close - b.open) * s > 0
    displacement = bool(av > 0 and directional and disp_atr >= 0.55 and body_ratio >= 0.50)
    structure_break = bool((b.close > float(level) if s > 0 else b.close < float(level)) and directional)

    htf_rejection, htf_zone, htf_tf = _htf_reaction(by_tf, s, break_tf, break_dt)
    interest_zone, zone_type = _interest_zone(seq, break_i, s)

    retest_reclaim = False
    if retest_dt:
        h1 = _bars(by_tf, "H1")
        ri = next((i for i, c in enumerate(h1) if str(c.dt) == str(retest_dt)), -1)
        if ri < 0:
            ri = _index_at_or_before(h1, retest_dt)
        if 0 <= ri < len(h1):
            r = h1[ri]
            touched = r.low <= float(level) if s > 0 else r.high >= float(level)
            reclaimed = r.close > float(level) if s > 0 else r.close < float(level)
            retest_reclaim = bool(touched and reclaimed and (r.close - r.open) * s > 0)

    # A confirmed retest/reclaim of the broken structural level is itself a
    # breaker/role-flip zone.  Keep it in the same PD-array family rather than
    # inventing a second signal family when no three-candle FVG is visible.
    if retest_reclaim and not interest_zone:
        interest_zone, zone_type = True, "BREAKER_RETEST"

    signs = (htf_rejection, htf_zone, displacement, structure_break, interest_zone)
    sign_count = sum(bool(x) for x in signs)
    score = (
        15 * int(htf_rejection) + 10 * int(htf_zone) + 25 * int(displacement)
        + 25 * int(structure_break) + 15 * int(interest_zone) + 10 * int(retest_reclaim)
    )
    confirmed = bool(
        displacement and structure_break and retest_reclaim and sign_count >= 3
        and (interest_zone or htf_rejection or htf_zone)
    )
    reason = (
        f"{sign_count}/5 признаков; displacement {disp_atr:.2f} ATR; "
        f"зона {zone_type or 'не подтверждена'}; HTF {htf_tf or 'нет подтверждения'}; "
        f"H1 retest/reclaim {'да' if retest_reclaim else 'нет'}"
    )
    return StructureBreakQuality(
        side_name, break_tf, int(score), int(sign_count), confirmed,
        htf_rejection, htf_zone, displacement, structure_break, interest_zone, retest_reclaim,
        round(disp_atr, 4), round(body_ratio, 4), zone_type, htf_tf, reason,
    )
