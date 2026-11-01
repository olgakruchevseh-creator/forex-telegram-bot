"""H1-only isolated Livermore observer for Forex; it never changes signals."""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from livermore_market_key import evaluate, compact_ru

LOGGER = logging.getLogger("fxbot.livermore")


def snapshot(symbol: str, by_tf: Mapping) -> dict:
    from analysis import closed_candles
    source = list((by_tf or {}).get("H1") or [])
    closed = closed_candles(source, timeframe_minutes=60)
    # Never accept open/future H1 from any caller or delayed provider snapshot.
    rows = []
    now = datetime.now(timezone.utc)
    for b in closed:
        try:
            opened = datetime.fromisoformat(str(b.dt).replace("Z", "+00:00"))
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            end = opened.astimezone(timezone.utc) + timedelta(hours=1)
        except (ValueError, TypeError, OverflowError):
            return {"available": False, "reason": "ОШИБКА_ЧАСОВ_H1"}
        if end > now:
            continue
        rows.append({"datetime": end.isoformat(), "open": b.open,
                     "high": b.high, "low": b.low, "close": b.close})
    result = evaluate(rows[-250:])
    result["symbol"] = symbol
    result["timeframe"] = "H1_CLOSED"
    return result


def _location() -> Path:
    env = os.getenv("LIVERMORE_JOURNAL_PATH")
    if env:
        return Path(env)
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "livermore_observe_forex.jsonl"


def journal(record: dict) -> bool:
    """Append one distinct H1 per symbol; advisory history only."""
    if not record.get("available") or not record.get("h1"):
        return False
    path = _location()
    path.parent.mkdir(parents=True, exist_ok=True)
    # fcntl locks cover concurrent scan/briefing jobs on Linux Railway.
    import fcntl
    import os as _os
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        last = None
        for line in handle:
            try:
                old = json.loads(line)
                if old.get("symbol") == record["symbol"]:
                    last = old.get("h1")
            except json.JSONDecodeError:
                continue
        if last is not None and str(record["h1"]) <= str(last):
            return False
        compact = {k: v for k, v in record.items() if k != "journal"}
        handle.seek(0, _os.SEEK_END)
        handle.write(json.dumps(compact, ensure_ascii=False, allow_nan=False) + "\n")
        handle.flush()
        _os.fsync(handle.fileno())
        return True


def observe_market(market: Mapping) -> None:
    for symbol, by_tf in (market or {}).items():
        try:
            result = snapshot(symbol, by_tf)
            if journal(result):
                LOGGER.info("LIVERMORE_OBSERVE symbol=%s h1=%s state=%s event=%s mode=%s",
                            symbol, result["h1"], result.get("state"), result.get("event"), result.get("mode"))
        except Exception:
            LOGGER.exception("LIVERMORE_OBSERVE_FAILED symbol=%s", symbol)
