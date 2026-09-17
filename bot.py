#!/usr/bin/env python3
"""Личный FX-бот. Мультитаймфрейм W1→D1→H4→H1→M15→M5."""
from __future__ import annotations

import json
import hashlib
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
    market_coverage,
    rank_currencies,
)
import briefing
import news as newsmod
import levels
import zigzag_scanner
import disbalance
import imbalance
import accumulation_distribution
import consolidation_zone
import amd_power_of_three
import crt_candle_range
import movement_progress
import liquidity_sweep
import poc_profile
import order_block
import breaker_block
import smart_money_62_26
import silver_bullet_ict
import daily_high_low
import chain_entries
import retest_confirmation
import fibonacci_grid
import fib_smc
import ats_reversal_point
import market_schedule
import master_direction
import signal_navigator
import signal_context
import signal_journal
import session_projection_reports
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
        lines.append(f"• {tf['label']}: структура — {v.structure}; импульс — {v.phase}; итоговый уклон {arrow} (ADX {v.adx:.0f})")
    return lines


def format_signal(side: str, stack: PairStack, strength: dict[str, float]) -> str:
    base, quote = stack.symbol.split("/")
    icon = "🟢" if side == "LONG" else "🔴"
    wanted = 1 if side == "LONG" else -1
    senior = [stack.views[k].bias for k in cfg.HTF_KEYS if k in stack.views]
    junior = [stack.views[k].bias for k in cfg.LTF_KEYS if k in stack.views]
    senior_agree = sum(1 for value in senior if value == wanted)
    junior_agree = sum(1 for value in junior if value == wanted)
    facts = [
        f"{icon} {side} {stack.symbol}",
        "",
        "Факты:",
        f"• Старшие ТФ (W1/D1/H4): {senior_agree} из {len(senior)} подтверждают {side}",
        f"• Младшие ТФ (H1/M15/M5): {junior_agree} из {len(junior)} подтверждают {side}",
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


class ScanStats:
    def __init__(self) -> None:
        self.ran = 0
        self.failed = 0
        self.failed_names: list[str] = []

    def note_run(self, name: str) -> None:
        self.ran += 1

    def note_fail(self, name: str) -> None:
        self.failed += 1
        self.failed_names.append(name)

    def abort_delivery(self) -> bool:
        if self.ran <= 0:
            return False
        ratio = self.failed / self.ran
        limit = float(getattr(cfg, "SCAN_FAIL_ABORT_RATIO", 0.35))
        if ratio >= limit:
            log.error(
                "SCAN_ABORT_DELIVERY failed=%s/%s ratio=%.2f modules=%s",
                self.failed, self.ran, ratio, ",".join(self.failed_names),
            )
            return True
        if self.failed:
            log.warning(
                "SCAN_PARTIAL failed=%s/%s modules=%s",
                self.failed, self.ran, ",".join(self.failed_names),
            )
        return False


def _source_image_for(text: str, source_text: str):
    """Единая точка картинки. Ключ всегда исходный source_text, не подпись с CPI."""
    try:
        if "⚖️ ДИСБАЛАНС ПОДТВЕРЖДЁН" in text:
            return disbalance.image_for_alert(source_text)
        if "IMBALANCE —" in text:
            return imbalance.image_for_alert(source_text)
        if "↕️ ZIGZAG —" in text:
            return zigzag_scanner.image_for_alert(source_text)
        if patterns is not None and "🧩 ПАТТЕРН ПОДТВЕРЖДЁН" in text:
            return patterns.image_for_alert(source_text)
        if "🎯 AMD / POWER OF THREE" in text:
            return amd_power_of_three.image_for_alert(source_text)
        if "🕯 CRT — CANDLE RANGE THEORY" in text:
            return crt_candle_range.image_for_alert(source_text)
        if "📐 РЕАКЦИЯ ОТ СЕТКИ ФИБОНАЧЧИ" in text:
            return fibonacci_grid.image_for_alert(source_text)
        if "🎯 ATS REVERSAL POINT" in text:
            return ats_reversal_point.image_for_alert(source_text)
        if "🧬 FIB + SMC —" in text:
            return fib_smc.image_for_alert(source_text)
        if "🎯 POC —" in text:
            return poc_profile.image_for_alert(source_text)
        if "🧱 РЕТЕСТ ORDER BLOCK" in text:
            return order_block.image_for_alert(source_text)
        if "🔄 BREAKER BLOCK ПОДТВЕРЖДЁН" in text:
            return breaker_block.image_for_alert(source_text)
        if "🥈 ICT SILVER BULLET —" in text:
            return silver_bullet_ict.image_for_alert(source_text)
        if "🏦 SMART MONEY 62-26" in text:
            return smart_money_62_26.image_for_alert(source_text)
        if "СНЯТИЕ ЛИКВИДНОСТИ" in text.upper():
            return liquidity_sweep.image_for_alert(source_text)
        if "РЕТЕСТ" in text.upper() and "ORDER BLOCK" not in text.upper() and "УРОВЕНЬ" not in text.upper() and "УРОВНЯ" not in text.upper():
            return retest_confirmation.image_for_alert(source_text)
        if "⛓️ CHAIN ENTRY" in text:
            return chain_entries.image_for_alert(source_text)
        if ("📅 ПРОБОЙ PDH" in text or "📅 ПРОБОЙ PDL" in text or
                "📅 СНЯТИЕ PDH И ВОЗВРАТ" in text or "📅 СНЯТИЕ PDL И ВОЗВРАТ" in text):
            return daily_high_low.image_for_alert(source_text)
        if "📦 ВЫХОД ИЗ ЗОНЫ КОНСОЛИДАЦИИ" in text:
            return consolidation_zone.image_for_alert(source_text)
        if "🚀 ВЫХОД ИЗ ФАЗЫ" in text or "📦 ФАЗА " in text:
            return accumulation_distribution.image_for_alert(source_text)
        if any(tag in text for tag in (
            "📍 СИЛЬНЫЙ УРОВЕНЬ", "↘️ ОТБОЙ ОТ СОПРОТИВЛЕНИЯ",
            "↗️ ОТБОЙ ОТ ПОДДЕРЖКИ", "⚡ ПРОБОЙ УРОВНЯ",
            "📌 УДЕРЖАНИЕ ПОДТВЕРЖДЕНО", "↘️ ЛОЖНЫЙ ПРОБОЙ СОПРОТИВЛЕНИЯ",
            "↗️ ЛОЖНЫЙ ПРОБОЙ ПОДДЕРЖКИ", "🔄 РЕТТЕСТ УРОВНЯ",
            "🔄 СМЕНА РОЛИ УРОВНЯ", "❌ УРОВЕНЬ НЕДЕЙСТВИТЕЛЕН",
        )):
            return levels.image_for_alert(source_text)
    except Exception:
        log.exception("Подготовка изображения карточки")
    return None


async def _deliver_trade_card(app: Application, chat_id: int, text: str, image=None) -> None:
    """Единственный выход торговой карточки в Telegram."""
    if image is not None:
        if len(text) <= 1000:
            await app.bot.send_photo(chat_id=int(chat_id), photo=image, caption=text)
        else:
            await app.bot.send_photo(
                chat_id=int(chat_id), photo=image,
                caption="📊 Сценарий подтверждён · полный разбор следующим сообщением",
            )
            await _send_parts(app, int(chat_id), text)
        return
    await _send_parts(app, int(chat_id), text)


def _source_event_id(text: str) -> str:
    """Stable routing identity. Module-specific IDs win over mutable message text."""
    if "📦 ВЫХОД ИЗ ЗОНЫ КОНСОЛИДАЦИИ" in (text or ""):
        try:
            event_id = consolidation_zone.event_id_for_alert(text)
            if event_id:
                return event_id
        except Exception:
            log.exception("CONSOLIDATION_EVENT_ID_FAILED")
    if "↕️ ZIGZAG —" in (text or ""):
        try:
            event_id = zigzag_scanner.event_id_for_alert(text)
            if event_id:
                return event_id
        except Exception:
            log.exception("ZIGZAG_EVENT_ID_FAILED")
    return hashlib.sha256((text or "").encode()).hexdigest()[:24]


def _event_already_delivered(state: dict, event_id: str) -> bool:
    return bool(event_id and event_id in (state.get("delivered_event_ids") or {}))


def _mark_event_delivered(state: dict, event_id: str) -> None:
    if not event_id:
        return
    registry = state.setdefault("delivered_event_ids", {})
    registry[event_id] = time.time()
    # Bounded durable registry: enough history to survive restarts without state growth.
    if len(registry) > 1200:
        keep = sorted(registry.items(), key=lambda kv: kv[1])[-900:]
        state["delivered_event_ids"] = dict(keep)


def _enqueue_source(state: dict, text: str) -> None:
    """Durable source-card outbox: persist before Telegram I/O, remove only after success."""
    if not text:
        return
    outbox = state.setdefault("source_outbox", [])
    if not any(item.get("text") == text for item in outbox if isinstance(item, dict)):
        outbox.append({"text": text, "saved_at": time.time()})
    state["source_outbox"] = outbox[-60:]


def _ack_source(state: dict, text: str) -> None:
    state["source_outbox"] = [item for item in state.get("source_outbox", [])
                              if not isinstance(item, dict) or item.get("text") != text]


async def _flush_source_outbox(app: Application, chat_id: int, state: dict) -> None:
    """Retry source cards lost to a transient Telegram/Railway send failure."""
    outbox = state.setdefault("source_outbox", [])
    while outbox:
        item = outbox[0]
        text = item.get("text", "") if isinstance(item, dict) else ""
        if not text:
            outbox.pop(0); continue
        try:
            await _send_parts(app, chat_id, text)
            outbox.pop(0); save_state(state)
        except Exception:
            log.exception("Повторная доставка исходной торговой карточки")
            save_state(state); break


def _enqueue_navigator(state: dict, text: str, event_id: str = "") -> None:
    """One Navigator launch per source market event, with durable retry semantics."""
    if not text:
        return
    nav_key = f"NAV:{event_id}" if event_id else hashlib.sha256(text.encode()).hexdigest()[:24]
    if nav_key in (state.get("navigator_event_ids") or {}):
        return
    outbox = state.setdefault("navigator_outbox", [])
    if not any((item.get("event_id") == nav_key or item.get("text") == text)
               for item in outbox if isinstance(item, dict)):
        outbox.append({"text": text, "event_id": nav_key, "saved_at": time.time()})
    state["navigator_outbox"] = outbox[-30:]


async def _flush_navigator_outbox(app: Application, chat_id: int, state: dict) -> None:
    """Доставляет Навигатор и оставляет неотправленное для следующего скана."""
    outbox = state.setdefault("navigator_outbox", [])
    while outbox:
        item = outbox[0]
        text = item.get("text", "") if isinstance(item, dict) else ""
        if not text:
            outbox.pop(0)
            continue
        try:
            await _send_parts(app, chat_id, text)
            signal_navigator.mark_delivered(text)
            nav_event_id = item.get("event_id", "") if isinstance(item, dict) else ""
            if nav_event_id:
                registry = state.setdefault("navigator_event_ids", {})
                registry[nav_event_id] = time.time()
                if len(registry) > 1200:
                    state["navigator_event_ids"] = dict(sorted(registry.items(), key=lambda kv: kv[1])[-900:])
            outbox.pop(0)
            save_state(state)
        except Exception:
            log.exception("Доставка обязательного сопровождения Навигатора")
            save_state(state)
            break


def _alert_pair(text: str) -> str:
    match = re.search(r"(?:Пара:\s*|💱 Пара:\s*)([A-Z]{3}/[A-Z]{3})", text or "")
    if not match:
        match = re.search(r"(?:LONG|SHORT)\s+([A-Z]{3}/[A-Z]{3})", text or "")
    return match.group(1) if match else ""


def _direct_signal_side(text: str) -> str:
    """Extract direction from every trading-module card format.

    Levels use "Направление реакции/пробоя", ATS uses "Направление разворота",
    while most modules use plain "Направление". Navigator routing must treat
    all of them identically.
    """
    match = re.search(r"(?:^|\n)[🟢🔴]?\s*(LONG|SHORT)\s+[A-Z]{3}/[A-Z]{3}", text or "", re.I)
    if not match:
        match = re.search(
            r"(?:🧭\s*)?Направление(?:\s+(?:реакции|пробоя|разворота))?:\s*(LONG|SHORT)\b",
            text or "", re.I,
        )
    return match.group(1).upper() if match else ""


def signal_allowed_by_h4_zigzag(symbol: str, by_tf: dict, side: str) -> bool:
    """Use the same confirmed H4 ZigZag direction as the hourly briefing."""
    if not getattr(cfg, "SIGNAL_BLOCK_OPPOSITE_H4_ZIGZAG", True):
        return True
    try:
        snapshot = zigzag_scanner.analyze_symbol(symbol, by_tf)
        h4_side = int((snapshot.get("zigzag_directions") or {}).get("H4", 0))
    except Exception:
        log.exception("Не удалось проверить ZigZag H4 для %s", symbol)
        return False
    wanted = 1 if side == "LONG" else -1
    return not h4_side or h4_side == wanted


def _alert_metric(text: str, label: str) -> int | None:
    match = re.search(rf"(?:^|\n)[^\n]*{re.escape(label)}:\s*(\d{{1,3}})(?:/100|%)?", text or "", re.I)
    return max(0, min(100, int(match.group(1)))) if match else None


def _alert_rank(item: tuple[int, str]) -> tuple[float, int, int]:
    """Качество/вероятность главнее прежнего фиксированного порядка модулей."""
    priority, text = item
    quality = _alert_metric(text, "Качество")
    probability = _alert_metric(text, "Уверенность модели")
    if probability is None:
        probability = _alert_metric(text, "Вероятность")
    # У ZigZag и некоторых структурных событий числовой оценки нет. Для них
    # сохраняется спокойный базовый балл и прежний приоритет как tie-breaker.
    default = {0: 78, 1: 75, 2: 70, 3: 68}.get(priority, 70)
    quality = quality if quality is not None else default
    probability = probability if probability is not None else quality
    combined = probability * .65 + quality * .35
    # sorted() идёт по возрастанию: отрицательные значения ставят лучший факт первым.
    return (-combined, priority, -probability)


def select_trade_alerts(items: list[tuple[int, str]], limit: int = 2, blocked_pairs=None) -> list[str]:
    """Лучшие числовые сигналы, максимум один на пару в часовом бюджете."""
    ranked = sorted(items, key=_alert_rank)
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


def merge_navigator_with_sources(
    raw_items: list[tuple[int, str]], confirmed_items: list[tuple[int, str]]
) -> list[tuple[int, str]]:
    """Навигатор улучшает исходный сигнал, но больше не блокирует его доставку.

    Если по той же паре и направлению уже готова карточка Навигатора, она
    заменяет исходную карточку. Остальные сработавшие модули остаются в очереди
    и могут быть отправлены в пределах общего часового бюджета.
    """
    replaced = {
        (_alert_pair(text), _direct_signal_side(text))
        for _priority, text in confirmed_items
        if _alert_pair(text) and _direct_signal_side(text)
    }
    fallback = [
        item for item in raw_items
        if (_alert_pair(item[1]), _direct_signal_side(item[1])) not in replaced
    ]
    return list(confirmed_items) + fallback


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
                await _send_parts(
                    context.application,
                    int(chat_id),
                    briefing.format_news_warning(event, fresh_s, fresh_r, fresh_dxy),
                )
                # Фиксируем предупреждение только после подтверждённой отправки.
                # При ошибке Telegram следующий скан повторит попытку.
                state["news_warned"][event.event_id] = time.time()
                save_state(state)
            if newsmod.has_actual(event) and event.event_id not in state["news_actual_sent"]:
                verdict = newsmod.interpret_print(event)
                msg = briefing.format_actual_update(
                    event, verdict, dxy, strength.get("USD", 0.0), all_events
                )
                if msg:
                    await _send_parts(context.application, int(chat_id), msg)
                # Для одновременных headline/core CPI отправляем одну сводную карточку
                # и помечаем весь кластер, чтобы не получить два противоречивых сообщения.
                if newsmod.is_cpi_event(event):
                    cluster = newsmod.cpi_release_consensus(all_events, event.currency, event).get("events") or [event]
                    for cluster_event in cluster:
                        state["news_actual_sent"][cluster_event.event_id] = time.time()
                else:
                    state["news_actual_sent"][event.event_id] = time.time()
                save_state(state)
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
        coverage = market_coverage(market)
        if not coverage["complete"]:
            log.warning(
                "MARKET_INCOMPLETE present=%s/%s missing=%s short_h1=%s",
                coverage["present"], coverage["expected"],
                ",".join(coverage["missing"][:12]) or "-",
                ",".join(coverage["short_h1"][:8]) or "-",
            )
            if getattr(cfg, "MARKET_REQUIRE_COMPLETE", True):
                log.error("SCAN_SKIP_TRADE reason=incomplete_basket")
                save_state(state)
                return
        strength = currency_strength(h1_series(market), cfg.STRENGTH_LOOKBACK)
        rank = rank_currencies(strength)

        h1 = h1_series(market)
        closed_dt = last_closed_h1_dt(h1)
        empty = bool(rank) and (max(s for _, s in rank) - min(s for _, s in rank) < 1e-12)
        session_only = bool(getattr(cfg, "BRIEFING_SESSION_ONLY", True))
        session_due = briefing.just_opened(
            window_min=int(getattr(cfg, "BRIEFING_OPEN_WINDOW_MIN", 15))
        ) or bool(getattr(cfg, "BRIEFING_SESSION_CATCH_UP", True))
        if session_only:
            iid = briefing.briefing_id() if rank and not empty else ""
        else:
            iid = briefing.issue_id(closed_dt, chat_id) if closed_dt else ""
        log.info(
            "briefing_key=%s pid=%s reason=scan_job h1=%s session_only=%s due=%s empty=%s",
            iid,
            briefing.instance_id(),
            closed_dt,
            session_only,
            session_due,
            empty,
        )
        briefing_ok = bool(iid and not empty and rank)
        if session_only:
            briefing_ok = briefing_ok and session_due
        else:
            briefing_ok = briefing_ok and closed_dt and briefing.h1_is_current(
                closed_dt, cfg.BRIEFING_OPEN_WINDOW_MIN
            )
        if briefing_ok:
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

        module_alerts: list[tuple[int, str]] = []
        scan_stats = ScanStats()
        enabled_flags = (
            "DISBALANCE_ENABLED", "IMBALANCE_ENABLED", "CONSOLIDATION_ZONE_ENABLED",
            "ACCUMULATION_DISTRIBUTION_ENABLED", "AMD_POWER_OF_THREE_ENABLED",
            "CRT_CANDLE_RANGE_ENABLED", "LIQUIDITY_SWEEP_ENABLED", "POC_ENABLED",
            "ORDER_BLOCK_ENABLED", "BREAKER_BLOCK_ENABLED", "SMART_MONEY_62_26_ENABLED",
            "SILVER_BULLET_ENABLED", "DAILY_HIGH_LOW_ENABLED", "CHAIN_ENTRIES_ENABLED",
            "RETEST_CONFIRMATION_ENABLED", "FIBONACCI_ENABLED", "ATS_REVERSAL_ENABLED",
            "FIB_SMC_ENABLED", "LEVELS_ENABLED", "ZIGZAG_SCANNER_ENABLED", "PATTERNS_ENABLED",
        )
        for flag in enabled_flags:
            if getattr(cfg, flag, True):
                scan_stats.note_run(flag)
        # Полностью подтверждённый AMD — редкое завершённое событие. Оно имеет
        # собственный обязательный канал доставки и не расходует три места
        # часового рейтинга обычных кандидатов.
        mandatory_amd_alerts: list[str] = []

        if getattr(cfg, "DISBALANCE_ENABLED", True):
            try:
                for text in disbalance.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                scan_stats.note_fail("disbalance"); log.exception("Ошибка модуля дисбаланса")

        if getattr(cfg, "IMBALANCE_ENABLED", True):
            try:
                for text in imbalance.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                scan_stats.note_fail("imbalance"); log.exception("Ошибка модуля Imbalance/FVG")

        if getattr(cfg, "CONSOLIDATION_ZONE_ENABLED", True):
            try:
                for text in consolidation_zone.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                scan_stats.note_fail("consolidation_zone"); log.exception("Ошибка модуля Consolidation Zone")

        if getattr(cfg, "ACCUMULATION_DISTRIBUTION_ENABLED", True):
            try:
                for text in accumulation_distribution.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                scan_stats.note_fail("accumulation_distribution"); log.exception("Ошибка модуля накопления/распределения")

        if getattr(cfg, "AMD_POWER_OF_THREE_ENABLED", True):
            try:
                for text in amd_power_of_three.process_market(market, strength):
                    mandatory_amd_alerts.append(text)
            except Exception:
                scan_stats.note_fail("amd"); log.exception("Ошибка модуля AMD / Power of Three")

        if getattr(cfg, "CRT_CANDLE_RANGE_ENABLED", True):
            try:
                for text in crt_candle_range.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                scan_stats.note_fail("crt"); log.exception("Ошибка модуля CRT / Candle Range Theory")

        if getattr(cfg, "LIQUIDITY_SWEEP_ENABLED", True):
            try:
                for text in liquidity_sweep.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                scan_stats.note_fail("liquidity_sweep"); log.exception("Ошибка модуля снятия ликвидности")

        if getattr(cfg, "POC_ENABLED", True):
            try:
                for text in poc_profile.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                scan_stats.note_fail("poc"); log.exception("Ошибка модуля POC")

        invalidated_order_blocks = []
        if getattr(cfg, "ORDER_BLOCK_ENABLED", True):
            try:
                for text in order_block.process_market(market, strength):
                    module_alerts.append((1, text))
                invalidated_order_blocks = order_block.pop_invalidated_blocks()
            except Exception:
                scan_stats.note_fail("order_block"); log.exception("Ошибка модуля Order Block")

        if getattr(cfg, "BREAKER_BLOCK_ENABLED", True):
            try:
                for text in breaker_block.process_market(market, strength, invalidated_order_blocks):
                    module_alerts.append((1, text))
            except Exception:
                scan_stats.note_fail("breaker_block"); log.exception("Ошибка модуля Breaker Block")

        if getattr(cfg, "SMART_MONEY_62_26_ENABLED", True):
            try:
                for text in smart_money_62_26.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                scan_stats.note_fail("smart_money"); log.exception("Ошибка модуля Smart Money 62-26")

        if getattr(cfg, "SILVER_BULLET_ENABLED", True):
            try:
                try:
                    silver_events = newsmod.load_events()
                except Exception:
                    log.exception("Новости для ICT Silver Bullet")
                    silver_events = []
                for text in silver_bullet_ict.process_market(
                    market, strength, silver_events, datetime.now(timezone.utc)
                ):
                    module_alerts.append((0, text))
            except Exception:
                scan_stats.note_fail("silver_bullet"); log.exception("Ошибка модуля ICT Silver Bullet")

        if getattr(cfg, "DAILY_HIGH_LOW_ENABLED", True):
            try:
                for text in daily_high_low.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                scan_stats.note_fail("daily_high_low"); log.exception("Ошибка модуля дневного максимума/минимума")

        if getattr(cfg, "CHAIN_ENTRIES_ENABLED", True):
            try:
                for text in chain_entries.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                scan_stats.note_fail("chain_entries"); log.exception("Ошибка модуля Chain Entries")

        if getattr(cfg, "RETEST_CONFIRMATION_ENABLED", True):
            try:
                for text in retest_confirmation.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                scan_stats.note_fail("retest"); log.exception("Ошибка модуля структурного ретеста")

        if getattr(cfg, "FIBONACCI_ENABLED", True):
            try:
                for text in fibonacci_grid.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                scan_stats.note_fail("fibonacci"); log.exception("Ошибка модуля сетки Фибоначчи")

        if getattr(cfg, "ATS_REVERSAL_ENABLED", True):
            try:
                for text in ats_reversal_point.process_market(market, strength):
                    module_alerts.append((0, text))
            except Exception:
                scan_stats.note_fail("ats"); log.exception("Ошибка модуля ATS Reversal Point")

        if getattr(cfg, "FIB_SMC_ENABLED", True):
            try:
                try:
                    fib_smc_events = newsmod.load_events()
                except Exception:
                    log.exception("Новости для Fib+SMC")
                    fib_smc_events = []
                for text in fib_smc.process_market(
                    market, strength, fib_smc_events, datetime.now(timezone.utc)
                ):
                    module_alerts.append((0, text))
            except Exception:
                scan_stats.note_fail("fib_smc"); log.exception("Ошибка модуля Fib+SMC")

        mandatory_level_breakouts: list[str] = []
        if getattr(cfg, "LEVELS_ENABLED", True):
            try:
                try:
                    level_events = newsmod.load_events()
                except Exception:
                    log.exception("Новости для Levels")
                    level_events = []
                for text in levels.process_market(market, strength, level_events):
                    if (getattr(cfg, "LEVEL_BREAKOUT_MANDATORY_DELIVERY", True)
                            and "⚡ ПРОБОЙ УРОВНЯ" in text):
                        mandatory_level_breakouts.append(text)
                    else:
                        module_alerts.append((0, text))
            except Exception:
                scan_stats.note_fail("levels"); log.exception("Ошибка модуля уровней")

        if getattr(cfg, "ZIGZAG_SCANNER_ENABLED", True):
            try:
                for text in zigzag_scanner.process_market(market, strength):
                    module_alerts.append((2, text))
            except Exception:
                scan_stats.note_fail("zigzag"); log.exception("Ошибка отдельного ZigZag-сканера")

        if patterns is not None and getattr(cfg, "PATTERNS_ENABLED", True):
            try:
                for text in patterns.process_market(market, strength):
                    structural = any(name in text for name in (
                        "BOS", "Двойная", "голова и плечи", "AB=CD",
                        "треугольник", "клин", "флаг", "вымпел", "прямоугольник",
                    ))
                    module_alerts.append((1 if structural else 3, text))
            except Exception:
                scan_stats.note_fail("patterns"); log.exception("Ошибка сканера паттернов")

        # Все торговые события сначала становятся внутренними кандидатами.
        # Наружу исходная карточка может выйти только после строгого подтверждения
        # Master Direction / Navigator для той же пары и направления.
        source_alerts = list(module_alerts) + [(0, text) for text in mandatory_amd_alerts + mandatory_level_breakouts]
        raw_alerts = [text for _priority, text in source_alerts]
        # События, не подтверждённые в эту H1, не теряются: они остаются
        # внутренними кандидатами ограниченное число часов.
        candidate_alerts = signal_navigator.remember_candidates(raw_alerts, closed_dt)
        navigator_sources: dict[str, list[str]] = {}
        confirmed_alerts: list[tuple[int, str]] = []
        if getattr(cfg, "MASTER_DIRECTION_ENABLED", True):
            try:
                master_dxy = briefing.collect_extras(
                    env("TWELVE_DATA_API_KEY"), h1_dt=closed_dt, market=market
                )
                master_dxy_bias = briefing.effective_dxy_bias(master_dxy)
            except Exception:
                log.exception("DXY для Master Direction")
                master_dxy_bias = 0
            try:
                master_events = newsmod.load_events()
            except Exception:
                log.exception("Новости для Master Direction")
                master_events = []
            try:
                master_results = master_direction.analyze_market(
                    market,
                    strength,
                    candidate_alerts,
                    dxy_bias=master_dxy_bias,
                    events=master_events,
                    now_utc=datetime.now(timezone.utc),
                )
            except Exception:
                log.exception("NAVIGATOR_CONTEXT_SKIPPED stage=master_direction")
                master_results = []
            # Локальный AMD-маршрут намеренно не добавляется как обход строгого
            # Master Direction. AMD, как и Levels и остальные модули, обязан
            # пройти единый финальный контроль перед Telegram-доставкой.
            try:
                built_navigator = signal_navigator.build_confirmed(
                    master_results, market, strength, candidate_alerts
                )
            except Exception:
                log.exception("NAVIGATOR_CONTEXT_SKIPPED stage=build_confirmed")
                built_navigator = []
            for text, sources in built_navigator:
                pair = _alert_pair(text)
                side = _direct_signal_side(text)
                if pair and side and cooldown_ok(state, pair, side):
                    confirmed_alerts.append((-1, text))
                    navigator_sources[text] = sources
                    signal_navigator.register_card(text, sources, closed_dt)

        # Часовой лимит считает исходные торговые события. Навигатор является
        # обязательным сопровождением выбранного события и этот лимит не тратит.
        # AMD уже будет отправлен обязательным потоком ниже, поэтому повторно
        # не участвует в ранжировании и не может быть вытеснен/задублирован.

        # Верхний предел новых карточек за H1. 0 = без лимита.
        # AMD и обязательный пробой уровня идут отдельно и лимит не едят.
        hourly_cap = int(getattr(cfg, "MAX_MODULE_ALERTS_PER_H1", 7) or 0)
        if hourly_cap > 0:
            selected_alerts = [(0, text) for text in select_trade_alerts(module_alerts, limit=hourly_cap)]
        else:
            selected_alerts = list(module_alerts)
        if scan_stats.abort_delivery():
            selected_alerts = []
            mandatory_amd_alerts = []
            mandatory_level_breakouts = []
            log.error("SCAN_TRADE_CARDS_SUPPRESSED reason=too_many_scanner_failures")
        # Повторяем карточки, чья предыдущая отправка временно не удалась.
        # Они не расходуют лимит новых исходных сигналов.
        await _flush_source_outbox(context.application, int(chat_id), state)
        await _flush_navigator_outbox(context.application, int(chat_id), state)
        if getattr(cfg, "SIGNAL_JOURNAL_ENABLED", True):
            try:
                signal_journal.update_market(market)
            except Exception:
                log.exception("Обновление журнала сигналов")
        # ZIP 27: confirmed source events no longer wait for Master/Navigator.
        # Master/Navigator may follow as a stricter companion, but cannot suppress source.
        proposed_delivery = mandatory_amd_alerts + mandatory_level_breakouts + [
            text for _priority, text in selected_alerts
        ]
        delivery_alerts = list(dict.fromkeys(proposed_delivery))
        # Global significance gate: detectors/candidates keep every event internally,
        # but a fresh Telegram trade card needs both enough H1 life and enough
        # expected price amplitude. This prevents 1–2 H1 noise and five tiny
        # sideways candles from being presented as an actionable new signal.
        if getattr(cfg, "SIGNAL_SIGNIFICANCE_GATE_ENABLED", True):
            significant_alerts = []
            for source_text in delivery_alerts:
                if not (_alert_pair(source_text) and _direct_signal_side(source_text)):
                    significant_alerts.append(source_text)
                    continue
                # Pattern Scanner owns its structural residual-potential/late-entry gate.
                # Once the FIRST confirming close is valid, deliver it immediately; the
                # generic Navigator horizon gate must not turn that event into a delayed card.
                if "🧩 ПАТТЕРН ПОДТВЕРЖДЁН" in source_text:
                    significant_alerts.append(source_text)
                    continue
                try:
                    significance = signal_navigator.assess_new_signal_significance(
                        source_text, market, strength
                    )
                except Exception:
                    log.exception("SIGNAL_SIGNIFICANCE_GATE_FAILED")
                    significance = {"eligible": True, "reason": "gate_error"}
                if significance.get("eligible", True):
                    significant_alerts.append(source_text)
                else:
                    log.info(
                        "SIGNAL_INTERNAL_ONLY pair=%s side=%s reason=%s h1=%s route_atr=%s body_atr=%s eff=%s",
                        _alert_pair(source_text), _direct_signal_side(source_text),
                        significance.get("reason"), significance.get("remaining_h1"),
                        significance.get("route_atr"), significance.get("median_body_atr"),
                        significance.get("efficiency"),
                    )
            delivery_alerts = significant_alerts
        try:
            bundles, dropped_facts = signal_context.prepare(delivery_alerts, market, strength)
        except Exception:
            log.exception("SIGNAL_CONTEXT_FAILED")
            bundles = [{"primary": t, "source_text": t, "allies": [], "pair": _alert_pair(t), "side": _direct_signal_side(t)} for t in delivery_alerts]
            dropped_facts = []
        for reason, raw in dropped_facts:
            log.info(
                "SIGNAL_CONTEXT_INTERNAL pair=%s side=%s reason=%s source=%s",
                _alert_pair(raw), _direct_signal_side(raw), reason, signal_context.source_name(raw),
            )
        # CPI — единый защитный слой поверх всех модулей: сам факт модуля не теряется,
        # но карточка явно запрещает трактовать новостной импульс как готовый вход.
        try:
            cpi_events = newsmod.load_events()
        except Exception:
            log.exception("CPI guard calendar")
            cpi_events = []
        for bundle in bundles:
            source_text = bundle.get("source_text") or bundle["primary"]
            allied_texts = [source_text] + list(bundle.get("allies") or [])
            if any(_event_already_delivered(state, _source_event_id(item)) for item in allied_texts):
                log.warning("DUPLICATE_EVENT_BLOCKED pair=%s side=%s", bundle.get("pair"), bundle.get("side"))
                if "📦 ВЫХОД ИЗ ЗОНЫ КОНСОЛИДАЦИИ" in source_text:
                    try: consolidation_zone.mark_delivered(source_text)
                    except Exception: log.exception("Consolidation duplicate ack")
                continue
            pair = bundle.get("pair") or _alert_pair(source_text)
            direct_side = bundle.get("side") or _direct_signal_side(source_text)
            if pair and direct_side and not cooldown_ok(state, pair, direct_side):
                log.info("SIGNAL_COOLDOWN_SKIP pair=%s side=%s", pair, direct_side)
                continue
            source_event_id = _source_event_id(source_text)
            _enqueue_source(state, source_text)
            save_state(state)
            text = bundle.get("primary") or source_text
            pair_for_cpi = pair or _alert_pair(text)
            cpi_note = newsmod.cpi_pair_guard(pair_for_cpi, cpi_events) if pair_for_cpi else ""
            if cpi_note and cpi_note not in text:
                text = text.rstrip() + "\n\n" + cpi_note
            source_image = _source_image_for(text, source_text)
            await _deliver_trade_card(context.application, int(chat_id), text, source_image)
            if patterns is not None and "🧩 ПАТТЕРН ПОДТВЕРЖДЁН" in text:
                patterns.mark_card_delivered(text)
            _mark_event_delivered(state, source_event_id)
            for extra in bundle.get("allies") or []:
                _mark_event_delivered(state, _source_event_id(extra))
            _ack_source(state, source_text)
            if pair and direct_side:
                state.setdefault("last_signals", {})[f"{pair}:{direct_side}"] = time.time()
            save_state(state)
            delivered_sources = list(allied_texts)
            for source_text in delivered_sources:
                if "↕️ ZIGZAG —" in source_text:
                    try:
                        zigzag_scanner.mark_delivered(source_text)
                    except Exception:
                        log.exception("Фиксация доставленного ZigZag")
                if "⚖️ ДИСБАЛАНС ПОДТВЕРЖДЁН" in source_text:
                    try:
                        disbalance.mark_delivered(source_text)
                    except Exception:
                        log.exception("Фиксация доставленного Disbalance")
                if "IMBALANCE —" in source_text:
                    try:
                        imbalance.mark_delivered(source_text)
                    except Exception:
                        log.exception("Фиксация доставленного Imbalance/FVG")
                if getattr(cfg, "AMD_POWER_OF_THREE_ENABLED", True):
                    try:
                        amd_power_of_three.mark_delivered(source_text)
                    except Exception:
                        log.exception("Фиксация доставленного AMD")
                if "🕯 CRT — CANDLE RANGE THEORY" in source_text:
                    try:
                        crt_candle_range.mark_delivered(source_text)
                    except Exception:
                        log.exception("Фиксация доставленного CRT")
                if "📐 РЕАКЦИЯ ОТ СЕТКИ ФИБОНАЧЧИ" in source_text:
                    try:
                        fibonacci_grid.mark_delivered(source_text)
                    except Exception:
                        log.exception("Фиксация доставленного Fibonacci")
                if "🧬 FIB + SMC —" in source_text:
                    try:
                        fib_smc.mark_delivered(source_text)
                    except Exception:
                        log.exception("Фиксация доставленного Fib+SMC")
                if "⛓️ CHAIN ENTRY" in source_text:
                    try:
                        chain_entries.mark_delivered(source_text)
                    except Exception:
                        log.exception("Фиксация доставленного Chain Entry")
                if "📦 ВЫХОД ИЗ ЗОНЫ КОНСОЛИДАЦИИ" in source_text:
                    try:
                        consolidation_zone.mark_delivered(source_text)
                    except Exception:
                        log.exception("Фиксация доставленного Consolidation Zone")
                if "🚀 ВЫХОД ИЗ ФАЗЫ" in source_text or "📦 ФАЗА " in source_text:
                    try:
                        accumulation_distribution.mark_delivered(source_text)
                    except Exception:
                        log.exception("Фиксация доставленной фазы накопления/распределения")
            if getattr(cfg, "SIGNAL_JOURNAL_ENABLED", True):
                try:
                    signal_journal.record_sent(text, market, closed_dt)
                except Exception:
                    log.exception("Запись отправленного сигнала в журнал")
            pair = _alert_pair(text)
            direct_side = _direct_signal_side(text)
            # ZIP 28: Navigator встречает КАЖДОЕ пригодное направленное событие.
            # Строгая карточка Master Direction предпочтительна, но отсутствие Master
            # больше не заставляет Navigator исчезать: исходный модуль уже подтвердил
            # сам факт события, а Navigator отдельно показывает HTF/LTF-конфликты,
            # силу и ZigZag и сопровождает TR1/TR2/TR3.
            key = (pair, direct_side)
            companion = next((
                (card, navigator_sources[card]) for _p, card in confirmed_alerts
                if (_alert_pair(card), _direct_signal_side(card)) == key
            ), None)
            if not companion and pair and direct_side:
                try:
                    companion = signal_navigator.build_source_companion(source_text, market, strength)
                except Exception:
                    log.exception("NAVIGATOR_CONTEXT_SKIPPED stage=source_companion pair=%s", pair)
                    companion = None
            if companion:
                nav_text, nav_sources = companion
                # Регистрируем маршрут ДО Telegram: lifecycle не зависит от сетевой
                # отправки карточки и сможет сообщить TR1/TR2/TR3/отмену позже.
                signal_navigator.register_card(nav_text, nav_sources, closed_dt)
                _enqueue_navigator(state, nav_text, source_event_id)
                save_state(state)
                await _flush_navigator_outbox(context.application, int(chat_id), state)
                if pair and direct_side:
                    state.setdefault("last_signals", {})[f"{pair}:{direct_side}"] = time.time()

        # Активные сценарии сопровождаются отдельно от лимита новых сигналов:
        # только близость к цели, завершение либо подтверждённая отмена.
        try:
            for text in signal_navigator.process_lifecycle(market, strength):
                await _send_parts(context.application, int(chat_id), text)
                signal_navigator.mark_lifecycle_delivered(text)
        except Exception:
            log.exception("Сопровождение активного сценария Навигатора")

        # Полные прогнозы от текущей сессии до следующей: семь пар Эхо и
        # семь пар Next Pivot. Это отдельный информационный поток, который не
        # расходует лимит торговых кандидатов и не проходит через Навигатор.
        session_projection_due = (
            briefing.just_opened(
                window_min=int(getattr(cfg, "SESSION_PROJECTIONS_OPEN_WINDOW_MIN", 20))
            )
            or bool(getattr(cfg, "SESSION_PROJECTIONS_CATCH_UP", False))
        )
        if getattr(cfg, "SESSION_PROJECTIONS_ENABLED", True) and session_projection_due:
            try:
                # Только начало новой сессии: расчёт использует последнюю
                # закрытую H1 и не догоняется произвольно через несколько часов.
                session_events = briefing.session_events(newsmod.load_events())
                for alert in session_projection_reports.pending_reports(
                        market, session_events, state):
                    try:
                        caption = alert["text"]
                        long_caption = len(caption) > 1000
                        if long_caption:
                            # У Telegram подпись к фото короче обычного сообщения.
                            # Полный русский разбор отправляем сразу следом, не
                            # обрезая список относящихся к паре новостей.
                            module_name = "ЭХО" if "|echo|" in alert["key"] else "СЛЕДУЮЩИЙ PIVOT"
                            pair = alert["key"].rsplit("|", 1)[-1]
                            caption = f"🔭 {module_name} · {pair}\nПолный сессионный разбор — следующим сообщением."
                        message = await context.application.bot.send_photo(
                            chat_id=int(chat_id), photo=alert["image"], caption=caption)
                        if long_caption:
                            await _send_parts(context.application, int(chat_id), alert["text"])
                        session_projection_reports.mark_delivered(state, alert["key"])
                        save_state(state)
                        log.info("SESSION_REPORT_SENT message_id=%s key=%s pid=%s",
                                 getattr(message, "message_id", None), alert["key"],
                                 briefing.instance_id())
                    except Exception:
                        # Ошибка одной отправки не перекрывает остальные пары.
                        # Ключ не фиксируется: следующий скан повторит её.
                        log.exception("SESSION_REPORT_SEND_FAILED key=%s pid=%s",
                                      alert.get("key", "unknown"), briefing.instance_id())
            except Exception:
                log.exception("SESSION_REPORT_SCAN_FAILED")

        if getattr(cfg, "SIGNAL_JOURNAL_ENABLED", True):
            try:
                for report_id, report_text in signal_journal.pending_reports(datetime.now(timezone.utc)):
                    await _send_parts(context.application, int(chat_id), report_text)
                    signal_journal.mark_report_sent(report_id)
            except Exception:
                log.exception("Отправка отчёта журнала")

        save_state(state)
        log.info(
            "Скан %s OK top=%s scanners_failed=%s/%s",
            datetime.now(timezone.utc).strftime("%H:%M"),
            rank[0][0] if rank else "-",
            scan_stats.failed,
            scan_stats.ran,
        )
    except Exception:
        log.exception("Ошибка скана")


def main() -> None:
    token = env("TELEGRAM_TOKEN")
    env("TWELVE_DATA_API_KEY")
    state_dir = os.getenv("STATE_DIR", "").strip()
    if state_dir:
        dest = Path(state_dir)
        dest.mkdir(parents=True, exist_ok=True)
        log.info("STATE_DIR=%s persist=%s", dest, dest / "state.json")
    else:
        log.warning(
            "STATE_DIR не задан: state.json рядом с кодом пропадёт после рестарта контейнера"
        )
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
