#!/usr/bin/env python3
"""Личный FX-бот. Мультитаймфрейм W1→D1→H4→H1→M15→M5."""
from __future__ import annotations

import json
import math
import hashlib
import asyncio
import gc
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from telegram import Update, InputMediaPhoto
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
import bpr
import quasimodo_engine
import killer_engine
import accumulation_distribution
import consolidation_zone
import amd_power_of_three
import weekly_rhythm_context
import crt_candle_range
import ohlc_movement
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
import edge_estimator
import signal_navigator
import signal_journal
import decision_journal
import replay_calibration
import robustness_audit
import daily_calibration_report
import signal_context
import session_projection_reports
from telegram_locale import localize_telegram
from telegram_presentation import format_trade_card
try:
    import patterns
except ImportError:
    patterns = None

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# httpx logs full request URLs at INFO level; Telegram embeds the bot token in
# that URL. Keep transport chatter out of production logs so secrets are not
# copied into Railway Log Explorer.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
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
    state = None
    if dest.exists():
        try:
            loaded = json.loads(dest.read_text())
            state = loaded if isinstance(loaded, dict) else None
        except Exception:
            log.exception("Повреждён state.json")
    if state is None:
        state = {
            "chat_id": None,
            "last_signals": {},
            "last_rank": [],
            "last_strength_ts": 0,
        }
    # Railway restart/redeploy must not leave a healthy process permanently
    # silent just because state.json was lost or an older state did not contain
    # chat_id.  TELEGRAM_CHAT_ID is an emergency durable fallback.
    if not state.get("chat_id"):
        fallback = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if fallback:
            state["chat_id"] = fallback
    return state


def save_state(state: dict) -> None:
    briefing.persist_state(state)


def _parse_values(values: list) -> list[Candle]:
    candles = []
    invalid = 0
    for v in values or []:
        try:
            o, h, l, c = (float(v[k]) for k in ("open", "high", "low", "close"))
            if (not all(math.isfinite(x) and x > 0 for x in (o, h, l, c))
                    or h < max(o, l, c) or l > min(o, h, c)):
                invalid += 1
                continue
            volume = v.get("volume")
            volume = float(volume) if volume not in (None, "", "null") else None
            if volume is not None and (not math.isfinite(volume) or volume < 0):
                volume = None  # Volume is optional; OHLC validity is mandatory.
            candles.append(Candle(dt=str(v["datetime"]), open=o, high=h, low=l, close=c, volume=volume))
        except (KeyError, TypeError, ValueError, OverflowError):
            invalid += 1
    if invalid:
        # Reject whole damaged series: removing an internal bar would hide a
        # missing H1 close and distort ATR / indicators / historical replay.
        log.warning("MARKET_DATA_INVALID removed=%s of=%s; series rejected", invalid, len(values or []))
        return []
    candles.sort(key=lambda x: x.dt)
    if len({b.dt for b in candles}) != len(candles):
        log.warning("MARKET_DATA_INVALID duplicate candle timestamps; series rejected")
        return []
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
    """Сначала пакет из 7 пар, затем точечно добрать недостающие символы.

    Twelve Data иногда возвращает валидный multi-symbol JSON только для части
    корзины. Раньше любой непустой ``out`` считался успехом, из-за чего одна
    пропавшая пара затем блокировала весь scan_job через MARKET_INCOMPLETE.
    """
    out: dict[str, list[Candle]] = {}
    try:
        data = _request_series(",".join(cfg.PAIRS), tf, api_key)
        out = _extract_pairs(data)
        missing = [symbol for symbol in cfg.PAIRS if not out.get(symbol)]
        if not missing:
            return out
        if out:
            log.warning(
                "PARTIAL_BATCH tf=%s present=%s/%s missing=%s",
                tf["key"], len(out), len(cfg.PAIRS), ",".join(missing),
            )
    except Exception as e:
        log.warning("Пакет %s не вышел: %s", tf["key"], e)

    if not getattr(cfg, "MARKET_RETRY_MISSING_SYMBOLS", True):
        return out
    missing = [symbol for symbol in cfg.PAIRS if not out.get(symbol)]
    for symbol in missing:
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


def _cache_fallback(s: str, tf: dict, now: float) -> list[Candle]:
    """Reject stale cached quotes after API failure before market coverage."""
    hit = CACHE.get((s, tf["key"])) or {}
    candles = hit.get("candles") or []
    try:
        age = now - float(hit.get("ts"))
        max_age = float(getattr(cfg, "MARKET_CACHE_FALLBACK_MAX_AGE_MINUTES", {}).get(tf["key"], 0)) * 60
    except (TypeError, ValueError, OverflowError, AttributeError):
        return []
    if not candles or not math.isfinite(age) or not math.isfinite(max_age):
        return []
    if age < 0 or max_age <= 0 or age > max_age:
        log.warning("CACHE_EXPIRED symbol=%s tf=%s age_min=%.1f limit_min=%.1f",
                    s, tf["key"], age/60, max_age/60)
        return []
    return candles


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
            for s in cfg.PAIRS:
                candles = batch.get(s) or []
                if candles:
                    CACHE[(s, tf["key"])] = {"ts": ts, "candles": candles}
                    market[s][tf["key"]] = candles
                    continue
                # The targeted retry may still fail transiently. Preserve a
                # previously fetched series for this tick instead of dropping
                # the symbol from an otherwise usable basket. Exact freshness
                # guards inside signal modules still decide tradeability.
                cached = _cache_fallback(s, tf, time.time())
                if cached:
                    market[s][tf["key"]] = cached
                    log.warning("CACHE_FALLBACK symbol=%s tf=%s", s, tf["key"])
            time.sleep(cfg.REQUEST_PAUSE_SEC)
        except Exception as e:
            log.warning("Пакет %s: %s", tf["key"], e)
            for s in cfg.PAIRS:
                cached = _cache_fallback(s, tf, time.time())
                if cached:
                    market[s][tf["key"]] = cached
    return market


def h1_series(market: dict[str, dict[str, list[Candle]]]) -> dict[str, list[Candle]]:
    return {s: tfs.get("H1") or [] for s, tfs in market.items()}


