"""Уровни поддержки и сопротивления. Только закрытые свечи, только факты."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import config as cfg
from analysis import Candle, atr, closed_candles

try:
    from analysis import TF_MINUTES
except ImportError:
    TF_MINUTES = {
        "W1": 7 * 24 * 60,
        "D1": 24 * 60,
        "H4": 240,
        "H1": 60,
        "M15": 15,
        "M5": 5,
    }

log = logging.getLogger("fxbot.levels")
LOCAL_TZ = ZoneInfo(getattr(cfg, "LOCAL_TZ_NAME", "Europe/Amsterdam"))
DEFAULT_STATE_PATH = Path(__file__).parent / "levels_state.json"
STATE_PATH = DEFAULT_STATE_PATH


def levels_state_path() -> Path:
    raw = os.getenv("STATE_DIR", "").strip()
    if raw:
        return Path(raw) / "levels_state.json"
    return STATE_PATH

TF_ORDER = ["W1", "D1", "H4", "H1", "M15", "M5"]
TF_WEIGHT = {"W1": 40, "D1": 30, "H4": 18, "H1": 10, "M15": 4, "M5": 2}
TF_PIVOT = {"W1": 3, "D1": 3, "H4": 3, "H1": 2, "M15": 2, "M5": 2}
TF_WIDTH_ATR = {"W1": 0.35, "D1": 0.28, "H4": 0.22, "H1": 0.18, "M15": 0.16, "M5": 0.14}
TF_MERGE_ATR = {"W1": 0.55, "D1": 0.45, "H4": 0.35, "H1": 0.28, "M15": 0.22, "M5": 0.18}
SENIOR = {"W1", "D1"}
WORKING = {"H4", "H1"}
MIN_NOTIFY_STRENGTH = 68
MIN_APPROACH_STRENGTH = 80
MAX_ZONES_PAIR = 18
INVALID_BEYOND = 3
EVENT_PRIORITY = {
    "role": 1,
    "hold": 2,
    "break": 3,
    "fail_res": 4,
    "fail_sup": 4,
    "retest": 5,
    "bounce_res": 6,
    "bounce_sup": 6,
    "invalid": 7,
    "new_level": 9,
    "approach": 99,
}
LOOKBACK = {"W1": 80, "D1": 160, "H4": 180, "H1": 180, "M15": 160, "M5": 140}


@dataclass
class Zone:
    zone_id: str
    symbol: str
    kind: str  # support / resistance
    low: float
    high: float
    mid: float
    tfs: list[str]
    strength: float
    reactions: int
    created_ts: float
    last_test_ts: float
    last_event: str
    last_event_ts: float
    state: str
    sent_events: list[str]
    broken_side: str = ""
    last_touch_dt: str = ""
    tests_recent: int = 0
    false_wicks: int = 0
    closes_beyond: int = 0
    break_dt: str = ""
    hold_dt: str = ""
    retest_dt: str = ""
    original_kind: str = ""
    counted_beyond_dts: list[str] = field(default_factory=list)

    @property
    def width(self) -> float:
        return max(self.high - self.low, 1e-8)


def _now() -> float:
    return time.time()


def amsterdam_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(LOCAL_TZ)


def fmt_price(symbol: str, price: float) -> str:
    if "JPY" in symbol:
        return f"{price:.3f}"
    return f"{price:.5f}"


def digits(symbol: str) -> int:
    return 3 if "JPY" in symbol else 5


def load_store() -> dict:
    dest = levels_state_path()
    src = DEFAULT_STATE_PATH if DEFAULT_STATE_PATH.exists() else STATE_PATH
    if not dest.exists() and src.exists() and src != dest:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(src.read_text())
        except Exception:
            log.exception("Не удалось перенести levels_state.json")
    if dest.exists():
        try:
            data = json.loads(dest.read_text())
            if isinstance(data, dict):
                data.setdefault("zones", {})
                data.setdefault("sent", {})
                data.setdefault("last_closed", {})
                data.setdefault("semantic_sent", {})
                data.setdefault("semantic_candles", {})
                data.setdefault("bootstrapped", False)
                data.setdefault("lock", 0)
                return data
        except Exception:
            log.exception("Не прочитался levels_state.json")
    return {"zones": {}, "sent": {}, "last_closed": {}, "semantic_sent": {}, "semantic_candles": {}, "bootstrapped": False, "lock": 0}


def save_store(store: dict) -> None:
    dest = levels_state_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2))
    tmp.replace(dest)


def acquire(store: dict) -> bool:
    now = _now()
    if now - float(store.get("lock") or 0) < 90:
        return False
    store["lock"] = now
    save_store(store)
    return True


def release(store: dict) -> None:
    store["lock"] = 0
    save_store(store)


def zone_from_dict(raw: dict) -> Zone:
    data = dict(raw)
    data.setdefault("break_dt", "")
    data.setdefault("hold_dt", "")
    data.setdefault("retest_dt", "")
    data.setdefault("original_kind", data.get("kind") or "")
    data.setdefault("broken_side", "")
    data.setdefault("last_touch_dt", "")
    data.setdefault("tests_recent", 0)
    data.setdefault("false_wicks", 0)
    data.setdefault("closes_beyond", 0)
    data.setdefault("counted_beyond_dts", [])
    data.setdefault("sent_events", [])
    return Zone(**{k: data[k] for k in Zone.__dataclass_fields__ if k != "width"})


def tf_closed(symbol: str, tf_key: str, candles: list[Candle]) -> list[Candle]:
    mins = TF_MINUTES.get(tf_key, 60)
    closed = closed_candles(candles or [], mins)
    n = LOOKBACK.get(tf_key, 120)
    return closed[-n:] if len(closed) > n else closed


def last_price(candles: list[Candle]) -> Optional[float]:
    if not candles:
        return None
    return candles[-1].close


def live_price(raw: list[Candle], closed: list[Candle]) -> Optional[float]:
    if raw:
        return raw[-1].close
    if closed:
        return closed[-1].close
    return None


def pivot_swings(candles: list[Candle], left: int) -> list[tuple[int, float, str, str]]:
    out = []
    n = len(candles)
    if n < left * 2 + 3:
        return out
    for i in range(left, n - left):
        hi = candles[i].high
        lo = candles[i].low
        if all(hi >= candles[j].high for j in range(i - left, i + left + 1) if j != i):
            out.append((i, hi, "resistance", candles[i].dt))
        if all(lo <= candles[j].low for j in range(i - left, i + left + 1) if j != i):
            out.append((i, lo, "support", candles[i].dt))
    return out


def impulse_origins(candles: list[Candle]) -> list[tuple[int, float, str, str]]:
    out = []
    if len(candles) < 8:
        return out
    a = atr(candles, 14) or 0
    if a <= 0:
        return out
    for i in range(3, len(candles) - 1):
        body = abs(candles[i].close - candles[i].open)
        if body < a * 1.15:
            continue
        if candles[i].close > candles[i].open:
            out.append((i - 1, candles[i - 1].low, "support", candles[i - 1].dt))
        else:
            out.append((i - 1, candles[i - 1].high, "resistance", candles[i - 1].dt))
    return out[-20:]


def session_extremes(tf_key: str, candles: list[Candle]) -> list[tuple[int, float, str, str]]:
    if not candles:
        return []
    out = []
    if tf_key == "D1" and len(candles) >= 2:
        c = candles[-1]
        out.append((len(candles) - 1, c.high, "resistance", c.dt))
        out.append((len(candles) - 1, c.low, "support", c.dt))
    if tf_key == "W1" and len(candles) >= 2:
        c = candles[-1]
        out.append((len(candles) - 1, c.high, "resistance", c.dt))
        out.append((len(candles) - 1, c.low, "support", c.dt))
    return out


def zone_width(symbol: str, tf_key: str, candles: list[Candle], price: float) -> float:
    a = atr(candles, 14) if len(candles) >= 16 else 0.0
    k = TF_WIDTH_ATR.get(tf_key, 0.2)
    if a > 0:
        w = a * k
    else:
        w = price * (0.0008 if "JPY" not in symbol else 0.0006)
    min_w = 0.003 if "JPY" in symbol else 0.00008
    max_w = (0.12 if "JPY" in symbol else 0.0022) * (1.6 if tf_key in SENIOR else 1.0)
    return max(min_w, min(w, max_w))


def merge_tol(symbol: str, tf_key: str, candles: list[Candle], price: float) -> float:
    a = atr(candles, 14) if len(candles) >= 16 else 0.0
    k = TF_MERGE_ATR.get(tf_key, 0.3)
    if a > 0:
        t = a * k
    else:
        t = zone_width(symbol, tf_key, candles, price) * 1.4
    cap = (0.18 if "JPY" in symbol else 0.0030) * (1.5 if tf_key in SENIOR else 1.0)
    return min(t, cap)


def make_id(symbol: str, mid: float, kind: str) -> str:
    step = 0.01 if "JPY" in symbol else 0.0001
    bucket = round(mid / step)
    raw = f"{symbol}|{kind}|{bucket}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def collect_raw_levels(symbol: str, tf_key: str, candles: list[Candle]) -> list[dict]:
    if len(candles) < 20:
        return []
    price = candles[-1].close
    half = zone_width(symbol, tf_key, candles, price) / 2
    left = TF_PIVOT.get(tf_key, 2)
    pts = pivot_swings(candles, left) + impulse_origins(candles) + session_extremes(tf_key, candles)
    out = []
    for idx, px, kind, dt in pts:
        if px <= 0:
            continue
        move = 0.0
        if 0 <= idx < len(candles) - 1:
            nxt = candles[min(idx + 4, len(candles) - 1)]
            move = abs(nxt.close - px) / (atr(candles, 14) or px * 0.001)
        out.append(
            {
                "tf": tf_key,
                "kind": kind,
                "price": px,
                "low": px - half,
                "high": px + half,
                "dt": dt,
                "idx": idx,
                "impulse": min(move, 3.0),
            }
        )
    return out


def cluster_tf(symbol: str, tf_key: str, candles: list[Candle], raw: list[dict]) -> list[dict]:
    if not raw:
        return []
    price = candles[-1].close if candles else raw[0]["price"]
    tol = merge_tol(symbol, tf_key, candles, price)
    raw = sorted(raw, key=lambda x: x["price"])
    groups: list[list[dict]] = []
    for item in raw:
        if groups and abs(item["price"] - groups[-1][-1]["price"]) <= tol and item["kind"] == groups[-1][-1]["kind"]:
            groups[-1].append(item)
        else:
            groups.append([item])
    clustered = []
    for g in groups:
        prices = [x["price"] for x in g]
        lows = [x["low"] for x in g]
        highs = [x["high"] for x in g]
        dts = {x["dt"] for x in g}
        mid = sum(prices) / len(prices)
        clustered.append(
            {
                "tf": tf_key,
                "kind": g[0]["kind"],
                "mid": mid,
                "low": min(lows),
                "high": max(highs),
                "reactions": max(1, len(dts)),
                "impulse": max(x["impulse"] for x in g),
                "last_dt": max(x["dt"] for x in g),
            }
        )
    return clustered


def merge_cluster(symbol: str, items: list[dict], atr_map: dict[str, float]) -> list[Zone]:
    if not items:
        return []
    items = sorted(items, key=lambda x: (x["kind"], x["mid"]))
    used = [False] * len(items)
    zones: list[Zone] = []
    now = _now()
    for i, a in enumerate(items):
        if used[i]:
            continue
        pack = [a]
        used[i] = True
        base_tol = atr_map.get(a["tf"], abs(a["high"] - a["low"])) * 0.9
        pip = 0.01 if "JPY" in symbol else 0.0001
        absolute_cap = pip * float(getattr(cfg, "LEVEL_MAX_ZONE_PIPS", 45))
        h1_atr = atr_map.get("H1", 0.0)
        dynamic_cap = h1_atr * float(getattr(cfg, "LEVEL_MAX_ZONE_H1_ATR", 3.0)) if h1_atr > 0 else absolute_cap
        max_span = min(absolute_cap, dynamic_cap) if dynamic_cap > 0 else absolute_cap
        for j in range(i + 1, len(items)):
            if used[j]:
                continue
            b = items[j]
            if a["kind"] != b["kind"]:
                continue
            candidate_low = min([p["low"] for p in pack] + [b["low"]])
            candidate_high = max([p["high"] for p in pack] + [b["high"]])
            candidate_span = candidate_high - candidate_low
            near = abs(a["mid"] - b["mid"]) <= max(base_tol, atr_map.get(b["tf"], base_tol) * 0.9)
            if near and candidate_span <= max_span:
                pack.append(b)
                used[j] = True
        tfs = sorted({p["tf"] for p in pack}, key=lambda t: TF_ORDER.index(t) if t in TF_ORDER else 9)
        if tfs == ["M5"] or tfs == ["M15"] or set(tfs) <= {"M5", "M15"}:
            # локальные зоны живут внутри, но слабые
            pass
        low = min(p["low"] for p in pack)
        high = max(p["high"] for p in pack)
        mid = (low + high) / 2
        reactions = sum(p["reactions"] for p in pack)
        zid = make_id(symbol, mid, a["kind"])
        strength = score_zone(tfs, reactions, pack, now)
        zones.append(
            Zone(
                zone_id=zid,
                symbol=symbol,
                kind=a["kind"],
                low=low,
                high=high,
                mid=mid,
                tfs=tfs,
                strength=strength,
                reactions=reactions,
                created_ts=now,
                last_test_ts=0,
                last_event="",
                last_event_ts=0,
                state="активна",
                sent_events=[],
            )
        )
    zones.sort(key=lambda z: z.strength, reverse=True)
    return zones[:MAX_ZONES_PAIR]


def score_zone(tfs: list[str], reactions: int, pack: list[dict], now: float) -> float:
    w = sum(TF_WEIGHT.get(t, 0) for t in tfs)
    senior = any(t in SENIOR for t in tfs)
    if not senior:
        w = min(w, 38)
    react = min(reactions, 6) * 6
    impulse = min(max((p["impulse"] for p in pack), default=0) * 8, 16)
    multi = 12 if len(tfs) >= 2 else 0
    multi += 8 if len(set(tfs) & SENIOR) and len(set(tfs) & WORKING) else 0
    raw = w * 0.9 + react + impulse + multi
    # свежесть по числу паков старших ТФ
    if "M5" in tfs and not (set(tfs) & (SENIOR | WORKING)):
        raw *= 0.45
    return max(0.0, min(100.0, raw))


def absorb_penalty(zone: Zone) -> float:
    extra = max(0, zone.tests_recent - 2) * 7
    extra += zone.false_wicks * 3
    extra += zone.closes_beyond * 8
    age_h = (_now() - zone.created_ts) / 3600
    if age_h > 72 and zone.reactions < 3:
        extra += 10
    return min(35.0, extra)


def price_vs_zone(price: float, zone: Zone) -> str:
    if zone.low <= price <= zone.high:
        return "inside"
    if price < zone.low:
        return "below"
    return "above"


def wick_in_zone(c: Candle, zone: Zone) -> bool:
    return c.low <= zone.high and c.high >= zone.low


def closed_beyond(c: Candle, zone: Zone, side: str, buffer: float) -> bool:
    if side == "up":
        return c.close > zone.high + buffer and max(c.open, c.close) > zone.high
    return c.close < zone.low - buffer and min(c.open, c.close) < zone.low


def closed_back(c: Candle, zone: Zone, kind: str) -> bool:
    if kind == "resistance":
        return c.close < zone.low and c.high >= zone.low
    return c.close > zone.high and c.low <= zone.high


def reaction_size(c: Candle, zone: Zone, atr_v: float) -> bool:
    body = abs(c.close - c.open)
    need = max(atr_v * 0.12, zone.width * 0.25)
    return body >= need or abs(c.close - zone.mid) >= need


def pick_work_tf(zone: Zone) -> str:
    """W1/D1/H4 сохраняют происхождение, реакция подтверждается закрытой H1."""
    tfs = set(zone.tfs or [])
    if tfs & {"W1", "D1", "H4", "H1"}:
        return "H1"
    if "M15" in tfs and "M5" in tfs:
        return "M15"
    if "M15" in tfs:
        return "M15"
    if "M5" in tfs:
        return "M5"
    return "H1"


def _overlap_ratio(a: Zone, b: Zone) -> float:
    if a.symbol != b.symbol:
        return 0.0
    span = max(a.width, b.width, 1e-12)
    ov = min(a.high, b.high) - max(a.low, b.low)
    return ov / span if ov > 0 else 0.0


def _kinds_same_level(saved: Zone, fresh: Zone) -> bool:
    if saved.symbol != fresh.symbol:
        return False
    if saved.kind == fresh.kind:
        return True
    if saved.state in ("роль изменена", "ретест подтверждён"):
        return True
    if (saved.original_kind or saved.kind) == fresh.kind:
        return True
    if saved.kind == (fresh.original_kind or fresh.kind):
        return True
    return False


def attach_existing(old: dict[str, Zone], fresh: list[Zone]) -> list[Zone]:
    out = []
    used_old = set()
    for z in fresh:
        match = None
        for oid, oz in old.items():
            if oid in used_old:
                continue
            if not _kinds_same_level(oz, z):
                continue
            if _overlap_ratio(oz, z) >= 0.45:
                match = oz
                used_old.add(oid)
                break
        if match is None:
            for attached in out:
                if _overlap_ratio(attached, z) >= 0.45:
                    match = attached
                    break
            if match is not None:
                continue
        if match:
            z.zone_id = match.zone_id
            z.created_ts = match.created_ts
            z.last_test_ts = match.last_test_ts
            z.last_event = match.last_event
            z.last_event_ts = match.last_event_ts
            z.state = match.state
            z.sent_events = list(match.sent_events)
            z.broken_side = match.broken_side
            z.last_touch_dt = match.last_touch_dt
            z.tests_recent = match.tests_recent
            z.false_wicks = match.false_wicks
            z.closes_beyond = match.closes_beyond
            z.break_dt = match.break_dt
            z.hold_dt = match.hold_dt
            z.retest_dt = match.retest_dt
            z.original_kind = match.original_kind or match.kind
            z.counted_beyond_dts = list(match.counted_beyond_dts or [])
            z.kind = match.kind if match.state in ("роль изменена", "ретест подтверждён") else z.kind
            z.reactions = max(z.reactions, match.reactions)
            z.strength = max(0, min(100, z.strength - absorb_penalty(z)))
            # существенное расширение границ — новое событие позже
            span_old = match.width
            if match.state not in ("роль изменена", "ретест подтверждён", "пробита", "удержание подтверждено") and abs(z.mid - match.mid) > span_old * 0.6:
                z.zone_id = make_id(z.symbol, z.mid, z.kind) + "n"
        out.append(z)
    # сохранить недавно активные, если свежий поиск их не нашёл, но не invalid
    for oid, oz in old.items():
        if oid in used_old:
            continue
        if oz.state == "недействительна":
            continue
        if _now() - oz.last_event_ts < 36 * 3600 or _now() - oz.created_ts < 12 * 3600:
            oz.strength = max(0, oz.strength - 4)
            out.append(oz)
    return out


def event_key(zone: Zone, event: str, candle_dt: str = "") -> str:
    if candle_dt:
        return f"{zone.zone_id}:{event}:{candle_dt}"
    return f"{zone.zone_id}:{event}"


def note_beyond(zone: Zone, dt: str) -> bool:
    if not dt:
        return False
    if dt in zone.counted_beyond_dts:
        return False
    zone.counted_beyond_dts.append(dt)
    zone.closes_beyond = len(zone.counted_beyond_dts)
    return True


REPEATABLE = {
    "bounce_res", "bounce_sup", "break", "hold", "retest", "role",
    "fail_res", "fail_sup", "invalid",
}


def semantic_candle_key(zone: Zone, event: str, side: str, candle_dt: str) -> str:
    return f"{zone.symbol}:{event}:{side}:{candle_dt}"


def semantic_recent(store: dict, zone: Zone, event: str, side: str, candle_dt: str = "") -> bool:
    """Подавляет повтор даже после сдвига границ и смены zone_id."""
    if not side:
        return False
    if candle_dt:
        exact = semantic_candle_key(zone, event, side, candle_dt)
        if exact in (store.get("semantic_candles") or {}):
            return True
        # Совместимость с предыдущей версией: ищем то же событие/свечу
        # по старому zone_id, пока новый точный индекс ещё не создан.
        sent = store.get("sent") or {}
        for old_id, raw in (store.get("zones") or {}).items():
            if isinstance(raw, dict) and raw.get("symbol") == zone.symbol:
                if f"{old_id}:{event}:{candle_dt}" in sent:
                    return True
    key = f"{zone.symbol}:{event}:{side}"
    last = float((store.get("semantic_sent") or {}).get(key) or 0)
    if event in ("role", "invalid"):
        cooldown = 6 * 3600
    else:
        cooldown = float(getattr(cfg, "LEVEL_EVENT_COOLDOWN_MINUTES", 55)) * 60
    return _now() - last < cooldown


def mark_semantic(store: dict, zone: Zone, event: str, side: str, candle_dt: str = "") -> None:
    if side:
        store.setdefault("semantic_sent", {})[f"{zone.symbol}:{event}:{side}"] = _now()
        if candle_dt:
            store.setdefault("semantic_candles", {})[
                semantic_candle_key(zone, event, side, candle_dt)
            ] = _now()


def already_sent(store: dict, zone: Zone, event: str, candle_dt: str = "") -> bool:
    sent = store.get("sent") or {}
    if event == "new_level":
        return "new_level" in zone.sent_events or event_key(zone, "new_level") in sent
    if event in REPEATABLE:
        if not candle_dt:
            return False
        return event_key(zone, event, candle_dt) in sent
    if candle_dt and event_key(zone, event, candle_dt) in sent:
        return True
    return False


def mark_sent(store: dict, zone: Zone, event: str, candle_dt: str = "") -> None:
    zone.last_event = event
    zone.last_event_ts = _now()
    store.setdefault("sent", {})
    if event == "new_level":
        if "new_level" not in zone.sent_events:
            zone.sent_events.append("new_level")
        store["sent"][event_key(zone, "new_level")] = _now()
        return
    store["sent"][event_key(zone, event, candle_dt or "")] = _now()
    if event in REPEATABLE and event in zone.sent_events:
        zone.sent_events = [x for x in zone.sent_events if x != event]


def format_tfs(tfs: list[str]) -> str:
    return " · ".join(tfs)


def header_event(event: str) -> str:
    return {
        "new_level": "📍 СИЛЬНЫЙ УРОВЕНЬ",
        "bounce_res": "↘️ ОТБОЙ ОТ СОПРОТИВЛЕНИЯ",
        "bounce_sup": "↗️ ОТБОЙ ОТ ПОДДЕРЖКИ",
        "break": "⚡ ПРОБОЙ УРОВНЯ",
        "hold": "📌 УДЕРЖАНИЕ ПОДТВЕРЖДЕНО",
        "fail_res": "↘️ ЛОЖНЫЙ ПРОБОЙ СОПРОТИВЛЕНИЯ",
        "fail_sup": "↗️ ЛОЖНЫЙ ПРОБОЙ ПОДДЕРЖКИ",
        "retest": "🔄 РЕТТЕСТ УРОВНЯ",
        "role": "🔄 СМЕНА РОЛИ УРОВНЯ",
        "invalid": "❌ УРОВЕНЬ НЕДЕЙСТВИТЕЛЕН",
        "approach": "📍 ПОДХОД К СТАРШЕЙ ЗОНЕ",
    }.get(event, "📍 УРОВЕНЬ")


def kind_ru(kind: str) -> str:
    return "ПОДДЕРЖКА" if kind == "support" else "СОПРОТИВЛЕНИЕ"


def reaction_metrics(zone: Zone, candle: Candle | None, atr_v: float, event: str = "") -> tuple[int, int]:
    """Оценка основана на силе зоны и размере подтверждающей реакции."""
    body_atr = abs(candle.close - candle.open) / max(atr_v, 1e-12) if candle else 0.0
    multi = min(12, max(0, len(zone.tfs) - 1) * 3)
    impulse = min(18, int(body_atr * 12))
    quality = int(max(60, min(94, zone.strength * 0.62 + multi + impulse + 12)))
    confidence = max(58, min(91, quality - 4))
    # Первый пробой ещё не доказал удержание следующей свечой.
    if event == "break":
        quality = min(86, quality)
        confidence = min(82, confidence)
    return quality, confidence


def build_message(
    zone: Zone,
    event: str,
    extra: str,
    side: str = "",
    confirmation_tf: str = "",
    close_price: float | None = None,
    quality: int | None = None,
    confidence: int | None = None,
) -> str:
    lines = [
        "━━━━━━━━━━━━━━━━━━",
        header_event(event),
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"💱 Пара: {zone.symbol}",
    ]
    if event == "role":
        was = "СОПРОТИВЛЕНИЕ" if zone.kind == "support" else "ПОДДЕРЖКА"
        lines.append(f"🧱 Было: {was}")
        lines.append(f"🛡 Стало: {kind_ru(zone.kind)}")
    elif event in ("hold", "retest") and zone.broken_side:
        former = "БЫВШЕЕ СОПРОТИВЛЕНИЕ" if zone.broken_side == "up" else "БЫВШАЯ ПОДДЕРЖКА"
        lines.append(f"🧱 Тип: {former}")
    else:
        lines.append(f"🧱 Тип: {kind_ru(zone.kind)}")
    lines.append(f"📊 Таймфреймы уровня: {format_tfs(zone.tfs)}")
    lines.append(
        f"📍 Зона: {fmt_price(zone.symbol, zone.low)}–{fmt_price(zone.symbol, zone.high)}"
    )
    if event == "new_level":
        lines.append(f"💪 Сила: {zone.strength:.0f}/100")
        lines.append(f"🔎 Подтверждение: {zone.reactions} независимые реакции")
    if side:
        icon = "🟢" if side == "LONG" else "🔴"
        lines.append(f"{icon} Направление реакции: {side}")
    if confirmation_tf:
        lines.append(f"🕯 Подтверждение реакции: закрытая {confirmation_tf}-свеча")
    if close_price is not None:
        lines.append(f"💵 Цена закрытия: {fmt_price(zone.symbol, close_price)}")
    if quality is not None and confidence is not None:
        lines.append(f"💪 Качество реакции: {quality}/100")
        lines.append(f"📈 Вероятность: {confidence}%")
    lines.append(f"✅ Факт: {extra}")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def detect_events(
    zone: Zone,
    closed_by_tf: dict[str, list[Candle]],
    live: float,
    atr_map: dict[str, float],
    last_closed_map: dict[str, str],
    bootstrap: bool,
) -> list[tuple[str, str, str]]:
    """Возвращает список (event, fact, side). Пусто при неопределённости."""
    if bootstrap:
        return []
    work = pick_work_tf(zone)
    candles = closed_by_tf.get(work) or []
    if len(candles) < 2:
        return []
    c0 = candles[-2]
    c1 = candles[-1]
    atr_v = atr_map.get(work, zone.width)
    buf = max(atr_v * 0.08, zone.width * 0.15)
    events: list[tuple[str, str, str]] = []
    if not zone.original_kind:
        zone.original_kind = zone.kind

    # ложный прокол
    if wick_in_zone(c1, zone) and not (
        closed_beyond(c1, zone, "up", buf) or closed_beyond(c1, zone, "down", buf)
    ):
        if zone.kind == "resistance" and c1.high > zone.high and c1.close < zone.high:
            zone.false_wicks += 1
        if zone.kind == "support" and c1.low < zone.low and c1.close > zone.low:
            zone.false_wicks += 1

    # отбой
    if zone.state in ("активна", "протестирована", "отбой подтверждён", "ослаблена"):
        if zone.kind == "resistance" and closed_back(c1, zone, "resistance") and reaction_size(c1, zone, atr_v):
            if c1.high >= zone.low:
                zone.tests_recent += 1
                zone.last_test_ts = _now()
                zone.last_touch_dt = c1.dt
                zone.state = "отбой подтверждён"
                events.append(
                    (
                        "bounce_res",
                        f"Цена протестировала сопротивление и закрылась ниже зоны. Отбой подтверждён закрытой {work}-свечой.",
                        "SHORT",
                    )
                )
        elif zone.kind == "support" and closed_back(c1, zone, "support") and reaction_size(c1, zone, atr_v):
            if c1.low <= zone.high:
                zone.tests_recent += 1
                zone.last_test_ts = _now()
                zone.last_touch_dt = c1.dt
                zone.state = "отбой подтверждён"
                events.append(
                    (
                        "bounce_sup",
                        f"Цена протестировала поддержку и закрылась выше зоны. Отбой подтверждён закрытой {work}-свечой.",
                        "LONG",
                    )
                )

    # A breakout is a crossing event, not merely a candle that happens to be
    # beyond an old zone. The previous closed candle must still be on/before
    # the boundary and the newest one must close through it.
    broke_up = (
        c0.close <= zone.high + buf
        and closed_beyond(c1, zone, "up", buf)
        and (c1.close - c1.open) > 0
    )
    broke_dn = (
        c0.close >= zone.low - buf
        and closed_beyond(c1, zone, "down", buf)
        and (c1.close - c1.open) < 0
    )
    still_up = c1.close > zone.high + buf
    still_dn = c1.close < zone.low - buf
    returned = zone.low <= c1.close <= zone.high or (
        zone.broken_side == "up" and c1.close < zone.low
    ) or (
        zone.broken_side == "down" and c1.close > zone.high
    )

    # 1) пробой — только первое закрытие за зоной
    if zone.state in ("активна", "протестирована", "отбой подтверждён", "ослаблена") and not zone.break_dt:
        if broke_up and zone.kind == "resistance":
            note_beyond(zone, c1.dt)
            zone.state = "пробита"
            zone.broken_side = "up"
            zone.break_dt = c1.dt
            events.append(("break", "Цена закрылась выше сопротивления. Пробой подтверждён закрытой свечой.", "LONG"))
        elif broke_dn and zone.kind == "support":
            note_beyond(zone, c1.dt)
            zone.state = "пробита"
            zone.broken_side = "down"
            zone.break_dt = c1.dt
            events.append(("break", "Цена закрылась ниже поддержки. Пробой подтверждён закрытой свечой.", "SHORT"))

    # 2) после пробоя: удержание или ложный пробой — другая свеча
    elif zone.state == "пробита" and zone.break_dt and not zone.hold_dt:
        if c1.dt != zone.break_dt and c1.dt > zone.break_dt:
            if zone.broken_side == "up" and still_up:
                note_beyond(zone, c1.dt)
                zone.hold_dt = c1.dt
                zone.state = "удержание подтверждено"
                events.append(("hold", "Следующая закрытая свеча удержалась выше зоны. Удержание подтверждено.", "LONG"))
            elif zone.broken_side == "down" and still_dn:
                note_beyond(zone, c1.dt)
                zone.hold_dt = c1.dt
                zone.state = "удержание подтверждено"
                events.append(("hold", "Следующая закрытая свеча удержалась ниже зоны. Удержание подтверждено.", "SHORT"))
            elif returned or (zone.broken_side == "up" and c1.close < zone.high) or (zone.broken_side == "down" and c1.close > zone.low):
                zone.state = "активна"
                zone.kind = zone.original_kind or zone.kind
                zone.break_dt = ""
                zone.hold_dt = ""
                zone.retest_dt = ""
                zone.broken_side = ""
                zone.counted_beyond_dts = []
                zone.closes_beyond = 0
                if zone.kind == "resistance" and c1.close < zone.high:
                    events.append(("fail_res", "Пробой сопротивления не удержался. Цена вернулась вниз. Ложный пробой подтверждён.", "SHORT"))
                elif zone.kind == "support" and c1.close > zone.low:
                    events.append(("fail_sup", "Пробой поддержки не удержался. Цена вернулась вверх. Ложный пробой подтверждён.", "LONG"))

    # 3) ретест — отдельная свеча после удержания
    elif zone.state == "удержание подтверждено" and zone.hold_dt and not zone.retest_dt:
        if c1.dt != zone.hold_dt and c1.dt > zone.hold_dt and c1.dt != zone.break_dt:
            if zone.broken_side == "up" and c1.close >= zone.high - buf * 0.15 and c1.low <= zone.high:
                zone.retest_dt = c1.dt
                zone.last_touch_dt = c1.dt
                zone.state = "ретест подтверждён"
                events.append(("retest", "После удержания цена вернулась к зоне и закрылась сверху. Ретест подтверждён.", "LONG"))
            elif zone.broken_side == "down" and c1.close <= zone.low + buf * 0.15 and c1.high >= zone.low:
                zone.retest_dt = c1.dt
                zone.last_touch_dt = c1.dt
                zone.state = "ретест подтверждён"
                events.append(("retest", "После удержания цена вернулась к зоне и закрылась снизу. Ретест подтверждён.", "SHORT"))

    # 4) смена роли — только после ретеста и на более поздней свече
    elif zone.state == "ретест подтверждён" and zone.retest_dt:
        if c1.dt != zone.retest_dt and c1.dt > zone.retest_dt:
            if zone.broken_side == "up" and c1.close > zone.mid:
                zone.kind = "support"
                zone.state = "роль изменена"
                events.append(("role", "После подтверждённого ретеста зона работает как поддержка. Смена роли подтверждена.", "LONG"))
            elif zone.broken_side == "down" and c1.close < zone.mid:
                zone.kind = "resistance"
                zone.state = "роль изменена"
                events.append(("role", "После подтверждённого ретеста зона работает как сопротивление. Смена роли подтверждена.", "SHORT"))

    # дополнительные закрытия за зоной после удержания
    if zone.state in ("удержание подтверждено", "ретест подтверждён", "роль изменена") and (still_up or still_dn):
        if c1.dt not in (zone.break_dt, zone.hold_dt):
            note_beyond(zone, c1.dt)

    # отмена после нескольких разных закрытий за границей
    if zone.closes_beyond >= INVALID_BEYOND and zone.state in ("удержание подтверждено", "пробита", "ослаблена"):
        if (zone.broken_side == "down" or zone.kind == "support") and still_dn:
            zone.state = "недействительна"
            events.append(("invalid", "Несколько закрытых свечей закрепились ниже зоны. Уровень больше не удерживает цену.", "SHORT"))
        elif (zone.broken_side == "up" or zone.kind == "resistance") and still_up:
            zone.state = "недействительна"
            events.append(("invalid", "Несколько закрытых свечей закрепились выше зоны. Уровень больше не удерживает цену.", "LONG"))

    # новый сильный уровень только для зоны, родившейся после загрузки
    if (
        zone.strength >= MIN_NOTIFY_STRENGTH
        and "new_level" not in zone.sent_events
        and getattr(cfg, "LEVEL_NOTIFY_NEW", False)
        and getattr(zone, "post_bootstrap", False)
        and any(t in SENIOR or t in WORKING for t in zone.tfs)
    ):
        events.insert(
            0,
            (
                "new_level",
                f"Уровни {format_tfs(zone.tfs)} образовали единую сильную зону {kind_ru(zone.kind).lower()}.",
                "",
            ),
        )

    zone.strength = max(0, min(100, zone.strength - absorb_penalty(zone) * 0.15))
    return events


def build_pair_zones(symbol: str, by_tf: dict[str, list[Candle]]) -> tuple[list[Zone], dict[str, list[Candle]], dict[str, float], Optional[float], dict[str, str]]:
    closed_map: dict[str, list[Candle]] = {}
    atr_map: dict[str, float] = {}
    last_map: dict[str, str] = {}
    raw_all = []
    live = None
    for tf in TF_ORDER:
        raw = by_tf.get(tf) or []
        closed = tf_closed(symbol, tf, raw)
        closed_map[tf] = closed
        if raw:
            live = raw[-1].close
        elif closed:
            live = closed[-1].close
        if len(closed) >= 16:
            atr_map[tf] = atr(closed, 14)
        if closed:
            last_map[tf] = closed[-1].dt
        raw_all.extend(cluster_tf(symbol, tf, closed, collect_raw_levels(symbol, tf, closed)))
    zones = merge_cluster(symbol, raw_all, atr_map)
    return zones, closed_map, atr_map, live, last_map


def candle_changed(store: dict, symbol: str, last_map: dict[str, str]) -> bool:
    changed = False
    lc = store.setdefault("last_closed", {})
    for tf, dt in last_map.items():
        key = f"{symbol}:{tf}"
        if lc.get(key) != dt:
            changed = True
            lc[key] = dt
    return changed


def process_market(market: dict) -> list[str]:
    """Считает уровни по уже полученному рынку. Возвращает тексты новых фактов."""
    store = load_store()
    if not acquire(store):
        return []
    messages: list[str] = []
    try:
        # Older broken builds could mark bootstrap complete while saving no
        # zones at all. Rebuild that empty baseline silently after deployment
        # so the repair does not flood Telegram with seven "new level" cards.
        bootstrap = not store.get("bootstrapped") or not store.get("zones")
        known = set(store.get("known_ids") or [])
        old_zones = {k: zone_from_dict(v) for k, v in store.get("zones", {}).items()}
        new_map: dict[str, Zone] = {}
        for symbol in cfg.PAIRS:
            try:
                fresh, closed_map, atr_map, live, last_map = build_pair_zones(symbol, market.get(symbol) or {})
                if live is None:
                    continue
                pair_old = {k: z for k, z in old_zones.items() if z.symbol == symbol}
                zones = attach_existing(pair_old, fresh)
                # Если одна свеча пересекла несколько близких зон, первой
                # рассматривается самая сильная; остальные отсечёт точный ключ.
                zones.sort(key=lambda item: item.strength, reverse=True)
                changed = candle_changed(store, symbol, last_map)
                if not changed and store.get("bootstrapped"):
                    for z in zones:
                        new_map[z.zone_id] = z
                    continue
                pair_cards = []
                for z in zones:
                    if bootstrap or z.zone_id in known:
                        z.post_bootstrap = False
                        if "new_level" not in z.sent_events:
                            z.sent_events.append("new_level")
                    else:
                        z.post_bootstrap = True
                    evs = detect_events(z, closed_map, live, atr_map, last_map, bootstrap)
                    evs = [e for e in evs if e[0] != "approach"]
                    evs.sort(key=lambda x: EVENT_PRIORITY.get(x[0], 50))
                    chosen = None
                    extras = []
                    work_bars = closed_map.get(pick_work_tf(z)) or []
                    cdt = work_bars[-1].dt if work_bars else ""
                    for ev, fact, side in evs:
                        if already_sent(store, z, ev, cdt):
                            continue
                        if semantic_recent(store, z, ev, side, cdt):
                            continue
                        if ev == "new_level" and z.strength < MIN_NOTIFY_STRENGTH:
                            continue
                        if chosen is None:
                            chosen = (ev, fact, side)
                            mark_sent(store, z, ev, cdt)
                            mark_semantic(store, z, ev, side, cdt)
                        else:
                            extras.append(fact)
                    if chosen and not bootstrap:
                        fact = chosen[1] if not extras else chosen[1] + " " + extras[0]
                        pair_cards.append((EVENT_PRIORITY.get(chosen[0], 50), z, chosen[0], fact, chosen[2]))
                    new_map[z.zone_id] = z
                    known.add(z.zone_id)
                if pair_cards and not bootstrap:
                    pair_cards.sort(key=lambda x: x[0])
                    _pr, z, ev, fact, side = pair_cards[0]
                    work = pick_work_tf(z)
                    work_bars = closed_map.get(work) or []
                    candle = work_bars[-1] if work_bars else None
                    av = atr_map.get(work, z.width)
                    quality, confidence = reaction_metrics(z, candle, av, ev)
                    messages.append(build_message(
                        z, ev, fact, side,
                        confirmation_tf=work if ev != "new_level" else "",
                        close_price=candle.close if candle and ev != "new_level" else None,
                        quality=quality if ev != "new_level" else None,
                        confidence=confidence if ev != "new_level" else None,
                    ))
            except Exception:
                log.exception("Уровни %s", symbol)
        store["zones"] = {k: {kk: vv for kk, vv in asdict(z).items() if kk != "post_bootstrap"} for k, z in new_map.items()}
        store["known_ids"] = sorted(known)
        history_limit = int(getattr(cfg, "LEVEL_EXACT_EVENT_HISTORY", 1500))
        exact_items = sorted(
            (store.get("semantic_candles") or {}).items(),
            key=lambda item: float(item[1] or 0),
        )[-history_limit:]
        store["semantic_candles"] = dict(exact_items)
        if bootstrap:
            store["bootstrapped"] = True
            messages = []
        save_store(store)
    except Exception:
        log.exception("Сбой модуля уровней")
        messages = []
    finally:
        try:
            release(load_store() if False else store)
        except Exception:
            pass
    return messages


def process_and_collect(market: dict) -> list[str]:
    return process_market(market)
