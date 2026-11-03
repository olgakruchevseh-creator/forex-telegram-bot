"""KILLER's separate, passive, reproducible rejected-candidate journal.

This module cannot publish or approve an order. Replays use FROZEN CLOSED bars
and the ORIGINAL calendar/time; forward outcome inspection is kept distinct.
"""
from __future__ import annotations
import hashlib
import json
import logging
import math
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import config as cfg
from analysis import Candle, atr, closed_candles

log = logging.getLogger("fxbot.killer.audit")
TF_MIN = {"D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}
MAX_BARS = {"D1": 100, "H4": 120, "H1": 120, "M15": 90, "M5": 90}
_CACHE: dict[str, set] = {}


def _path(name):
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / name


def _seen(path):
    key = str(path)
    if key not in _CACHE:
        found = set()
        if path.is_file():
            try:
                with path.open(encoding="utf-8", errors="replace") as fh:
                    for line in deque(fh, maxlen=50000):
                        try: found.add(json.loads(line).get("id"))
                        except (ValueError, TypeError): pass
            except OSError: log.exception("KILLER_AUDIT_DEDUP_LOAD_FAILED")
        _CACHE[key] = found
    return _CACHE[key]


def _write(name, record):
    try:
        p = _path(name)
        seen = _seen(p)
        if record["id"] in seen: return False
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
        seen.add(record["id"])
        return True
    except (OSError, TypeError, ValueError):
        log.exception("KILLER_AUDIT_WRITE_FAILED file=%s", name)
        return False


def _finite(number):
    try:
        v = float(number)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError, OverflowError): return None


def _closed_snapshot(by_tf):
    out = {}
    for tf, minutes in TF_MIN.items():
        bars = closed_candles((by_tf or {}).get(tf) or [], minutes)[-MAX_BARS[tf]:]
        out[tf] = [{"dt": str(b.dt), "open": _finite(b.open), "high": _finite(b.high),
                    "low": _finite(b.low), "close": _finite(b.close), "volume": _finite(getattr(b, "volume", None))}
                   for b in bars if all(_finite(getattr(b, x, None)) is not None for x in ("open", "high", "low", "close"))]
    return out


def restore(snapshot):
    out = {}
    for tf in TF_MIN:
        out[tf] = [Candle(**item) for item in (snapshot or {}).get(tf, []) if isinstance(item, dict)]
    return out


def _event_snapshot(events):
    return [{"currency": str(getattr(e, "currency", "")), "impact": str(getattr(e, "impact", "")),
             "title": str(getattr(e, "title", "")), "event_id": str(getattr(e, "event_id", "")),
             "dt_utc": getattr(getattr(e, "dt_utc", None), "isoformat", lambda: "")()}
            for e in (events or [])][:100]


def record_attempt(pair, side, meta, by_tf, strength, texts, events, now_utc):
    """One record per *distinct verdict* per closed H1; never one per poll/tick."""
    if not getattr(cfg, "KILLER_AUDIT_ENABLED", True): return
    snap = _closed_snapshot(by_tf)
    anchor = (snap["H1"][-1]["dt"] if snap["H1"] else "")
    if not anchor: return
    family_names = sorted(meta.get("families") or [])
    reason = str(meta.get("reason") or "eligible")
    signature = "|".join([pair, side, anchor, reason, str(meta.get("score")), ",".join(family_names)])
    key = hashlib.sha256(signature.encode()).hexdigest()[:24]
    h1 = restore({"H1": snap["H1"]})["H1"]
    av = _finite(atr(h1, 14)) if len(h1) >= 25 else None
    px = _finite(h1[-1].close) if h1 else None
    direction = 1 if side == "LONG" else -1
    risk = (av*1.2 if av and av>0 else None)
    # Hypothetical projection only. Rejected trades have no actionable price.
    projection = ({"entry": px, "invalidation": px-direction*risk,
                   "tr1": px+direction*av, "tr2": px+direction*av*1.75,
                   "tr3": px+direction*av*2.5} if px is not None and risk else {})
    payload = {"id": key, "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "pair": pair, "side": side, "h1_dt": anchor,
               "status": "CANDIDATE" if meta.get("eligible") else "REJECTED",
               "reason": reason, "score": _finite(meta.get("score")), "min_score": int(getattr(cfg, "KILLER_SCORE_THRESHOLD", 84)),
               "family_count": len(family_names), "min_families": int(getattr(cfg, "KILLER_MIN_FAMILIES", 5)),
               "families": family_names, "trap_review": meta.get("trap_review") or {},
               "module_trace": meta.get("module_trace") or {},
               "projection_observe_only": projection, "snapshot": snap,
               "strength": {str(k): _finite(v) for k,v in (strength or {}).items()},
               "source_fact_hashes": [hashlib.sha256(t.encode()).hexdigest()[:18] for t in texts],
               "source_facts": list(texts)[:35],  # reproducibility: no private tokens/headers
               "events": _event_snapshot(events), "decision_now_utc": now_utc.isoformat()}
    _write("killer_decisions.jsonl", payload)