def bars(score: float) -> str:
    n = max(0, min(10, int(round((score + 1.5) / 3 * 10))))
    return "█" * n + "░" * (10 - n)


def closed_bar_utc(dt_str: str | None) -> datetime | None:
    """Время последней закрытой H1 как UTC. Не часы хоста."""
    raw = str(dt_str or "")[:19]
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(str(dt_str).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)


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


def cooldown_ok(state: dict, symbol: str, side: str, source: str = "") -> bool:
    """Anti-spam cooldown scoped to a setup family, not the whole pair+side.

    Exact repeats are handled by durable event IDs.  This guard only prevents a
    scanner family from re-presenting essentially the same idea too quickly, while
    allowing a genuinely independent setup on the same pair/direction.
    """
    family = (source or "GENERAL").strip().upper()
    key = f"{symbol}:{side}:{family}"
    last = state.get("last_setups", {}).get(key)
    if not last:
        return True
    hours = float(getattr(cfg, "SIGNAL_SETUP_COOLDOWN_HOURS", 1.5))
    return (time.time() - last) / 3600 >= hours


async def send(app: Application, chat_id: int, text: str):
    msg = await app.bot.send_message(chat_id=chat_id, text=text)
    mid = getattr(msg, "message_id", None)
    log.info("telegram_message_id=%s pid=%s", mid, briefing.instance_id())
    return mid


