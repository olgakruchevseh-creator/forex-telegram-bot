"""Наблюдение за замороженными зонами Levels, опубликованными в брифинге.

Информационный канал. Не входит в очередь сигналов, Master/Killer/Nav и не
меняет историю событий Levels. Только полностью закрытые ПОСЛЕ публикации H1.
SQLite на постоянном STATE_DIR сохраняет исходные зоны и защищает от повторов.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import config as cfg
import levels
from analysis import atr, closed_candles

log = logging.getLogger("fxbot.briefing_level_watch")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except (ValueError, TypeError, OverflowError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def db_path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "briefing_level_watch.sqlite3"


def _open(path: Path | None = None) -> sqlite3.Connection:
    dest = Path(path) if path is not None else db_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(dest), timeout=8)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=8000")
    conn.execute("""CREATE TABLE IF NOT EXISTS watched (
        id TEXT PRIMARY KEY, session TEXT NOT NULL, symbol TEXT NOT NULL,
        label TEXT NOT NULL, kind TEXT NOT NULL, low REAL NOT NULL, high REAL NOT NULL,
        as_of_h1 TEXT NOT NULL, published_at TEXT NOT NULL, expires_at TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1, last_h1 TEXT NOT NULL DEFAULT '',
        contacts INTEGER NOT NULL DEFAULT 0, in_contact INTEGER NOT NULL DEFAULT 0,
        last_contact_h1 TEXT NOT NULL DEFAULT '', pending_wick TEXT NOT NULL DEFAULT '',
        candidate_h1 TEXT NOT NULL DEFAULT '', break_h1 TEXT NOT NULL DEFAULT '',
        break_confirmed INTEGER NOT NULL DEFAULT 0, role_confirmed INTEGER NOT NULL DEFAULT 0,
        lifecycle TEXT NOT NULL DEFAULT '', tfs TEXT NOT NULL DEFAULT ''
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS watch_events (
        id TEXT PRIMARY KEY, watch_id TEXT NOT NULL, symbol TEXT NOT NULL,
        h1 TEXT NOT NULL, event TEXT NOT NULL, report TEXT NOT NULL,
        created_at TEXT NOT NULL, sent_at TEXT NOT NULL DEFAULT '',
        claimed_until TEXT NOT NULL DEFAULT ''
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS watch_events_pending ON watch_events(sent_at, claimed_until, h1)")
    conn.execute("CREATE INDEX IF NOT EXISTS watched_pair ON watched(symbol, active)")
    return conn


def register_briefing(session: str, symbol: str, snapshot: dict,
                      *, published_at: datetime | None = None,
                      path: Path | None = None) -> int:
    """Freeze a pair's S1/S2/R1/R2 once per session AFTER successful delivery.

    Later revisions do not silently replace the actual zones already published.
    The first successfully delivered briefing for that session is canonical.
    """
    info = snapshot or {}
    opened = _parse_dt(info.get("as_of_h1") or "")
    now = published_at or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    if not opened or opened + timedelta(hours=1) > now:
        return 0
    records = []
    for kind, key in (("support", "supports"), ("resistance", "resistances"), ("current", "current_zones")):
        for row in list(info.get(key) or [])[:2 if kind != "current" else 1]:
            try:
                low, high = float(row["low"]), float(row["high"])
                if not (math.isfinite(low) and math.isfinite(high) and 0 < low <= high):
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            label = str(row.get("label") or "")
            if not label or (kind == "support" and not label.startswith("S")) or (kind == "resistance" and not label.startswith("R")):
                continue
            if kind == "current" and not label.startswith("Z"):
                continue
            if row.get("state") in ("пробита", "недействительна"):
                continue
            ident = hashlib.sha256(f"{session}|{symbol}|{kind}|{low:.7f}|{high:.7f}".encode()).hexdigest()[:24]
            records.append((ident, label, kind, low, high, str(row.get("status") or ""), str(row.get("tfs_text") or "")))
    if not records or not session or not symbol:
        return 0
    expiry = now + timedelta(hours=max(3, min(48, int(getattr(cfg, "BRIEF_LEVEL_WATCH_HOURS", 18)))))
    inserted = 0
    with _open(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        # Existing session is immutable; keep the original published geometry.
        exists = conn.execute("SELECT 1 FROM watched WHERE session=? AND symbol=? LIMIT 1", (session, symbol)).fetchone()
        if exists:
            return 0
        # Do not send a second stream for stale zones from an earlier briefing.
        conn.execute("UPDATE watched SET active=0 WHERE symbol=? AND active=1", (symbol,))
        for ident, label, kind, low, high, lifecycle, tfs in records:
            cur = conn.execute("""INSERT OR IGNORE INTO watched
                (id, session, symbol, label, kind, low, high, as_of_h1, published_at, expires_at, lifecycle, tfs)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ident, session, symbol, label, kind, low, high, _stamp(opened), _stamp(now), _stamp(expiry), lifecycle, tfs))
            inserted += max(0, cur.rowcount)
        # Keep finite history, never delete not-yet-delivered reports.
        cutoff = _stamp(now - timedelta(days=30))
        conn.execute("DELETE FROM watch_events WHERE sent_at!='' AND created_at<?", (cutoff,))
        conn.execute("DELETE FROM watched WHERE active=0 AND published_at<?", (cutoff,))
    return inserted


def _valid_bars(by_tf: dict, now: datetime):
    # Use the same raw H1 values as the signal engine, not a substitute M15 series.
    bars = closed_candles((by_tf or {}).get("H1") or [], 60)
    seen = set()
    out = []
    for candle in bars:
        opened = _parse_dt(getattr(candle, "dt", ""))
        if opened is None or opened in seen or opened + timedelta(hours=1) > now:
            continue
        try:
            values = [float(getattr(candle, k)) for k in ("open", "high", "low", "close")]
        except (ValueError, TypeError, AttributeError):
            continue
        if not all(math.isfinite(v) and v > 0 for v in values):
            continue
        if values[1] < max(values[0], values[3]) or values[2] > min(values[0], values[3]):
            continue
        seen.add(opened)
        out.append((opened, candle))
    return sorted(out, key=lambda x: x[0])


def _watch_event(conn, state, opened: datetime, key: str, title: str, comment: str,
                 candle, published: str) -> None:
    pair = state["symbol"]
    label = state["label"]
    kind = "поддержка" if state["kind"] == "support" else ("сопротивление" if state["kind"] == "resistance" else "текущая зона")
    price = levels.fmt_price
    event_id = hashlib.sha256(f"{state['id']}|{_stamp(opened)}|{key}".encode()).hexdigest()[:30]
    header = f"📍 LEVELS · СОПРОВОЖДЕНИЕ БРИФИНГА · {pair}"
    report = "\n".join((header, f"{label} · {kind} · {price(pair, state['low'])}–{price(pair, state['high'])}",
                       f"H1 закрыта: {levels.candle_close_time(getattr(candle, 'dt', ''), 'H1')}",
                       f"Событие: {title}", comment,
                       f"Уровень зафиксирован при брифинге: {published} (UTC)",
                       "OBSERVE_ONLY · не новый торговый сигнал · порядок касаний внутри H1 неизвестен"))
    conn.execute("""INSERT OR IGNORE INTO watch_events
        (id, watch_id, symbol, h1, event, report, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (event_id, state["id"], pair, _stamp(opened), key, report, _stamp(_now())))


def _update_one(conn, state, bars, *, now: datetime) -> int:
    origin = _parse_dt(state["published_at"])
    anchor = _parse_dt(state["as_of_h1"])
    last = _parse_dt(state["last_h1"]) if state["last_h1"] else None
    if origin is None or anchor is None:
        return 0
    zone = SimpleNamespace(symbol=state["symbol"], low=state["low"], high=state["high"],
                           width=max(state["high"] - state["low"], 1e-8))
    contact_count = int(state["contacts"])
    in_contact = bool(state["in_contact"])
    last_contact = _parse_dt(state["last_contact_h1"]) if state["last_contact_h1"] else None
    pending_wick = state["pending_wick"]
    candidate = _parse_dt(state["candidate_h1"]) if state["candidate_h1"] else None
    broken = bool(state["break_confirmed"])
    break_h1 = _parse_dt(state["break_h1"]) if state["break_h1"] else None
    role = bool(state["role_confirmed"])
    processed = 0
    # Genuine post-publication candles only, starting at their OPEN time.
    for idx, (opened, candle) in enumerate(bars):
        if opened < origin or opened <= anchor or (last is not None and opened <= last):
            continue
        if opened > now or opened + timedelta(hours=1) > now:
            continue
        processed += 1
        last = opened
        low, high = float(state["low"]), float(state["high"])
        touched = candle.high >= low and candle.low <= high
        av = float(atr([c for _, c in bars[max(0, idx-14):idx+1]], 14) or 0) if idx >= 3 else 0.0
        buffer = levels.breakout_buffer(zone, av) if av > 0 else max(zone.width * .20, levels.estimated_spread_price(state["symbol"]))
        down_break = candle.close < low - buffer and candle.close < candle.open
        up_break = candle.close > high + buffer and candle.close > candle.open
        if state["kind"] == "support":
            crossed = down_break
            piercing = candle.low < low and candle.close >= low
            recovered = candle.close > high
            holding_role = candle.close < low
        elif state["kind"] == "resistance":
            crossed = up_break
            piercing = candle.high > high and candle.close <= high
            recovered = candle.close < low
            holding_role = candle.close > high
        else:
            # Z1 is monitored for contacts only; direction cannot be assumed.
            crossed = piercing = recovered = holding_role = False
        event = None
        if not broken and candidate is not None:
            if crossed and opened - candidate == timedelta(hours=1):
                broken, break_h1, candidate = True, opened, None
                event = ("break", "Подтверждённый пробой: две закрытые H1 за зоной",
                         "Граница пройдена с запасом. Это технический факт закрытий, не гарантия продолжения.")
            elif not crossed and opened > candidate:
                candidate = None
                event = ("false_break", "Неудачный пробой: цена вернулась к зоне",
                         "После закрытия за границей следующая H1 не закрепилась за ней.")
        if broken and break_h1 is not None and opened > break_h1:
            if touched and holding_role and not role:
                role = True
                event = ("role", "Ретест зоны и подтверждение новой роли",
                         "После предыдущего пробоя H1 коснулась зоны и закрылась со стороны пробоя.")
            elif (state["kind"] == "support" and candle.close > high) or (state["kind"] == "resistance" and candle.close < low):
                # Failed breakdown/breakout: never keep reporting the old role.
                broken, role, candidate, break_h1 = False, False, None, None
                event = ("failed_break", "Возврат за пробитую зону",
                         "Предыдущий подтверждённый пробой больше нельзя считать действующим.")
        if pending_wick and opened > (_parse_dt(pending_wick) or opened):
            if recovered and not broken and event is None:
                event = ("false_wick", "Ложный прокол: закрытие подтвердило возврат",
                         "Прокол был зафиксирован ранее; новое закрытие осталось по исходную сторону зоны.")
            pending_wick = ""
        if not broken and not candidate and crossed:
            candidate = opened
            # First close beyond is not independently a confirmed breakout.
        if not broken and piercing and not pending_wick and event is None:
            pending_wick = _stamp(opened)
            if event is None:
                event = ("wick", "Прокол уровня тенью закрытой H1",
                         "Цена выходила за границу, но закрылась внутри либо по исходную сторону зоны. Следим за подтверждением.")
        first_touch = touched and not in_contact and (last_contact is None or opened - last_contact >= timedelta(hours=2))
        if first_touch:
            contact_count += 1
            last_contact = opened
            if event is None and not broken and not crossed:
                if contact_count == 1:
                    event = ("touch1", "Первое касание после брифинга", "Закрытая H1 пересекла опубликованную ценовую область.")
                elif contact_count == 2:
                    event = ("touch2", "Второе независимое касание после брифинга", "Между касаниями был выход из зоны; это не повтор одного и того же бара.")
        in_contact = touched
        if event is not None:
            _watch_event(conn, state, opened, *event, candle, state["published_at"])
    if processed:
        conn.execute("""UPDATE watched SET last_h1=?, contacts=?, in_contact=?, last_contact_h1=?,
            pending_wick=?, candidate_h1=?, break_h1=?, break_confirmed=?, role_confirmed=? WHERE id=?""",
            (_stamp(last), contact_count, int(in_contact), _stamp(last_contact) if last_contact else "",
             pending_wick, _stamp(candidate) if candidate else "", _stamp(break_h1) if break_h1 else "",
             int(broken), int(role), state["id"]))
    return processed


def observe_market(market: dict, *, now: datetime | None = None, path: Path | None = None) -> int:
    """Replays only NEW fully post-publication H1, idempotent after redeploy."""
    now = now or _now()
    updated = 0
    with _open(path) as conn:
        tracked = conn.execute("SELECT * FROM watched WHERE active=1 AND expires_at>? ORDER BY symbol, published_at", (_stamp(now),)).fetchall()
        for item in tracked:
            symbol = item["symbol"]
            series = _valid_bars((market or {}).get(symbol) or {}, now)
            if not series:
                continue
            updated += _update_one(conn, dict(item), series, now=now)
        conn.execute("UPDATE watched SET active=0 WHERE active=1 AND expires_at<=?", (_stamp(now),))
    return updated


def claim_reports(*, path: Path | None = None, now: datetime | None = None, limit: int = 5) -> list[dict]:
    """Claim reports atomically so concurrent schedulers don't double-send."""
    now = now or _now()
    items = []
    with _open(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("""SELECT id, report, symbol, h1 FROM watch_events
            WHERE sent_at='' AND (claimed_until='' OR claimed_until<=?)
            ORDER BY h1, created_at LIMIT ?""", (_stamp(now), max(1, min(int(limit), 8)))).fetchall()
        hold = _stamp(now + timedelta(minutes=10))
        for row in rows:
            conn.execute("UPDATE watch_events SET claimed_until=? WHERE id=? AND sent_at=''", (hold, row["id"]))
            items.append(dict(row))
    return items


def ack_report(event_id: str, *, path: Path | None = None, sent: bool = True) -> None:
    with _open(path) as conn:
        if sent:
            conn.execute("UPDATE watch_events SET sent_at=?, claimed_until='' WHERE id=?", (_stamp(_now()), event_id))
        else:
            conn.execute("UPDATE watch_events SET claimed_until='' WHERE id=? AND sent_at=''", (event_id,))


def claim_short_digests(*, path: Path | None = None, now: datetime | None = None,
                        limit: int = 5) -> list[dict]:
    """At most one short Telegram report per pair and H1, combining level events.

    The underlying event journal retains individual event facts. A 10-minute
    lease avoids a flood from concurrent schedulers; ACK follows Telegram.
    """
    now = now or _now()
    max_digests = max(1, min(7, int(limit)))
    groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    with _open(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        # Old backlogged events must not surface as if they happened now.
        cutoff = _stamp(now - timedelta(hours=30))
        conn.execute("UPDATE watch_events SET sent_at='expired' WHERE sent_at='' AND h1<?", (cutoff,))
        rows = conn.execute("""SELECT id, watch_id, symbol, h1, event, report
            FROM watch_events WHERE sent_at='' AND (claimed_until='' OR claimed_until<=?)
            ORDER BY h1, created_at LIMIT 100""", (_stamp(now),)).fetchall()
        for row in rows:
            key = (row['symbol'], row['h1'])
            if key not in groups and len(groups) >= max_digests:
                break
            groups.setdefault(key, []).append(row)
        hold = _stamp(now + timedelta(minutes=10))
        out = []
        for (symbol, h1), parts in groups.items():
            selected = parts[:5]
            lines = [f"📍 LEVELS · {symbol} · сопровождение брифинга",
                     f"Закрытая H1: {(_parse_dt(h1) + timedelta(hours=1)).strftime('%d.%m.%Y %H:%M')} UTC"]
            ids=[]
            for row in selected:
                body = row['report'].splitlines()
                source = next((v for v in body if v.startswith('S1 ') or v.startswith('S2 ') or
                               v.startswith('R1 ') or v.startswith('R2 ') or v.startswith('Z1 ')), '')
                event = next((v.removeprefix('Событие: ') for v in body if v.startswith('Событие: ')), row['event'])
                lines.append(f"• {source} · {event}")
                ids.append(row['id'])
                conn.execute("UPDATE watch_events SET claimed_until=? WHERE id=? AND sent_at=''", (hold, row['id']))
            lines.append('OBSERVE_ONLY · исходные зоны брифинга · не торговый сигнал')
            out.append({'ids': ids, 'symbol': symbol, 'h1': h1, 'report': '\n'.join(lines)})
    return out


def ack_digest(ids: list[str], *, path: Path | None = None, sent: bool = True) -> None:
    """Ack all events of a delivered single-chat digest, or release the lease."""
    if not ids:
        return
    with _open(path) as conn:
        for event_id in ids:
            if sent:
                conn.execute("UPDATE watch_events SET sent_at=?, claimed_until='' WHERE id=?",
                             (_stamp(_now()), event_id))
            else:
                conn.execute("UPDATE watch_events SET claimed_until='' WHERE id=? AND sent_at=''", (event_id,))
