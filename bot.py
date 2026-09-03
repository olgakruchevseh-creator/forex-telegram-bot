#!/usr/bin/env python3
"""Личный FX-бот. Мультитаймфрейм W1→D1→H4→H1→M15→M5."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

import config as cfg
from analysis import (
    Candle,
    PairStack,
    bias_word,
    build_stack,
    closed_candles,
    currency_strength,
    decide_signal,
    rank_currencies,
)
import briefing
import news as newsmod
import levels
import zigzag_scanner
import disbalance
import imbalance
import accumulation_distribution
import daily_high_low
import chain_entries
import market_schedule
try:
    import patterns
except ImportError:
    patterns = None

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fxbot")

DEFAULT_STATE_FILE = Path(__file__).parent / "state.json"
STATE_FILE = DEFAULT_STATE_FILE


def state_file() -> Path:
    raw = os.getenv("STATE_DIR", "").strip()
    if raw:
        return Path(raw) / "state.json"
    return STATE_FILE
TD_URL = "https://api.twelvedata.com/time_series"

# cache[(symbol, tf_key)] = {"ts": float, "candles": list[Candle]}
CACHE: dict[tuple[str, str], dict] = {}
SENT_H1: set[str] = set()
LOCAL_TZ = ZoneInfo("Europe/Amsterdam")


def env(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise SystemExit(f"Не задано {name}. Заполни .env")
    return v


def load_state() -> dict:
    dest = state_file()
    src = DEFAULT_STATE_FILE
    if not dest.exists() and src.exists() and src != dest:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(src.read_text())
        except Exception:
            log.exception("Не удалось перенести state.json")
    if dest.exists():
        try:
            return json.loads(dest.read_text())
        except Exception:
            log.exception("Повреждён state.json")
    return {
        "chat_id": os.getenv("TELEGRAM_CHAT_ID") or None,
        "last_signals": {},
        "last_rank": [],
        "last_strength_ts": 0,
    }


def save_state(state: dict) -> None:
    briefing.persist_state(state)


def _parse_values(values: list) -> list[Candle]:
    candles = [
        Candle(dt=v["datetime"], open=float(v["open"]), high=float(v["high"]), low=float(v["low"]), close=float(v["close"]))
        for v in values or []
    ]
    candles.sort(key=lambda x: x.dt)
    return candles


def _request_series(symbols: str, tf: dict, api_key: str) -> dict:
    r = requests.get(
        TD_URL,
        params={
            "symbol": symbols,
            "interval": tf["api"],
            "outputsize": tf["candles"],
            "apikey": api_key,
            "timezone": "UTC",
        },
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and data.get("status") == "error":
        raise RuntimeError(str(data.get("message") or data))
    return data


def _extract_pairs(data: dict) -> dict[str, list[Candle]]:
    out: dict[str, list[Candle]] = {}
    if not isinstance(data, dict):
        return out
    if "values" in data and isinstance(data.get("values"), list):
        # одиночный ответ без имени пары
        return out
    for symbol in cfg.PAIRS:
        block = data.get(symbol)
        if isinstance(block, dict) and block.get("status") == "error":
            log.warning("%s: %s", symbol, block.get("message"))
            continue
        values = block.get("values") if isinstance(block, dict) else None
        if values:
            out[symbol] = _parse_values(values)
    return out


def fetch_tf_batch(tf: dict, api_key: str) -> dict[str, list[Candle]]:
    """Сначала пакет из 7 пар, если пусто — по одной."""
    try:
        data = _request_series(",".join(cfg.PAIRS), tf, api_key)
        out = _extract_pairs(data)
        if out:
            return out
    except Exception as e:
        log.warning("Пакет %s не вышел: %s", tf["key"], e)

    out: dict[str, list[Candle]] = {}
    for symbol in cfg.PAIRS:
        try:
            data = _request_series(symbol, tf, api_key)
            if isinstance(data, dict) and isinstance(data.get("values"), list):
                out[symbol] = _parse_values(data["values"])
            else:
                part = _extract_pairs(data)
                if symbol in part:
                    out[symbol] = part[symbol]
        except Exception as e:
            log.warning("%s %s: %s", symbol, tf["key"], e)
        time.sleep(cfg.REQUEST_PAUSE_SEC)
    return out


def fetch_market(api_key: str, force: bool = False) -> dict[str, dict[str, list[Candle]]]:
    """symbol -> {tf_key -> candles}. На платном плане — batch по каждому ТФ."""
    market: dict[str, dict[str, list[Candle]]] = {s: {} for s in cfg.PAIRS}
    now = time.time()
    for tf in cfg.TIMEFRAMES:
        stale = force or any(
            (s, tf["key"]) not in CACHE
            or now - CACHE[(s, tf["key"])]["ts"] >= tf["ttl_min"] * 60
            or not CACHE[(s, tf["key"])]["candles"]
            for s in cfg.PAIRS
        )
        if not stale:
            for s in cfg.PAIRS:
                market[s][tf["key"]] = CACHE[(s, tf["key"])]["candles"]
            continue
        try:
            batch = fetch_tf_batch(tf, api_key)
            ts = time.time()
            for s, candles in batch.items():
                CACHE[(s, tf["key"])] = {"ts": ts, "candles": candles}
                market[s][tf["key"]] = candles
            time.sleep(cfg.REQUEST_PAUSE_SEC)
        except Exception as e:
            log.warning("Пакет %s: %s", tf["key"], e)
            for s in cfg.PAIRS:
                hit = CACHE.get((s, tf["key"]))
                if hit:
                    market[s][tf["key"]] = hit["candles"]
    return market


def h1_series(market: dict[str, dict[str, list[Candle]]]) -> dict[str, list[Candle]]:
    return {s: tfs.get("H1") or [] for s, tfs in market.items()}


def bars(score: float) -> str:
    n = max(0, min(10, int(round((score + 1.5) / 3 * 10))))
    return "█" * n + "░" * (10 - n)


def last_closed_h1_dt(h1: dict[str, list[Candle]]) -> str:
    dts = []
    for candles in h1.values():
        closed = closed_candles(candles)
        if closed:
            dts.append(closed[-1].dt)
    return max(dts) if dts else ""


def h1_just_closed(dt_str: str, max_min: int = 12) -> bool:
    raw = (dt_str or "")[:19]
    if not raw:
        return False
    try:
        opened = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    closed_at = opened + timedelta(hours=1)
    age = (datetime.now(timezone.utc) - closed_at).total_seconds() / 60.0
    return 0 <= age <= max_min


def format_h1_time(dt_str: str) -> str:
    raw = (dt_str or "")[:19]
    try:
        utc = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        local = utc.astimezone(LOCAL_TZ)
        return f"{local:%H:%M} по Амстердаму ({utc:%H:%M} UTC)"
    except ValueError:
        return dt_str


def format_strength(rank: list[tuple[str, float]], candle_dt: str = "") -> str:
    title = "💱 Сила валют по закрытой часовой свече"
    if candle_dt:
        title += f"\nСвеча: {format_h1_time(candle_dt)}"
    lines = [title + "\n"]
    pct = briefing.strength_pct(rank)
    for i, (cur, sc) in enumerate(pct, 1):
        lines.append(f"{i}. {cur}  {sc:.0f}%")
    if pct:
        lines.append(f"\nСамая сильная: {pct[0][0]} ({pct[0][1]:.0f}%)")
        lines.append(f"Самая слабая: {pct[-1][0]} ({pct[-1][1]:.0f}%)")
        lines.append(f"Разница силы: {pct[0][1] - pct[-1][1]:.0f} п.п.")
    return "\n".join(lines)


def format_stack_block(stack: PairStack) -> list[str]:
    lines = ["Таймфреймы:"]
    for tf in cfg.TIMEFRAMES:
        v = stack.views.get(tf["key"])
        if not v:
            lines.append(f"• {tf['label']}: нет данных")
            continue
        arrow = "↑" if v.bias > 0 else "↓" if v.bias < 0 else "•"
        lines.append(f"• {tf['label']}: {arrow} {v.structure}; {v.phase} (ADX {v.adx:.0f})")
    return lines


def format_signal(side: str, stack: PairStack, strength: dict[str, float]) -> str:
    base, quote = stack.symbol.split("/")
    icon = "🟢" if side == "LONG" else "🔴"
    facts = [
        f"{icon} {side} {stack.symbol}",
        "",
        "Факты:",
        f"• Старшие ТФ (W/D/H4): {bias_word(stack.htf_bias)}",
        f"• Младшие ТФ (H1/M15/M5): {bias_word(stack.ltf_bias)}",
        f"• {base} {strength[base]:+.2f} vs {quote} {strength[quote]:+.2f} (разница {stack.strength_gap:+.2f})",
        f"• Цена: {stack.last}",
        "",
    ]
    facts.extend(format_stack_block(stack))
    h1 = stack.views.get("H1") or stack.views.get("M15")
    if h1 and h1.nearby_fvg:
        kind = "бычий FVG" if h1.nearby_fvg.kind == "bull" else "медвежий FVG"
        facts.append(f"\n• Рядом {kind}: {h1.nearby_fvg.bottom:.5f}–{h1.nearby_fvg.top:.5f}")
    return "\n".join(facts)


def format_pair_now(stack: PairStack) -> str:
    lines = [f"📊 {stack.symbol}  {stack.last}", ""]
    lines.extend(format_stack_block(stack))
    lines.append(f"\nСтаршие: {bias_word(stack.htf_bias)}")
    lines.append(f"Младшие: {bias_word(stack.ltf_bias)}")
    return "\n".join(lines)


def rank_changed(old: list, new: list[tuple[str, float]]) -> bool:
    if not old:
        return True
    old_map = {c: i for i, c in enumerate(old)}
    for i, (cur, _) in enumerate(new):
        if cur not in old_map:
            return True
        if abs(old_map[cur] - i) >= cfg.STRENGTH_RANK_JUMP:
            return True
    return False


def cooldown_ok(state: dict, symbol: str, side: str) -> bool:
    last = state.get("last_signals", {}).get(f"{symbol}:{side}")
    if not last:
        return True
    return (time.time() - last) / 3600 >= cfg.SIGNAL_COOLDOWN_HOURS


async def send(app: Application, chat_id: int, text: str):
    msg = await app.bot.send_message(chat_id=chat_id, text=text)
    mid = getattr(msg, "message_id", None)
    log.info("telegram_message_id=%s pid=%s", mid, briefing.instance_id())
    return mid


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    state["chat_id"] = update.effective_chat.id
    save_state(state)
    await update.message.reply_text(
        "Готово. Я запомнил тебя.\n"
        "Смотрю W1, D1, H4, H1, M15, M5.\n"
        "Пишу только когда старшие и младшие ТФ смотрят в одну сторону "
        "и сила валют это подтверждает.\n"
        "После закрытия каждой часовой свечи присылаю полный брифинг.\n\n"
        "/now — сила валют сейчас\n"
        "/pair EUR/USD — стек таймфреймов по паре\n"
        "/briefing — брифинг текущей сессии\n"
        "/status — жив ли я"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Работаю. 7 мажоров × W1/D1/H4/H1/M15/M5.\n"
        "Полный брифинг — после закрытия каждой H1."
    )


async def cmd_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Считаю силу валют по H1…")
    market = fetch_market(env("TWELVE_DATA_API_KEY"), force=True)
    h1 = h1_series(market)
    if not any(len(closed_candles(v)) > cfg.STRENGTH_LOOKBACK for v in h1.values()):
        await update.message.reply_text(
            "Котировки H1 сейчас не пришли. Повтори /now через минуту."
        )
        return
    strength = currency_strength(h1, cfg.STRENGTH_LOOKBACK)
    rank = rank_currencies(strength)
    await update.message.reply_text(format_strength(rank, last_closed_h1_dt(h1)))


async def cmd_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = " ".join(context.args).upper().replace(" ", "")
    if not raw:
        await update.message.reply_text("Напиши так: /pair EUR/USD")
        return
    if "/" not in raw and len(raw) == 6:
        raw = raw[:3] + "/" + raw[3:]
    if raw not in cfg.PAIRS:
        await update.message.reply_text("Доступны: " + ", ".join(cfg.PAIRS))
        return
    await update.message.reply_text(f"Собираю стек по {raw}…")
    market = fetch_market(env("TWELVE_DATA_API_KEY"), force=True)
    got = {k: len(v) for k, v in (market.get(raw) or {}).items()}
    strength = currency_strength(h1_series(market), cfg.STRENGTH_LOOKBACK)
    stack = build_stack(raw, market.get(raw) or {}, strength)
    if not stack:
        detail = ", ".join(f"{k}:{n}" for k, n in got.items()) or "пусто"
        await update.message.reply_text(
            "Не хватает данных по таймфреймам.\n"
            f"Пришло свечей: {detail}\n"
            "Повтори команду через минуту."
        )
        return
    await update.message.reply_text(format_pair_now(stack))


async def cmd_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Собираю брифинг сессии…")
    try:
        api_key = env("TWELVE_DATA_API_KEY")
        market = fetch_market(api_key)
        if not any(h1_series(market).values()):
            market = fetch_market(api_key, force=True)
        strength = currency_strength(h1_series(market), cfg.STRENGTH_LOOKBACK)
        rank = rank_currencies(strength)
        if not rank:
            await update.message.reply_text(
                "Брифинг не собрался: нет закрытых H1. Напиши /now или повтори через минуту."
            )
            return
        dxy = None
        try:
            dxy = briefing.collect_extras(
                api_key, force=True, h1_dt=last_closed_h1_dt(h1_series(market)), market=market
            )
        except Exception:
            log.exception("DXY для /briefing")
        events = []
        try:
            events = briefing.session_events(newsmod.load_events())
        except Exception:
            log.exception("Новости для /briefing")
        try:
            text = briefing.build_briefing_text(market, strength, rank, dxy, events)
        except Exception:
            log.exception("Сборка текста /briefing")
            text = format_strength(rank, last_closed_h1_dt(h1_series(market)))
            text += "\n\nПолная доска пар сейчас недоступна."
        for part in briefing.split_telegram(text):
            await update.message.reply_text(part)
    except Exception:
        log.exception("Ошибка /briefing")
        await update.message.reply_text(
            "Брифинг не собрался. /now работает отдельно. Повтори команду через минуту."
        )


async def _send_parts(app: Application, chat_id: int, text: str) -> None:
    for part in briefing.split_telegram(text):
        await send(app, chat_id, part)


def _alert_pair(text: str) -> str:
    match = re.search(r"(?:Пара:\s*|💱 Пара:\s*)([A-Z]{3}/[A-Z]{3})", text or "")
    return match.group(1) if match else ""


def select_trade_alerts(items: list[tuple[int, str]], limit: int = 2, blocked_pairs=None) -> list[str]:
    """One alert per pair, at most two strongest alerts per market scan."""
    ranked = sorted(items, key=lambda item: item[0])
    chosen, pairs = [], set(blocked_pairs or [])
    for _priority, text in ranked:
        pair = _alert_pair(text)
        if pair and pair in pairs:
            continue
        chosen.append(text)
        if pair:
            pairs.add(pair)
        if len(chosen) >= limit:
            break
    return chosen


async def briefing_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    # Выход до чтения API-ключа и запросов: в выходные нет расхода кредитов.
    if not market_schedule.automatic_jobs_allowed():
        return
    if not cfg.BRIEFING_ENABLED:
        return
    state = load_state()
    chat_id = state.get("chat_id")
    if not chat_id:
        return
    state.setdefault("news_warned", {})
    state.setdefault("news_actual_sent", {})
    api_key = env("TWELVE_DATA_API_KEY")
    try:
        market = fetch_market(api_key)
        strength = currency_strength(h1_series(market), cfg.STRENGTH_LOOKBACK)
        rank = rank_currencies(strength)
        dxy = briefing.collect_extras(api_key, market=market)
        all_events = newsmod.load_events()
        now_utc = datetime.now(timezone.utc)
        for event in newsmod.high_events(all_events):
            left = newsmod.minutes_left(event, now_utc)
            if 50 <= left <= cfg.NEWS_WARN_MINUTES + 8:
                if event.event_id in state["news_warned"]:
                    continue
                fresh_m = fetch_market(api_key, force=True)
                fresh_s = currency_strength(h1_series(fresh_m), cfg.STRENGTH_LOOKBACK)
                fresh_r = rank_currencies(fresh_s)
                fresh_dxy = briefing.collect_extras(api_key, market=fresh_m)
                state["news_warned"][event.event_id] = time.time()
                save_state(state)
                await _send_parts(
                    context.application,
                    int(chat_id),
                    briefing.format_news_warning(event, fresh_s, fresh_r, fresh_dxy),
                )
            if newsmod.has_actual(event) and event.event_id not in state["news_actual_sent"]:
                verdict = newsmod.interpret_print(event)
                state["news_actual_sent"][event.event_id] = time.time()
                save_state(state)
                msg = briefing.format_actual_update(event, verdict, dxy, strength.get("USD", 0.0))
                if msg:
                    await _send_parts(context.application, int(chat_id), msg)
    except Exception:
        log.exception("Ошибка брифинга")


async def scan_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    # Автоматические брифинги, новости и все модули молчат в субботу/воскресенье.
    if not market_schedule.automatic_jobs_allowed():
        return
    state = load_state()
    chat_id = state.get("chat_id")
    if not chat_id:
        log.info("Нет chat_id. Напиши боту /start.")
        return
    try:
        market = fetch_market(env("TWELVE_DATA_API_KEY"))
        strength = currency_strength(h1_series(market), cfg.STRENGTH_LOOKBACK)
        rank = rank_currencies(strength)

        h1 = h1_series(market)
        closed_dt = last_closed_h1_dt(h1)
        empty = bool(rank) and (max(s for _, s in rank) - min(s for _, s in rank) < 1e-12)
        iid = briefing.issue_id(closed_dt, chat_id) if closed_dt else ""
        log.info(
            "briefing_key=%s pid=%s reason=scan_job h1=%s current=%s empty=%s",
            iid,
            briefing.instance_id(),
            closed_dt,
            briefing.h1_is_current(closed_dt, cfg.BRIEFING_OPEN_WINDOW_MIN) if closed_dt else False,
            empty,
        )
        if closed_dt and iid and briefing.h1_is_current(closed_dt, cfg.BRIEFING_OPEN_WINDOW_MIN) and not empty and rank:
            if briefing.issue_sent(state, iid):
                log.info("DUPLICATE_SKIPPED briefing_key=%s pid=%s reason=already_sent", iid, briefing.instance_id())
            elif not briefing.claim_issue(state, iid):
                log.info("DUPLICATE_SKIPPED briefing_key=%s pid=%s reason=claim_failed", iid, briefing.instance_id())
                save_state(state)
            else:
                save_state(state)
                sent_ok = False
                try:
                    api_key = env("TWELVE_DATA_API_KEY")
                    try:
                        dxy = briefing.collect_extras(
                            api_key,
                            h1_dt=closed_dt,
                            market=market,
                        )
                    except Exception:
                        log.exception("DXY часового брифинга")
                        dxy = None
                    try:
                        events = briefing.session_events(newsmod.load_events())
                    except Exception:
                        log.exception("новости часового брифинга")
                        events = []
                    text = briefing.build_briefing_text(market, strength, rank, dxy, events)
                    parts = briefing.prepare_telegram_parts(text)
                    already = set(briefing.delivered_parts(state, iid))
                    for idx, part in enumerate(parts, 1):
                        if idx in already:
                            log.info("часть %s/%s уже доставлена, пропуск", idx, len(parts))
                            continue
                        await send(context.application, int(chat_id), part)
                        briefing.mark_part_delivered(state, iid, idx, len(parts))
                    if len(briefing.delivered_parts(state, iid)) >= len(parts):
                        briefing.mark_issue_sent(state, iid, len(parts))
                    state["last_rank"] = [c for c, _ in rank]
                    state["last_strength_h1"] = closed_dt
                    state["last_strength_ts"] = time.time()
                    state["last_briefing_id"] = briefing.briefing_id()
                    state["last_briefing_sent_ts"] = time.time()
                    SENT_H1.add(closed_dt)
                    sent_ok = True
                    log.info("брифинг отправлен issue=%s parts=%s", iid, len(parts))
                except Exception:
                    log.exception("отправка часового брифинга")
                    briefing.release_issue(state, iid)
                if not sent_ok:
                    briefing.release_issue(state, iid)
                save_state(state)

        for symbol, by_tf in market.items():
            stack = build_stack(symbol, by_tf, strength)
            if not stack:
                continue
            side = decide_signal(stack)
            if not side or not cooldown_ok(state, symbol, side):
                continue
            await send(context.application, int(chat_id), format_signal(side, stack, strength))
            state.setdefault("last_signals", {})[f"{symbol}:{side}"] = time.time()

        module_alerts: list[tuple[int, str]] = []
        if getattr(cfg, "DISBALANCE_ENABLED", True):
            try:
                for text in disbalance.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                log.exception("Ошибка модуля дисбаланса")

        if getattr(cfg, "IMBALANCE_ENABLED", True):
            try:
                for text in imbalance.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                log.exception("Ошибка модуля Imbalance/FVG")

        if getattr(cfg, "ACCUMULATION_DISTRIBUTION_ENABLED", True):
            try:
                for text in accumulation_distribution.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                log.exception("Ошибка модуля накопления/распределения")

        if getattr(cfg, "DAILY_HIGH_LOW_ENABLED", True):
            try:
                for text in daily_high_low.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                log.exception("Ошибка модуля дневного максимума/минимума")

        if getattr(cfg, "CHAIN_ENTRIES_ENABLED", True):
            try:
                for text in chain_entries.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                log.exception("Ошибка модуля Chain Entries")

        if getattr(cfg, "LEVELS_ENABLED", True):
            try:
                for text in levels.process_market(market):
                    module_alerts.append((0, text))
            except Exception:
                log.exception("Ошибка модуля уровней")

        if getattr(cfg, "ZIGZAG_SCANNER_ENABLED", True):
            try:
                for text in zigzag_scanner.process_market(market):
                    module_alerts.append((2, text))
            except Exception:
                log.exception("Ошибка отдельного ZigZag-сканера")

        if patterns is not None and getattr(cfg, "PATTERNS_ENABLED", True):
            try:
                for text in patterns.process_market(market):
                    structural = any(name in text for name in ("BOS", "Двойная", "голова и плечи", "AB=CD"))
                    module_alerts.append((1 if structural else 3, text))
            except Exception:
                log.exception("Ошибка сканера паттернов")

        buckets = state.setdefault("module_alert_buckets", {})
        bucket_key = closed_dt or datetime.now(timezone.utc).strftime("%Y-%m-%d %H")
        bucket = buckets.setdefault(bucket_key, {"count": 0, "pairs": []})
        remaining = max(0, 2 - int(bucket.get("count") or 0))
        selected_alerts = select_trade_alerts(
            module_alerts,
            limit=remaining,
            blocked_pairs=set(bucket.get("pairs") or []),
        ) if remaining else []
        for text in selected_alerts:
            await _send_parts(context.application, int(chat_id), text)
            pair = _alert_pair(text)
            bucket["count"] = int(bucket.get("count") or 0) + 1
            if pair and pair not in bucket.setdefault("pairs", []):
                bucket["pairs"].append(pair)
        # One small current-H1 record is enough; old budgets cannot affect new hours.
        state["module_alert_buckets"] = {bucket_key: bucket}

        save_state(state)
        log.info("Скан %s OK top=%s", datetime.now(timezone.utc).strftime("%H:%M"), rank[0][0] if rank else "-")
    except Exception:
        log.exception("Ошибка скана")


def main() -> None:
    token = env("TELEGRAM_TOKEN")
    env("TWELVE_DATA_API_KEY")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("now", cmd_now))
    app.add_handler(CommandHandler("pair", cmd_pair))
    app.add_handler(CommandHandler("briefing", cmd_briefing))
    app.job_queue.run_repeating(scan_job, interval=cfg.SCAN_EVERY_MINUTES * 60, first=20)
    app.job_queue.run_repeating(briefing_job, interval=cfg.SCAN_EVERY_MINUTES * 60, first=45)
    log.info("Бот запущен.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