def _remember_effective_chat(update: Update) -> dict:
    """Any successful manual command heals a lost persistent chat_id."""
    state = load_state()
    chat = getattr(update, "effective_chat", None)
    chat_id = getattr(chat, "id", None)
    if chat_id and str(state.get("chat_id") or "") != str(chat_id):
        state["chat_id"] = chat_id
        save_state(state)
        log.info("CHAT_ID_RECOVERED source=manual_command chat_id=%s", chat_id)
    return state


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    state["chat_id"] = update.effective_chat.id
    save_state(state)
    await update.message.reply_text(
        "Готово. Я запомнил тебя.\n"
        "Смотрю W1, D1, H4, H1, M15, M5.\n"
        "Пишу подтверждённые события по закрытым свечам; сила валют учитывается как контекст, "
        "включая её устойчивую динамику во времени.\n"
        "Полный брифинг — на открытии сессии (Азия / Европа / Америка), не каждый час. "
        "Новости high-impact идут отдельным потоком.\n\n"
        "/now — сила валют сейчас\n"
        "/pair EUR/USD — стек таймфреймов по паре\n"
        "/briefing — брифинг текущей сессии\n"
        "/status — жив ли я"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _remember_effective_chat(update)
    lines = ["Статус worker: Telegram polling отвечает."]
    try:
        market = fetch_market(env("TWELVE_DATA_API_KEY"))
        coverage = market_coverage(market)
        h1 = h1_series(market)
        latest = last_closed_h1_dt(h1) or "—"
        lines.append(
            "Корзина Twelve Data: "
            + ("полная" if coverage["complete"] else f"НЕПОЛНАЯ {coverage['present']}/{coverage['expected']}")
        )
        if coverage["missing"]:
            lines.append("Нет: " + ", ".join(coverage["missing"][:8]))
        if coverage["short_h1"]:
            lines.append("Мало H1: " + ", ".join(coverage["short_h1"][:8]))
        lines.append(f"Последняя закрытая H1: {latest} UTC")
    except Exception as exc:
        log.exception("STATUS market health")
        lines.append(f"Twelve Data: ошибка {type(exc).__name__}")
    lines.append(f"Последний брифинг: {state.get('last_briefing_id') or '—'}")
    lines.append(f"STATE_DIR: {os.getenv('STATE_DIR', '').strip() or 'НЕ ЗАДАН'}")
    await update.message.reply_text("\n".join(lines))


async def cmd_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _remember_effective_chat(update)
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
    _remember_effective_chat(update)
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
    await update.message.reply_text(localize_telegram(format_pair_now(stack)))


async def cmd_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _remember_effective_chat(update)
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
        events = []; upcoming_events = []
        try:
            all_events = newsmod.load_events()
            events = briefing.session_events(all_events)
            upcoming_events = briefing.events_until_next_briefing(all_events)
        except Exception:
            log.exception("Новости для /briefing")
        try:
            text = briefing.build_briefing_text(market, strength, rank, dxy, events, upcoming_events)
        except Exception:
            log.exception("Сборка текста /briefing")
            text = format_strength(rank, last_closed_h1_dt(h1_series(market)))
            text += "\n\nПолная доска пар сейчас недоступна."
        await _send_briefing_sequence(
            context.application, int(update.effective_chat.id), market, strength, rank, dxy, events, upcoming_events
        )
    except Exception:
        log.exception("Ошибка /briefing")
        await update.message.reply_text(
            "Брифинг не собрался. /now работает отдельно. Повтори команду через минуту."
        )


async def _send_parts(app: Application, chat_id: int, text: str) -> None:
    # Переводим только готовый пользовательский вывод: внутренняя логика и парсеры
    # продолжают работать с исходными LONG/SHORT/enum.
    text = localize_telegram(text)
    for part in briefing.split_telegram(text):
        await send(app, chat_id, part)


async def _deliver_scheduled_reports(app: Application, chat_id: int) -> None:
    """Reports are operationally independent from trade-basket completeness."""
    if getattr(cfg, "SIGNAL_JOURNAL_ENABLED", True) and getattr(
        cfg, "SIGNAL_JOURNAL_REPORTS_ENABLED", False
    ):
        try:
            for report_id, report_text in signal_journal.pending_reports(datetime.now(timezone.utc)):
                await _send_parts(app, int(chat_id), report_text)
                signal_journal.mark_report_sent(report_id)
        except Exception:
            log.exception("Отправка отчёта журнала")

    if getattr(cfg, "DAILY_CALIBRATION_REPORT_ENABLED", True):
        try:
            for report_id, report_text in daily_calibration_report.pending_reports(datetime.now(timezone.utc)):
                await _send_parts(app, int(chat_id), report_text)
                daily_calibration_report.mark_report_sent(report_id)
        except Exception:
            log.exception("Отправка Daily Calibration Report")


def _coverage_signature(coverage: dict) -> str:
    missing = ",".join(sorted(coverage.get("missing") or []))
    short_h1 = ",".join(sorted(coverage.get("short_h1") or []))
    return f"missing={missing}|short_h1={short_h1}"


async def _publish_market_health(app: Application, chat_id: int, state: dict, coverage: dict) -> None:
    """Expose a persistent data outage instead of silently returning forever."""
    if not getattr(cfg, "SYSTEM_HEALTH_ALERTS_ENABLED", True):
        return
    now_ts = time.time()
    health = state.setdefault("runtime_health", {})
    was_degraded = health.get("market_status") == "degraded"
    if coverage.get("complete"):
        if was_degraded:
            try:
                await _send_parts(
                    app,
                    int(chat_id),
                    "✅ ТЕХНИЧЕСКОЕ ВОССТАНОВЛЕНИЕ\n"
                    "Полная корзина Twelve Data снова доступна. Автоматические брифинги и торговые сканы возобновлены.",
                )
            except Exception:
                log.exception("SYSTEM_HEALTH_RECOVERY_SEND_FAILED")
        health.update({"market_status": "ok", "market_signature": "", "market_notice_ts": now_ts})
        return

    signature = _coverage_signature(coverage)
    repeat_sec = max(60, int(getattr(cfg, "SYSTEM_HEALTH_ALERT_REPEAT_MINUTES", 180)) * 60)
    last_notice = float(health.get("market_notice_ts") or 0)
    changed = signature != health.get("market_signature") or not was_degraded
    if changed or now_ts - last_notice >= repeat_sec:
        missing = ", ".join((coverage.get("missing") or [])[:10]) or "—"
        short_h1 = ", ".join((coverage.get("short_h1") or [])[:8]) or "—"
        try:
            await _send_parts(
                app,
                int(chat_id),
                "⚠️ ТЕХНИЧЕСКИЙ СТАТУС БОТА\n"
                "Worker отвечает, но полная рыночная корзина не собрана.\n"
                f"Данные: {coverage.get('present', 0)}/{coverage.get('expected', 0)} TF-серий.\n"
                f"Отсутствуют: {missing}\n"
                f"Недостаточно H1: {short_h1}\n"
                "Торговые сигналы временно fail-safe заблокированы; журнальные отчёты продолжают отправляться.",
            )
        except Exception:
            log.exception("SYSTEM_HEALTH_DEGRADED_SEND_FAILED")
        health["market_notice_ts"] = now_ts
    health.update({"market_status": "degraded", "market_signature": signature})


async def _maybe_send_session_briefing(
    context: ContextTypes.DEFAULT_TYPE,
    state: dict,
    market: dict,
    strength: dict | None = None,
    rank: list | None = None,
) -> bool:
    """One deduplicated session briefing, callable from both scheduler jobs."""
    if not getattr(cfg, "BRIEFING_ENABLED", True):
        return False
    chat_id = state.get("chat_id")
    if not chat_id:
        return False
    strength = strength if strength is not None else currency_strength(h1_series(market), cfg.STRENGTH_LOOKBACK)
    rank = rank if rank is not None else rank_currencies(strength)
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
        "briefing_key=%s pid=%s reason=session_helper h1=%s session_only=%s due=%s empty=%s",
        iid, briefing.instance_id(), closed_dt, session_only, session_due, empty,
    )
    briefing_ok = bool(iid and not empty and rank)
    if session_only:
        briefing_ok = briefing_ok and session_due
    else:
        briefing_ok = briefing_ok and closed_dt and briefing.h1_is_current(
            closed_dt, cfg.BRIEFING_OPEN_WINDOW_MIN
        )
    if not briefing_ok:
        return False
    if briefing.issue_sent(state, iid):
        log.info("DUPLICATE_SKIPPED briefing_key=%s pid=%s reason=already_sent", iid, briefing.instance_id())
        return False
    if not briefing.claim_issue(state, iid):
        log.info("DUPLICATE_SKIPPED briefing_key=%s pid=%s reason=claim_failed", iid, briefing.instance_id())
        save_state(state)
        return False
    save_state(state)
    sent_ok = False
    try:
        api_key = env("TWELVE_DATA_API_KEY")
        try:
            dxy = briefing.collect_extras(api_key, h1_dt=closed_dt, market=market)
        except Exception:
            log.exception("DXY сессионного брифинга")
            dxy = None
        try:
            all_events = newsmod.load_events()
            events = briefing.session_events(all_events)
            upcoming_events = briefing.events_until_next_briefing(all_events)
        except Exception:
            log.exception("новости сессионного брифинга")
            events = []
            upcoming_events = []
        delivered = await _send_briefing_sequence(
            context.application, int(chat_id), market, strength, rank, dxy, events, upcoming_events
        )
        briefing.mark_issue_sent(state, iid, delivered)
        state["last_rank"] = [c for c, _ in rank]
        state["last_strength_h1"] = closed_dt
        state["last_strength_ts"] = time.time()
        state["last_briefing_id"] = briefing.briefing_id()
        state["last_briefing_sent_ts"] = time.time()
        if closed_dt:
            SENT_H1.add(closed_dt)
        sent_ok = True
        log.info("брифинг отправлен issue=%s parts=%s", iid, delivered)
    except Exception:
        log.exception("отправка сессионного брифинга")
        briefing.release_issue(state, iid)
    if not sent_ok:
        briefing.release_issue(state, iid)
    save_state(state)
    return sent_ok


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
        if "⚖️ BPR — BALANCED PRICE RANGE" in text:
            return bpr.image_for_alert(source_text)
        if "🔄 QUASIMODO — QM" in text:
            return quasimodo_engine.image_for_alert(source_text)
        if "🏹🎯 KILLER" in source_text:
            return killer_engine.image_for_alert(source_text)
        if "IMBALANCE —" in text:
            return imbalance.image_for_alert(source_text)
        if "↕️ ZIGZAG —" in text:
            return zigzag_scanner.image_for_alert(source_text)
        if patterns is not None and "🧩 ПАТТЕРН ПОДТВЕРЖДЁН" in text:
            return patterns.image_for_alert(source_text)
        if "🎯 AMD / POWER OF THREE" in text:
            return amd_power_of_three.image_for_alert(source_text)
        if "📅 РИТМ НЕДЕЛИ — КОНТЕКСТ" in text:
            return weekly_rhythm_context.image_for_alert(source_text)
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
    display_text = format_trade_card(localize_telegram(text))
    if image is not None:
        if len(display_text) <= 1000:
            await app.bot.send_photo(chat_id=int(chat_id), photo=image, caption=display_text)
        else:
            await app.bot.send_photo(
                chat_id=int(chat_id), photo=image,
                caption="📊 Сценарий подтверждён · полный разбор следующим сообщением",
            )
            await _send_parts(app, int(chat_id), display_text)
        return
    await _send_parts(app, int(chat_id), display_text)


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
    if "🏹🎯 KILLER" in (text or ""):
        try:
            event_id = killer_engine.event_id_for_alert(text)
            if event_id:
                return event_id
        except Exception:
            log.exception("KILLER_EVENT_ID_FAILED")
    return signal_context.event_fingerprint(text)


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
            image = _source_image_for(text, text)
            await _deliver_trade_card(app, chat_id, text, image)
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