def _tail(path, limit=180):
    if not path.exists(): return []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            return [json.loads(s) for s in deque(fh, maxlen=limit) if s.strip()]
    except (OSError, ValueError):
        log.exception("KILLER_AUDIT_READ_FAILED file=%s", path)
        return []


def review_rejections(market, evaluator, *, limit=6):
    """Bounded replay of persisted rejects. Results never enter candidate stream.

    A later good outcome is NOT evidence that the original block was wrong.
    """
    if not getattr(cfg, "KILLER_AUDIT_ENABLED", True): return 0
    decisions = _tail(_path("killer_decisions.jsonl"), 3000)
    budget = max(0, min(20, int(limit)))
    completed = 0
    reviewed = _seen(_path("killer_reviews.jsonl"))
    for row in reversed(decisions):
        if completed >= budget: break
        if row.get("status") != "REJECTED": continue
        row_id = row.get("id")
        if not row_id or f"{row_id}:replay" in reviewed: continue
        pair, side = row.get("pair"), row.get("side")
        current = closed_candles(((market or {}).get(pair) or {}).get("H1") or [], 60)
        current_dts = [str(x.dt) for x in current]
        if row.get("h1_dt") not in current_dts: continue
        anchor_idx = current_dts.index(row["h1_dt"])
        if anchor_idx == len(current_dts)-1: continue  # at least one NEW closed H1
        try:
            frozen = restore(row["snapshot"])
            now = datetime.fromisoformat(row["decision_now_utc"])
            from types import SimpleNamespace
            events = []
            for data in row.get("events", []):
                try: events.append(SimpleNamespace(**{**data,"dt_utc":datetime.fromisoformat(data["dt_utc"])}))
                except (KeyError, TypeError, ValueError): pass
            checked = evaluator(pair, side, row.get("source_facts") or [], {pair: frozen},
                                row.get("strength") or {}, events=events, now_utc=now)
            old, new = row.get("reason"), checked.get("reason") or "eligible"
            verdict = "REPRODUCED" if old == new and bool(checked.get("eligible")) is False else "REPLAY_DIFFERENCE"
            # Distinguish a reproducibility mismatch from quality of a trade.
            detail = {"id": f"{row_id}:replay", "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "candidate_id": row_id, "pair": pair, "side": side, "h1_dt": row.get("h1_dt"),
                      "type": "FROZEN_REPLAY", "verdict": verdict, "original_reason": old,
                      "replayed_reason": new, "replayed_score": _finite(checked.get("score")),
                      "note": "Никакого возобновления старого входа; нужен новый H1 и все защитные фильтры"}
            if _write("killer_reviews.jsonl", detail): completed += 1
        except Exception:
            log.exception("KILLER_REPLAY_FAILED pair=%s side=%s id=%s", pair,side,row_id)
    return completed


def review_outcomes(market, *, limit=12):
    """Forward-only measurement AFTER 6 H1, separate from frozen original review."""
    if not getattr(cfg, "KILLER_AUDIT_ENABLED", True): return 0
    reviewed = _seen(_path("killer_reviews.jsonl"))
    completed = 0
    for row in reversed(_tail(_path("killer_decisions.jsonl"), 3000)):
        if completed >= limit: break
        row_id = row.get("id")
        if not row_id or f"{row_id}:outcome" in reviewed or row.get("status") != "REJECTED": continue
        levels = row.get("projection_observe_only") or {}
        if not all(_finite(levels.get(x)) is not None for x in ("entry","invalidation","tr1","tr2","tr3")): continue
        pair, side = row.get("pair"), row.get("side")
        current = closed_candles(((market or {}).get(pair) or {}).get("H1") or [], 60)
        dates = [str(x.dt) for x in current]
        if row.get("h1_dt") not in dates: continue
        idx = dates.index(row["h1_dt"])
        future = current[idx+1:idx+7]
        if len(future) < 6: continue
        direction = 1 if side == "LONG" else -1
        def touched(bar, price, is_target):
            return (bar.high >= price if direction>0 else bar.low<=price) if is_target else (bar.low<=price if direction>0 else bar.high>=price)
        hit = {}; stop_first = False; ambiguous = False
        for n, bar in enumerate(future, 1):
            stop = touched(bar, levels["invalidation"], False)
            targets = [name for name, horizon in (("tr1",2),("tr2",4),("tr3",6)) if n<=horizon and touched(bar,levels[name],True)]
            if stop and targets: ambiguous=True
            if stop: stop_first=True
            for name in targets: hit.setdefault(name, n)
            if stop: break
        verdict = "AMBIGUOUS_H1" if ambiguous else ("STOP_OR_INVALIDATION" if stop_first else "NO_STOP_IN_WINDOW")
        detail = {"id":f"{row_id}:outcome", "utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "candidate_id":row_id,"pair":pair,"side":side,"type":"FORWARD_OUTCOME_6H1",
                  "verdict":verdict,"tr_hit_at_h1":hit,"stop_touched":stop_first,
                  "hypothetical_only":True,"note":"Гипотетическая траектория; не доказывает, что отклонение было ошибкой"}
        if _write("killer_reviews.jsonl",detail): completed+=1
    return completed


