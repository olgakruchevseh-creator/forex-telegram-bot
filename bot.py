#!/usr/bin/env python3
"""Личный FX-бот. Мультитаймфрейм W1→D1→H4→H1→M15→M5."""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
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

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fxbot")

STATE_FILE = Path(__file__).parent / "state.json"
TD_URL = "https://api.twelvedata.com/time_series"

# cache[(symbol, tf_key)] = {"ts": float, "candles": list[Candle]}
CACHE: dict[tuple[str, str], dict] = {}
SENT_H1: set[str] = set()
LOCAL_TZ = ZoneInfo("Europe/Berlin")


def env(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise SystemExit(f"Не задано {name}. Заполни .env")
    return v


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        "chat_id": os.getenv("TELEGRAM_CHAT_ID") or None,
        "last_signals": {},
        "last_rank": [],
        "last_strength_ts": 0,
    }


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


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


def format_h1_time(dt_str: str) -> str:
    raw = (dt_str or "")[:19]
    try:
        utc = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        local = utc.astimezone(LOCAL_TZ)
        return f"{local:%H:%M} по Берлину ({utc:%H:%M} UTC)"
    except ValueError:
        return dt_str


def format_strength(rank: list[tuple[str, float]], candle_dt: str = "") -> str:
    title = "💱 Сила валют по закрытой часовой свече"
    if candle_dt:
        title += f"\nСвеча: {format_h1_time(candle_dt)}"
    lines = [title + "\n"]
    for i, (cur, sc) in enumerate(rank, 1):
        sign = "+" if sc >= 0 else ""
        lines.append(f"{i}. {cur}  {sign}{sc:.2f}  {bars(sc)}")
    lines.append(f"\nСамая сильная: {rank[0][0]}")
    lines.append(f"Самая слабая: {rank[-1][0]}")
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


async def send(app: Application, chat_id: int, text: str) -> None:
    await app.bot.send_message(chat_id=chat_id, text=text)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    state["chat_id"] = update.effective_chat.id
    save_state(state)
    await update.message.reply_text(
        "Готово. Я запомнил тебя.\n"
        "Смотрю W1, D1, H4, H1, M15, M5.\n"
        "Пишу только когда старшие и младшие ТФ смотрят в одну сторону "
        "и сила валют это подтверждает.\n"
        "Силу валют присылаю после закрытия каждой часовой свечи.\n\n"
        "/now — сила валют сейчас\n"
        "/pair EUR/USD — стек таймфреймов по паре\n"
        "/status — жив ли я"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Работаю. 7 мажоров × W1/D1/H4/H1/M15/M5.\n"
        "Сила валют — после закрытия каждой H1. LONG/SHORT — только при согласии ТФ."
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


async def scan_job(context: ContextTypes.DEFAULT_TYPE) -> None:
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
        already = state.get("last_strength_h1")
        if rank and closed_dt and closed_dt not in SENT_H1 and closed_dt != already:
            SENT_H1.add(closed_dt)
            await send(context.application, int(chat_id), format_strength(rank, closed_dt))
            state["last_rank"] = [c for c, _ in rank]
            state["last_strength_h1"] = closed_dt
            state["last_strength_ts"] = time.time()

        for symbol, by_tf in market.items():
            stack = build_stack(symbol, by_tf, strength)
            if not stack:
                continue
            side = decide_signal(stack)
            if not side or not cooldown_ok(state, symbol, side):
                continue
            await send(context.application, int(chat_id), format_signal(side, stack, strength))
            state.setdefault("last_signals", {})[f"{symbol}:{side}"] = time.time()

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
    app.job_queue.run_repeating(scan_job, interval=cfg.SCAN_EVERY_MINUTES * 60, first=20)
    log.info("Бот запущен.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