def _master_allows_delivery(pair: str, side: str, master_results: list[dict]) -> bool:
    """Служебные карточки без стороны проходят. Направленный вход — только через Master."""
    if not pair or not side:
        return True
    if not getattr(cfg, "MASTER_DIRECTION_ENABLED", True):
        return True
    if not getattr(cfg, "MASTER_REQUIRE_FOR_DELIVERY", True):
        return True
    return any(item.get("symbol") == pair and item.get("side") == side for item in master_results)


def _alert_metric(text: str, label: str) -> int | None:
    match = re.search(rf"(?:^|\n)[^\n]*{re.escape(label)}:\s*(\d{{1,3}})(?:/100|%)?", text or "", re.I)
    return max(0, min(100, int(match.group(1)))) if match else None


def _alert_rank(item: tuple[int, str]) -> tuple[float, int, int]:
    """Качество/вероятность главнее прежнего фиксированного порядка модулей."""
    priority, text = item
    quality = _alert_metric(text, "Качество")
    if quality is None:
        quality = _alert_metric(text, "Killer Score")
    probability = _alert_metric(text, "Уверенность модели")
    if probability is None:
        probability = _alert_metric(text, "Вероятность")
    if probability is None:
        probability = _alert_metric(text, "Killer Score")
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


async def _send_briefing_sequence(app: Application, chat_id: int, market: dict, strength: dict, rank: list, dxy, events: list, upcoming_events: list) -> int:
    """Брифинг: общий контекст → картинка пары → описание пары → ... → итог.

    Если HTF-фигуры для пары нет, описание всё равно отправляется на своём месте.
    Обычный lifecycle pattern alerts не затрагивается.
    """
    head, pair_sections, tail = briefing.build_briefing_sections(
        market, strength, rank, dxy, events, upcoming_events
    )
    sent = 0
    for part in briefing.split_telegram(localize_telegram(head)):
        await send(app, int(chat_id), part); sent += 1
    for symbol, pair_text in pair_sections:
        pair_text = localize_telegram(pair_text)
        card = None
        if patterns is not None and getattr(cfg, "PATTERN_CHART_IMAGES_ENABLED", True):
            try:
                card = patterns.briefing_htf_card(symbol, (market or {}).get(symbol) or {})
            except Exception:
                log.exception("PATTERN_BRIEFING_IMAGE_FAILED pair=%s", symbol)
        if card:
            try:
                card["image"].seek(0)
                # Если аналитика пары помещается в caption, она идёт прямо под картинкой
                # в одном Telegram-сообщении. Иначе фото остаётся визуальным разделителем,
                # а полный блок отправляется непосредственно следующим сообщением.
                visual = localize_telegram(card["caption"])
                combined = f"{visual}\n\n{pair_text}"
                if len(combined) <= 1024:
                    await app.bot.send_photo(chat_id=int(chat_id), photo=card["image"], caption=combined)
                    sent += 1
                    pair_text = ""
                else:
                    await app.bot.send_photo(chat_id=int(chat_id), photo=card["image"], caption=visual)
                    sent += 1
            except Exception:
                log.exception("PATTERN_BRIEFING_IMAGE_SEND_FAILED pair=%s", symbol)
        if pair_text:
            for part in briefing.split_telegram(pair_text):
                await send(app, int(chat_id), part); sent += 1
    for part in briefing.split_telegram(localize_telegram(tail)):
        await send(app, int(chat_id), part); sent += 1
    return sent


async def _briefing_job_impl(context: ContextTypes.DEFAULT_TYPE) -> None:
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
        coverage = market_coverage(market)
        await _publish_market_health(context.application, int(chat_id), state, coverage)
        save_state(state)
        strength = currency_strength(h1_series(market), cfg.STRENGTH_LOOKBACK)
        rank = rank_currencies(strength)
        # Independent scheduler path: even if scan_job later fails inside a
        # scanner, the session briefing still has its own chance to publish.
        if coverage["complete"]:
            await _maybe_send_session_briefing(context, state, market, strength, rank)
        dxy = briefing.collect_extras(api_key, market=market)
        all_events = newsmod.load_events()
        now_utc = datetime.now(timezone.utc)
        for event in all_events:
            if not newsmod.needs_advance_warning(event):
                continue
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