def record_delivery_gate(text, reason, market):
    """Final Master/significance/ZigZag/cooldown result, never changes delivery."""
    if "🏹🎯 KILLER" not in (text or ""): return
    import re
    pm = re.search(r"Пара:\s*([A-Z]{3}/[A-Z]{3})",text)
    sm = re.search(r"Направление:\s*(LONG|SHORT)",text)
    if not pm or not sm:return
    pair, side = pm.group(1), sm.group(1)
    h1 = closed_candles(((market or {}).get(pair) or {}).get("H1") or [],60)
    anchor = str(h1[-1].dt) if h1 else ""
    key = hashlib.sha256((pair+side+anchor+str(reason)).encode()).hexdigest()[:24]
    _write("killer_final_gates.jsonl", {"id":key,"utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                      "pair":pair,"side":side,"h1_dt":anchor,"reason":str(reason)})


def daily_digest(day):
    """Compact human-readable section for the existing daily Telegram journal."""
    from collections import Counter
    from zoneinfo import ZoneInfo
    zone = ZoneInfo(getattr(cfg,"LOCAL_TZ_NAME","Europe/Amsterdam"))
    def local_day(row):
        try:return datetime.fromisoformat(row.get("recorded_utc") or row.get("utc")).astimezone(zone).date().isoformat()
        except (ValueError,TypeError):return ""
    rows = [r for r in _tail(_path("killer_decisions.jsonl"),5000) if local_day(r)==day]
    if not rows: return ""
    status = Counter(r.get("status") or "?" for r in rows)
    reasons = Counter(r.get("reason") or "?" for r in rows if r.get("status")=="REJECTED")
    names={"not_enough_independent_families":"мало независимых семейств",
           "score_below_threshold":"недостаточный балл",
           "high_impact_news_veto":"важные новости",
           "critical_tf_contradiction":"конфликт таймфреймов",
           "ohlc_contradiction":"противоречие свечей",
           "erl_residual_exhausted":"исчерпан маршрут",
           "invalid_price_or_atr":"ошибка цены/ATR"}
    top = ", ".join(f"{names.get(name,name)} {n}" for name,n in reasons.most_common(3)) or "—"
    ids={r.get("id") for r in rows}
    reviews=[r for r in _tail(_path("killer_reviews.jsonl"),5000) if r.get("candidate_id") in ids]
    replays=Counter(r.get("verdict") for r in reviews if r.get("type")=="FROZEN_REPLAY")
    outcomes=sum(r.get("type")=="FORWARD_OUTCOME_6H1" for r in reviews)
    gates=[r for r in _tail(_path("killer_final_gates.jsonl"),5000) if local_day(r)==day]
    final_blocks=Counter(r.get("reason") for r in gates if "BLOCKED" in r.get("reason","") or "final_" in r.get("reason",""))
    return ("🏹 KILLER · ОТДЕЛЬНЫЙ ЖУРНАЛ (OBSERVE_ONLY)\n"
            f"Кандидаты: {len(rows)} · отклонено {status['REJECTED']} · прошли Killer {status['CANDIDATE']}\n"
            f"Основные причины: {top}\n"
            f"Повторный расчёт: совпал {replays['REPRODUCED']} · расхождение {replays['REPLAY_DIFFERENCE']} · итог 6H1 {outcomes}\n"
            f"Финальные отказы после Killer: {sum(final_blocks.values())} · отдельных записей {len(gates)}\n"
            "Исторически удачная отклонённая сделка НЕ возобновляется автоматически.")
