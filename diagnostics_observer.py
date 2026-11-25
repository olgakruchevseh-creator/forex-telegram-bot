"""Stage 24: hourly OBSERVE_ONLY diagnostics that bring four dormant layers
back into the live runtime.

Once per NEW closed H1 (per process, deduplicated by the basket's newest H1)
this observer runs, for each of the seven pairs:

- data_quality_guard   — gaps inside trading hours, frozen/flat feed tail;
- causality_guard      — live ZigZag repaint audit on the real H1 series;
- market_adaptation_context — volatility-regime change detectors on
  log(TR / median TR) of closed H1;
- pair_character       — second, independent estimator of the pair's
  character, cross-checked against pair_character_matrix (efficiency).

It never produces LONG/SHORT, never vetoes, never changes thresholds, routing
or Telegram delivery. Output: one compact log line and one JSONL record per
closed H1 in STATE_DIR/diagnostics_observe_forex.jsonl (size-bounded). The
latest result is kept in memory for the /diagnostics command.
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

import config as cfg
from analysis import closed_candles, zigzag, _true_range

log = logging.getLogger("fxbot.diagnostics")

TF_SECONDS = {"M5": 300, "M15": 900, "H1": 3600, "H4": 14400}
_LAST_H1: str | None = None
_LAST_RESULT: dict | None = None


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "diagnostics_observe_forex.jsonl"


def _opened(dt: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(dt).strip().replace("Z", "+00:00").replace(" ", "T"))
    except (TypeError, ValueError):
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def _is_trading(slot: datetime) -> bool:
    from patterns_history_integrity import definitely_trading_fx
    return definitely_trading_fx(slot)


def data_quality(by_tf: dict) -> dict:
    import data_quality_guard
    out = {}
    for tf, seconds in TF_SECONDS.items():
        bars = closed_candles(by_tf.get(tf) or [], seconds // 60)
        if len(bars) < 2:
            continue
        rep = data_quality_guard.audit_bars(
            bars, expected_seconds=seconds, max_gap_bars=3,
            # M5 can legitimately print several identical quotes in the
            # rollover minute; a frozen feed shows a long identical tail.
            max_stale_tail=6 if tf == "M5" else 2, is_trading=_is_trading)
        out[tf] = {"ok": rep.ok, "reasons": list(rep.reasons), "large_gaps": rep.large_gaps,
                   "stale_tail_bars": rep.stale_tail_bars, "flat_bars": rep.flat_bars}
    return out


def zigzag_repaint(h1: list) -> dict:
    import causality_guard
    pct = float((cfg.ZIGZAG_PCT or {}).get("H1", 0.18))
    bars = int(getattr(cfg, "ZIGZAG_MIN_BARS", 3))
    return causality_guard.swing_repaint_check(h1[-250:], lambda b: zigzag(b, pct, bars), checkpoints=3)


def volatility_regime(h1: list) -> dict:
    """Regime detectors on x_t = log(TR_t / median TR) — unitless, near-normal."""
    import market_adaptation_context as mac
    window = h1[-160:]
    if len(window) < 60:
        return {"status": "INSUFFICIENT_DATA", "n": len(window)}
    tr = _true_range(window)[1:]
    med = median([v for v in tr if v > 0] or [0.0])
    if med <= 0:
        return {"status": "NO_VARIANCE", "n": len(tr)}
    x = [math.log(max(v, med * 1e-3) / med) for v in tr]
    ctx = mac.build_context(x)
    aw = mac.adaptive_window_shift(x)
    ph = mac.page_hinkley(x)
    return {"status": "OK", "state": ctx.state, "votes": ctx.consensus, "facts": list(ctx.facts),
            "cusum": aw.get("statistic"), "change_bars_ago": (aw["n"] - aw["cut"]) if aw.get("alarm") and aw.get("cut") else None,
            "page_hinkley_active": bool(ph.get("active")), "direction": ph.get("direction")}


def character_crosscheck(symbol: str, by_tf: dict) -> dict:
    import pair_character
    import pair_character_matrix
    pc = pair_character.analyze_symbol(symbol, by_tf)
    if not pc:
        return {"status": "INSUFFICIENT_DATA"}
    try:
        pm = pair_character_matrix.analyze(symbol, by_tf)
    except Exception:
        pm = {}
    out = {"status": "OK", "movement": pc["movement_character"],
           "efficiency_pc": pc["trend_efficiency"], "volatility_ratio": pc["volatility_ratio_fast_slow"]}
    if pm.get("ready") and pm.get("efficiency") is not None:
        diff = abs(float(pc["trend_efficiency"]) - float(pm["efficiency"]))
        out.update(efficiency_matrix=pm["efficiency"], efficiency_diff=round(diff, 3),
                   # Both are |ΔP|/Σ|Δp| over ~24 H1; a large gap means the two
                   # estimators see different data or one formula drifted.
                   estimators_agree=diff <= 0.15)
    return out


def _append(record: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    limit = int(getattr(cfg, "DIAGNOSTICS_JOURNAL_MAX_BYTES", 5_000_000))
    try:
        if path.is_file() and path.stat().st_size > limit:
            lines = path.read_text(encoding="utf-8").splitlines()
            path.write_text("\n".join(lines[len(lines) // 2:]) + "\n", encoding="utf-8")
    except OSError:
        log.exception("DIAGNOSTICS_JOURNAL_TRIM_FAILED")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False, default=str) + "\n")


def observe(market: dict, *, force: bool = False) -> dict | None:
    """Run once per new closed H1 of the basket. Returns the record or None."""
    global _LAST_H1, _LAST_RESULT
    if not getattr(cfg, "DIAGNOSTICS_OBSERVER_ENABLED", True):
        return None
    newest = ""
    h1_by_pair = {}
    for pair in cfg.PAIRS:
        h1 = closed_candles(((market or {}).get(pair) or {}).get("H1") or [], 60)
        h1_by_pair[pair] = h1
        if h1:
            newest = max(newest, str(h1[-1].dt))
    if not newest or (newest == _LAST_H1 and not force):
        return None
    pairs = {}
    for pair in cfg.PAIRS:
        by_tf = (market or {}).get(pair) or {}
        h1 = h1_by_pair[pair]
        row = {}
        for name, fn in (("data_quality", lambda: data_quality(by_tf)),
                         ("zigzag_repaint", lambda: zigzag_repaint(h1)),
                         ("volatility_regime", lambda: volatility_regime(h1)),
                         ("character", lambda: character_crosscheck(pair, by_tf))):
            try:
                row[name] = fn()
            except Exception as exc:
                row[name] = {"status": "ERROR", "error": type(exc).__name__}
                log.exception("DIAGNOSTICS_%s_FAILED pair=%s", name.upper(), pair)
        pairs[pair] = row
    record = {"schema": 1, "mode": "OBSERVE_ONLY", "live_effect": "NONE",
              "h1": newest, "observed_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "pairs": pairs, "summary": summarize(pairs)}
    _LAST_H1, _LAST_RESULT = newest, record
    try:
        _append(record)
    except OSError:
        log.exception("DIAGNOSTICS_JOURNAL_WRITE_FAILED")
    log.info("DIAGNOSTICS_OBSERVE h1=%s %s", newest, json.dumps(record["summary"], ensure_ascii=False))
    return record


def summarize(pairs: dict) -> dict:
    s = {"data_issues": [], "repaint": [], "regime_change": [], "estimator_disagree": []}
    for pair, row in pairs.items():
        for tf, q in (row.get("data_quality") or {}).items():
            if isinstance(q, dict) and not q.get("ok"):
                s["data_issues"].append(f"{pair}:{tf}:{','.join(q.get('reasons') or [])}")
        if (row.get("zigzag_repaint") or {}).get("violations"):
            s["repaint"].append(pair)
        if (row.get("volatility_regime") or {}).get("votes", 0) >= 2:
            s["regime_change"].append(pair)
        if (row.get("character") or {}).get("estimators_agree") is False:
            s["estimator_disagree"].append(pair)
    return s


def last_result() -> dict | None:
    return _LAST_RESULT


def format_ru(record: dict | None) -> str:
    if not record:
        return ("🩺 ДИАГНОСТИКА · OBSERVE_ONLY\nЕщё нет ни одного прогона: он выполняется "
                "один раз после закрытия каждой H1.")
    s = record["summary"]
    lines = [f"🩺 ДИАГНОСТИКА · H1 {record['h1']} UTC · OBSERVE_ONLY",
             "Сигналы, пороги и доставка не затрагиваются.", ""]
    lines.append("Данные: " + ("без замечаний" if not s["data_issues"] else "; ".join(s["data_issues"][:6])))
    lines.append("Перерисовка ZigZag H1: " + ("не обнаружена" if not s["repaint"] else ", ".join(s["repaint"])))
    lines.append("")
    for pair, row in record["pairs"].items():
        v = row.get("volatility_regime") or {}
        c = row.get("character") or {}
        part = [pair]
        if v.get("status") == "OK":
            part.append(f"волатильность: {v['state'].lower()} (CUSUM {v.get('cusum')})")
        if c.get("status") == "OK":
            agree = c.get("estimators_agree")
            part.append(f"характер {c['movement']}" + ("" if agree is None else
                        (" · оценщики согласны" if agree else f" · РАСХОЖДЕНИЕ эффективности {c.get('efficiency_diff')}")))
        lines.append(" · ".join(part))
    return "\n".join(lines)