async def _scan_job_impl(context: ContextTypes.DEFAULT_TYPE) -> None:
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
        # Passive replay/calibration observes matured journal decisions only.
        # It cannot veto, rerank or modify any live signal.
        try:
            replay_calibration.update(market)
            robustness_audit.update()
        except Exception:
            log.exception("Replay/Calibration update skipped")
        # Journals must continue maturing and scheduled summaries must still be
        # delivered even when a market-data gap blocks live trading.
        if getattr(cfg, "SIGNAL_JOURNAL_ENABLED", True):
            try:
                signal_journal.update_market(market)
            except Exception:
                log.exception("Обновление журнала сигналов")
        await _deliver_scheduled_reports(context.application, int(chat_id))
        coverage = market_coverage(market)
        await _publish_market_health(context.application, int(chat_id), state, coverage)
        save_state(state)
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
        await _maybe_send_session_briefing(context, state, market, strength, rank)

        module_alerts: list[tuple[int, str]] = []
        mandatory_pdh_pdl: list[str] = []
        scan_stats = ScanStats()
        enabled_flags = (
            "DISBALANCE_ENABLED", "IMBALANCE_ENABLED", "BPR_ENABLED", "QUASIMODO_ENABLED", "CONSOLIDATION_ZONE_ENABLED",
            "ACCUMULATION_DISTRIBUTION_ENABLED", "AMD_POWER_OF_THREE_ENABLED",
            "CRT_CANDLE_RANGE_ENABLED", "LIQUIDITY_SWEEP_ENABLED", "POC_ENABLED",
            "ORDER_BLOCK_ENABLED", "BREAKER_BLOCK_ENABLED", "SMART_MONEY_62_26_ENABLED",
            "SILVER_BULLET_ENABLED", "DAILY_HIGH_LOW_ENABLED", "CHAIN_ENTRIES_ENABLED",
            "RETEST_CONFIRMATION_ENABLED", "FIBONACCI_ENABLED", "ATS_REVERSAL_ENABLED",
            "FIB_SMC_ENABLED", "LEVELS_ENABLED", "ZIGZAG_SCANNER_ENABLED", "EDGE_ESTIMATOR_ENABLED", "PATTERNS_ENABLED",
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

        if getattr(cfg, "BPR_ENABLED", True):
            try:
                for text in bpr.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                scan_stats.note_fail("bpr"); log.exception("Ошибка модуля BPR")

        if getattr(cfg, "QUASIMODO_ENABLED", True):
            try:
                for text in quasimodo_engine.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                scan_stats.note_fail("quasimodo"); log.exception("Ошибка модуля Quasimodo")

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

        if getattr(cfg, "WEEKLY_RHYTHM_ENABLED", True):
            try:
                for text in weekly_rhythm_context.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                scan_stats.note_fail("weekly_rhythm"); log.exception("Ошибка модуля Ритм недели")

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
                    market, strength, silver_events, closed_bar_utc(closed_dt)
                ):
                    module_alerts.append((0, text))
            except Exception:
                scan_stats.note_fail("silver_bullet"); log.exception("Ошибка модуля ICT Silver Bullet")

        if getattr(cfg, "DAILY_HIGH_LOW_ENABLED", True):
            try:
                for text in daily_high_low.process_market(market, strength):
                    # A confirmed closed-H1 PDH/PDL break/reclaim is an event the
                    # user explicitly wants to see. It bypasses the hourly ranking
                    # cap, but still passes the global entry/significance safety gate.
                    if "📅 ПРОБОЙ PDH" in text or "📅 ПРОБОЙ PDL" in text or "📅 СНЯТИЕ PDH" in text or "📅 СНЯТИЕ PDL" in text:
                        mandatory_pdh_pdl.append(text)
                    else:
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
                    market, strength, fib_smc_events, closed_bar_utc(closed_dt)
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

        if getattr(cfg, "EDGE_ESTIMATOR_ENABLED", True):
            try:
                for text in edge_estimator.process_market(market, strength):
                    module_alerts.append((1, text))
            except Exception:
                scan_stats.note_fail("edge"); log.exception("Ошибка модуля короткого преимущества")

        if patterns is not None and getattr(cfg, "PATTERNS_ENABLED", True):
            try:
                for text in patterns.process_market(market, strength):
                    structural = any(name in text for name in (
                        "BOS", "Двойная", "голова и плечи", "AB=CD", "Паттерн 1-2-3",
                        "треугольник", "клин", "флаг", "вымпел", "прямоугольник",
                    ))
                    module_alerts.append((1 if structural else 3, text))
            except Exception:
                scan_stats.note_fail("patterns"); log.exception("Ошибка сканера паттернов")

        # Все торговые события сначала становятся внутренними кандидатами.
        # В чат уходит карточка источника только после Master Direction
        # по той же паре и стороне. Navigator идёт отдельным сопровождением TR.
        source_alerts = list(module_alerts) + [(0, text) for text in mandatory_amd_alerts + mandatory_level_breakouts + mandatory_pdh_pdl]
        raw_alerts = [text for _priority, text in source_alerts]
        # KILLER is a meta-selector over confirmed module facts; it never invents a setup.
        if getattr(cfg, "KILLER_ENABLED", True):
            try:
                for text in killer_engine.process_candidates(raw_alerts, market, strength):
                    module_alerts.append((-2, text))
                    source_alerts.append((-2, text))
                    raw_alerts.append(text)
            except Exception:
                scan_stats.note_fail("killer"); log.exception("Ошибка KILLER meta-engine")
        # События, не подтверждённые в эту H1, не теряются: они остаются
        # внутренними кандидатами ограниченное число часов.
        candidate_alerts = signal_navigator.remember_candidates(raw_alerts, closed_dt)
        navigator_sources: dict[str, list[str]] = {}
        confirmed_alerts: list[tuple[int, str]] = []
        master_results: list[dict] = []
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
                mandatory_keys = {
                    (pair, side)
                    for text in (mandatory_amd_alerts + mandatory_level_breakouts + mandatory_pdh_pdl)
                    for pair, side in ((_alert_pair(text), _direct_signal_side(text)),)
                    if pair and side
                }
                master_results = master_direction.analyze_market(
                    market,
                    strength,
                    candidate_alerts,
                    dxy_bias=master_dxy_bias,
                    events=master_events,
                    # News/Lifecycle must use the real wall clock. The closed H1
                    # timestamp belongs to market data only; using it here can keep
                    # Master inside a stale pre-news window for most of the hour.
                    now_utc=datetime.now(timezone.utc),
                    priority_keys=mandatory_keys,
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
                if pair and side and cooldown_ok(state, pair, side, "NAVIGATOR"):
                    confirmed_alerts.append((-1, text))
                    navigator_sources[text] = sources
                    signal_navigator.register_card(text, sources, closed_dt)

        # Часовой лимит считает исходные торговые события. Навигатор является
        # обязательным сопровождением выбранного события и этот лимит не тратит.
        # AMD уже будет отправлен обязательным потоком ниже, поэтому повторно
        # не участвует в ранжировании и не может быть вытеснен/задублирован.

        # Верхний предел новых карточек за H1. 0 = без лимита.
        # AMD, обязательный пробой уровня и KILLER идут отдельно и лимит не едят.
        hourly_cap = int(getattr(cfg, "MAX_MODULE_ALERTS_PER_H1", 7) or 0)
        killer_cards = []
        trade_pool = list(module_alerts)
        if getattr(cfg, "KILLER_BYPASS_HOURLY_CAP", True):
            killer_cards = [(-2, text) for _p, text in module_alerts if "🏹🎯 KILLER" in text]
            trade_pool = [(p, text) for p, text in module_alerts if "🏹🎯 KILLER" not in text]
        if hourly_cap > 0:
            selected_alerts = [(0, text) for text in select_trade_alerts(trade_pool, limit=hourly_cap)]
        else:
            selected_alerts = list(trade_pool)
        selected_alerts = killer_cards + selected_alerts
        if scan_stats.abort_delivery():
            selected_alerts = []
            mandatory_amd_alerts = []
            mandatory_level_breakouts = []
            mandatory_pdh_pdl = []
            log.error("SCAN_TRADE_CARDS_SUPPRESSED reason=too_many_scanner_failures")
        # Повторяем карточки, чья предыдущая отправка временно не удалась.
        # Они не расходуют лимит новых исходных сигналов.
        await _flush_source_outbox(context.application, int(chat_id), state)
        await _flush_navigator_outbox(context.application, int(chat_id), state)
        # ZIP 31: направленный факт модуля остаётся внутренним, пока его
        # не подтвердит Master. Navigator снова только сопровождает вход.
        proposed_delivery = mandatory_amd_alerts + mandatory_level_breakouts + mandatory_pdh_pdl + [
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
                # Паттерн не обходит запрет входа после крупного слива.
                # Остальной horizon-gate паттерну не нужен: важен первый close.
                if "🧩 ПАТТЕРН ПОДТВЕРЖДЁН" in source_text:
                    pair = _alert_pair(source_text)
                    side = _direct_signal_side(source_text)
                    early = ohlc_movement.early_entry_check(market.get(pair) or {}, side)
                    if early.get("allow", True):
                        significant_alerts.append(source_text)
                    else:
                        try:
                            decision_journal.record_stage(source_text, market, strength, "BLOCKED",
                                reason="final_pattern_early_entry_" + str(early.get("reason") or "unknown"))
                        except Exception:
                            log.exception("SIGNAL_GATE_AUDIT_SKIPPED")
                        log.info(
                            "SIGNAL_INTERNAL_ONLY pair=%s side=%s reason=%s age=%s travel=%s",
                            pair, side, early.get("reason"), early.get("impulse_age"),
                            early.get("travel_atr"),
                        )
                    continue
                try:
                    significance = signal_navigator.assess_new_signal_significance(
                        source_text, market, strength
                    )
                except Exception:
                    log.exception("SIGNAL_SIGNIFICANCE_GATE_FAILED")
                    significance = {"eligible": False, "reason": "gate_error"}
                if significance.get("eligible", True):
                    significant_alerts.append(source_text)
                else:
                    try:
                        decision_journal.record_stage(source_text, market, strength, "BLOCKED",
                            reason="final_significance_" + str(significance.get("reason") or "unknown"))
                    except Exception:
                        log.exception("SIGNAL_GATE_AUDIT_SKIPPED")
                    log.info(
                        "SIGNAL_INTERNAL_ONLY pair=%s side=%s reason=%s status=%s h1=%s route_atr=%s body_atr=%s eff=%s",
                        _alert_pair(source_text), _direct_signal_side(source_text),
                        significance.get("reason"),
                        significance.get("trade_lifecycle_status"),
                        significance.get("remaining_h1"),
                        significance.get("route_atr"), significance.get("median_body_atr"),
                        significance.get("efficiency"),
                    )
            delivery_alerts = significant_alerts
        try:
            bundles, dropped_facts = signal_context.prepare(delivery_alerts, market, strength)
        except Exception:
            log.exception("SIGNAL_CONTEXT_FAILED: new entries are blocked, not bypassed")
            for t in delivery_alerts:
                try:
                    decision_journal.record_stage(t, market, strength, "BLOCKED", [], {}, "context_error")
                except Exception:
                    log.exception("SIGNAL_GATE_AUDIT_SKIPPED")
            bundles = []  # Fail-closed for trade entries; keep rest of scan alive.
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
            if pair and direct_side and not cooldown_ok(state, pair, direct_side, signal_context.source_name(source_text)):
                log.info("SIGNAL_COOLDOWN_SKIP pair=%s side=%s", pair, direct_side)
                try:
                    decision_journal.record_stage(source_text, market, strength, "BLOCKED",
                        bundle.get("allies") or [], bundle.get("ctx") or {}, "final_cooldown")
                except Exception:
                    log.exception("SIGNAL_GATE_AUDIT_SKIPPED")
                continue
            if not _master_allows_delivery(pair, direct_side, master_results):
                log.info("SIGNAL_MASTER_BLOCK pair=%s side=%s reason=no_master_confirmation", pair, direct_side)
                try:
                    decision_journal.record_stage(source_text, market, strength, "BLOCKED",
                        bundle.get("allies") or [], bundle.get("ctx") or {}, "final_master_not_confirmed")
                except Exception:
                    log.exception("SIGNAL_GATE_AUDIT_SKIPPED")
                continue
            if pair and direct_side and not signal_allowed_by_h4_zigzag(pair, market.get(pair) or {}, direct_side):
                log.info("SIGNAL_H4_ZIGZAG_BLOCK pair=%s side=%s", pair, direct_side)
                try:
                    decision_journal.record_stage(source_text, market, strength, "BLOCKED",
                        bundle.get("allies") or [], bundle.get("ctx") or {}, "final_h4_zigzag")
                except Exception:
                    log.exception("SIGNAL_GATE_AUDIT_SKIPPED")
                continue
            # Passive funnel marker after all final delivery gates.
            try:
                decision_journal.record_stage(
                    source_text, market, strength, "MASTER_CONFIRMED",
                    bundle.get("allies") or [], bundle.get("ctx") or {},
                    "final_delivery_gates_passed",
                    master=next((item for item in master_results
                                 if item.get("symbol") == pair and item.get("side") == direct_side), None),
                )
            except Exception:
                log.exception("Запись этапа MASTER_CONFIRMED пропущена")
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
            if patterns is not None and "🧩 ПАТТЕРН ПОДТВЕРЖДЁН" in source_text:
                # Pattern state/cache is keyed by the original detector text. The
                # displayed primary may be enriched/combined by signal_context/CPI.
                patterns.mark_card_delivered(source_text)
            # Unified delivery acknowledgement: confirmed facts stay PENDING until
            # the exact Telegram card has been delivered successfully.
            for _module in (liquidity_sweep, order_block, breaker_block, smart_money_62_26, daily_high_low, retest_confirmation, ats_reversal_point):
                try:
                    _module.mark_delivered(source_text)
                except Exception:
                    log.exception("DELIVERY_ACK_FAILED module=%s", getattr(_module, "__name__", "unknown"))
            _mark_event_delivered(state, source_event_id)
            for extra in bundle.get("allies") or []:
                _mark_event_delivered(state, _source_event_id(extra))
            _ack_source(state, source_text)
            if pair and direct_side:
                state.setdefault("last_signals", {})[f"{pair}:{direct_side}"] = time.time()
                state.setdefault("last_setups", {})[f"{pair}:{direct_side}:{signal_context.source_name(source_text).strip().upper()}"] = time.time()
            save_state(state)
            delivered_sources = list(allied_texts)
            for delivered_source_text in delivered_sources:
                if "↕️ ZIGZAG —" in delivered_source_text:
                    try:
                        zigzag_scanner.mark_delivered(delivered_source_text)
                    except Exception:
                        log.exception("Фиксация доставленного ZigZag")
                if "⚖️ ДИСБАЛАНС ПОДТВЕРЖДЁН" in delivered_source_text:
                    try:
                        disbalance.mark_delivered(delivered_source_text)
                    except Exception:
                        log.exception("Фиксация доставленного Disbalance")
                if "⚖️ BPR — BALANCED PRICE RANGE" in delivered_source_text:
                    try:
                        bpr.mark_delivered(delivered_source_text)
                    except Exception:
                        log.exception("Фиксация доставленного BPR")
                if "🔄 QUASIMODO — QM" in delivered_source_text:
                    try:
                        quasimodo_engine.mark_delivered(delivered_source_text)
                    except Exception:
                        log.exception("Фиксация доставленного Quasimodo")
                if "🏹🎯 KILLER" in delivered_source_text:
                    try:
                        killer_engine.mark_delivered(delivered_source_text)
                    except Exception:
                        log.exception("Фиксация доставленного KILLER")
                if "IMBALANCE —" in delivered_source_text:
                    try:
                        imbalance.mark_delivered(delivered_source_text)
                    except Exception:
                        log.exception("Фиксация доставленного Imbalance/FVG")
                if getattr(cfg, "AMD_POWER_OF_THREE_ENABLED", True):
                    try:
                        amd_power_of_three.mark_delivered(delivered_source_text)
                    except Exception:
                        log.exception("Фиксация доставленного AMD")
                if "📅 РИТМ НЕДЕЛИ — КОНТЕКСТ" in delivered_source_text:
                    try:
                        weekly_rhythm_context.mark_delivered(delivered_source_text)
                    except Exception:
                        log.exception("Фиксация доставленного Ритма недели")
                if "🕯 CRT — CANDLE RANGE THEORY" in delivered_source_text:
                    try:
                        crt_candle_range.mark_delivered(delivered_source_text)
                    except Exception:
                        log.exception("Фиксация доставленного CRT")
                if "📐 РЕАКЦИЯ ОТ СЕТКИ ФИБОНАЧЧИ" in delivered_source_text:
                    try:
                        fibonacci_grid.mark_delivered(delivered_source_text)
                    except Exception:
                        log.exception("Фиксация доставленного Fibonacci")
                if "ICT SILVER BULLET" in delivered_source_text:
                    try:
                        silver_bullet_ict.mark_delivered(delivered_source_text)
                    except Exception:
                        log.exception("Фиксация доставленного Silver Bullet")
                if "🧬 FIB + SMC —" in delivered_source_text:
                    try:
                        fib_smc.mark_delivered(delivered_source_text)
                    except Exception:
                        log.exception("Фиксация доставленного Fib+SMC")
                if "⛓️ CHAIN ENTRY" in delivered_source_text:
                    try:
                        chain_entries.mark_delivered(delivered_source_text)
                    except Exception:
                        log.exception("Фиксация доставленного Chain Entry")
                if "📦 ВЫХОД ИЗ ЗОНЫ КОНСОЛИДАЦИИ" in delivered_source_text:
                    try:
                        consolidation_zone.mark_delivered(delivered_source_text)
                    except Exception:
                        log.exception("Фиксация доставленного Consolidation Zone")
                if "🚀 ВЫХОД ИЗ ФАЗЫ" in delivered_source_text or "📦 ФАЗА " in delivered_source_text:
                    try:
                        accumulation_distribution.mark_delivered(delivered_source_text)
                    except Exception:
                        log.exception("Фиксация доставленной фазы накопления/распределения")
            if getattr(cfg, "SIGNAL_JOURNAL_ENABLED", True):
                try:
                    signal_journal.record_sent(text, market, closed_dt)
                except Exception:
                    log.exception("Запись отправленного сигнала в журнал")
            # Preserve the primary delivered source: ACKs above visit its allies.
            # Find the exact route Navigator will send; it may replace source
            # targets with Pivot stages. Snapshot it BEFORE recording SENT.
            pair = _alert_pair(text)
            direct_side = _direct_signal_side(text)
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
            matching_master = next((item for item in master_results
                                   if item.get("symbol") == pair and item.get("side") == direct_side), None)
            # Telemetry must never prevent or veto a successfully delivered trade.
            try:
                journal_ctx = dict(bundle.get("ctx") or {})
                if companion:
                    route = decision_journal.navigator_route_snapshot(companion[0])
                    if route:
                        journal_ctx["route"] = route
                        journal_ctx["route_authoritative"] = True
                        if "tr1_progress" in route:
                            journal_ctx["progress"] = route["tr1_progress"]
                            journal_ctx["progress_observed"] = True
                decision_journal.record_sent(
                    source_text, market, strength, bundle.get("allies") or [],
                    journal_ctx, master=matching_master,
                )
            except Exception:
                log.exception("Запись Decision Journal пропущена")
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
                state.setdefault("last_setups", {})[f"{pair}:{direct_side}:{signal_context.source_name(source_text).strip().upper()}"] = time.time()

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
                for bundle in session_projection_reports.pending_report_bundles(
                        market, session_events, state):
                    try:
                        caption = localize_telegram(bundle["caption"])
                        image = bundle.get("image")
                        if image is None:
                            raise ValueError("Unified Echo/Pivot/ZigZag image is missing")
                        image.seek(0)
                        message = await context.application.bot.send_photo(
                            chat_id=int(chat_id), photo=image, caption=caption)
                        message_id = getattr(message, "message_id", None)
                        session_projection_reports.mark_delivered(state, bundle["key"])
                        save_state(state)
                        log.info("SESSION_ECHO_PIVOT_BUNDLE_SENT message_id=%s key=%s pid=%s",
                                 message_id, bundle["key"], briefing.instance_id())
                    except Exception:
                        # Вся пара считается недоставленной, если album не отправился целиком.
                        log.exception("SESSION_ECHO_PIVOT_BUNDLE_SEND_FAILED key=%s pid=%s",
                                      bundle.get("key", "unknown"), briefing.instance_id())
            except Exception:
                log.exception("SESSION_REPORT_SCAN_FAILED")

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



_RUNTIME_JOB_LOCK: asyncio.Lock | None = None
_RUNTIME_CHART_CACHE_LIMIT = max(4, int(os.getenv("RUNTIME_CHART_CACHE_LIMIT", "16")))


def _job_lock() -> asyncio.Lock:
    global _RUNTIME_JOB_LOCK
    if _RUNTIME_JOB_LOCK is None:
        _RUNTIME_JOB_LOCK = asyncio.Lock()
    return _RUNTIME_JOB_LOCK


def _current_rss_mb() -> float | None:
    """Current Linux RSS for Railway telemetry; never affects trading logic."""
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return round(float(line.split()[1]) / 1024.0, 1)
    except Exception:
        pass
    return None


def _trim_mapping(mapping: dict, limit: int) -> int:
    """Bound insertion-ordered runtime caches, evicting oldest presentation cards."""
    removed = 0
    while len(mapping) > limit:
        try:
            mapping.pop(next(iter(mapping)))
            removed += 1
        except (StopIteration, RuntimeError):
            break
    return removed


def _trim_runtime_chart_caches(limit: int | None = None) -> int:
    """Prevent blocked/duplicate chart snapshots from accumulating for days.

    These dicts are presentation-only caches. Durable lifecycle state stays in
    STATE_DIR JSON/SQLite and is not touched here.
    """
    cap = max(4, int(limit or _RUNTIME_CHART_CACHE_LIMIT))
    specs = (
        (silver_bullet_ict, "_PENDING_CARDS"),
        (liquidity_sweep, "_LAST_CHART_CARDS"),
        (bpr, "_PENDING_CARDS"),
        (quasimodo_engine, "_PENDING_CARDS"),
        (retest_confirmation, "_RETEST_CHART_CACHE"),
        (chain_entries, "_PENDING_CARDS"),
        (daily_high_low, "_PENDING_CARDS"),
        (accumulation_distribution, "_PENDING_CARDS"),
        (ats_reversal_point, "_LAST_CHART_CARDS"),
        (imbalance, "_PENDING_CARDS"),
        (crt_candle_range, "_PENDING_CARDS"),
        (smart_money_62_26, "_LAST_CHART_CARDS"),
        (breaker_block, "_LAST_CHART_CARDS"),
        (disbalance, "_PENDING_CARDS"),
        (order_block, "_LAST_CHART_CARDS"),
        (poc_profile, "_LAST_CHART_CARDS"),
        (fibonacci_grid, "_PENDING_CARDS"),
        (amd_power_of_three, "_PENDING_CARDS"),
        (zigzag_scanner, "_PENDING_CARDS"),
        (consolidation_zone, "_PENDING_CARDS"),
        (weekly_rhythm_context, "_PENDING_CARDS"),
        (levels, "_PENDING_CARDS"),
        (fib_smc, "_PENDING_CARDS"),
    )
    if patterns is not None:
        specs = specs + ((patterns, "_PENDING_CARDS"),)
    removed = 0
    for module, attr in specs:
        cache = getattr(module, attr, None)
        if isinstance(cache, dict):
            removed += _trim_mapping(cache, cap)
    return removed


async def _run_serialized_runtime_job(name: str, impl, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run only one heavy Forex runtime pass at once.

    scan_job and briefing_job are offset by seconds but both build the full market
    snapshot. If one is still alive when the other starts, skip the duplicate pass
    instead of holding two full analysis trees/images in RAM simultaneously.
    """
    lock = _job_lock()
    if lock.locked():
        log.warning("RUNTIME_JOB_SKIPPED_BUSY name=%s", name)
        return
    async with lock:
        before = _current_rss_mb()
        try:
            await impl(context)
        finally:
            removed = _trim_runtime_chart_caches()
            gc.collect()
            after = _current_rss_mb()
            log.info(
                "RUNTIME_MEMORY name=%s rss_before_mb=%s rss_after_mb=%s chart_cache_evicted=%s",
                name, before, after, removed,
            )


async def briefing_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_serialized_runtime_job("briefing_job", _briefing_job_impl, context)


async def scan_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_serialized_runtime_job("scan_job", _scan_job_impl, context)

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
