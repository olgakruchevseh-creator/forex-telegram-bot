"""Единый статус торгового решения: новый вход vs сопровождение vs тишина.

Не является самостоятельной стратегией и не добавляет KILLER-семейство.
Склеивает уже существующие факты: новости, сессия, режим, путь до цели.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import config as cfg
import news as newsmod
import market_regime
import session_cycle_context
from analysis import split_pair

NEW_ENTRY = "NEW_ENTRY"
MANAGE = "MANAGE"
WATCH = "WATCH"
STAND_DOWN = "STAND_DOWN"

NEWS_CLEAR = "CLEAR"
NEWS_PRE_EVENT = "PRE_EVENT"
NEWS_POST_NO_SURPRISE = "POST_NO_SURPRISE"
NEWS_SURPRISE_WITH = "SURPRISE_WITH"
NEWS_SURPRISE_AGAINST = "SURPRISE_AGAINST"

_REASON_RU = {
    "ok_new_entry": "новый вход разрешён",
    "pre_event_stand_down": "до важного события новые входы закрыты",
    "surprise_against": "сюрприз новости против сценария",
    "post_event_no_surprise": "после события нет подтверждённого сюрприза",
    "asia_compression": "азиатское сжатие — не новый вход",
    "session_balance": "сессия в балансе/накоплении",
    "regime_compression": "рынок в сжатии",
    "insufficient_room_to_tr1": "слишком мало места до TR1",
    "insufficient_room_to_route": "маршрут TR1–TR3 уже короткий",
    "late_after_impulse": "импульс уже реализован, только сопровождение",
    "displacement_already_consumed": "смещение уже съедено",
    "confirmed_structural_reversal": "подтверждён структурный разворот против",
    "stale_source": "факт источника уже старый для нового входа",
    "short_horizon": "горизонт текущего импульса слишком короткий",
    "small_range_candles": "свечи слишком мелкие для нового входа",
    "correlated_usd_exposure": "та же ставка на USD уже открыта в другой паре",
    "manage_active_route": "направление живо, новый вход не нужен",
    "watch_context_only": "контекст есть, готового входа нет",
}


def reason_ru(code: str) -> str:
    return _REASON_RU.get(code or "", code or "")


def _usd_expected(symbol: str, side: int) -> int:
    base, quote = split_pair(symbol)
    if base == "USD":
        return side
    if quote == "USD":
        return -side
    return 0


def _print_usd_dir(event: newsmod.NewsEvent) -> int:
    """+1 = валюта события усилилась, -1 = ослабла, 0 = нет сюрприза."""
    verdict = newsmod.interpret_print(event)
    if verdict == "positive":
        return 1
    if verdict == "negative":
        return -1
    return 0


def classify_news(symbol: str, side: int, events: list, now_utc: datetime | None = None) -> dict:
    now_utc = now_utc or datetime.now(timezone.utc)
    base, quote = split_pair(symbol)
    before = int(getattr(cfg, "MASTER_NEWS_BLOCK_BEFORE_MINUTES", 60))
    after = int(getattr(cfg, "MASTER_NEWS_BLOCK_AFTER_MINUTES", 30))
    usd_exp = _usd_expected(symbol, side)
    best = {
        "mode": NEWS_CLEAR,
        "event": None,
        "minutes_left": None,
        "title": "",
        "currency": "",
    }
    rank = {
        NEWS_CLEAR: 0,
        NEWS_POST_NO_SURPRISE: 1,
        NEWS_SURPRISE_WITH: 2,
        NEWS_PRE_EVENT: 3,
        NEWS_SURPRISE_AGAINST: 4,
    }
    for event in newsmod.high_events(events or []):
        if event.currency not in (base, quote):
            continue
        if newsmod.is_speech_event(event) and not newsmod.has_actual(event):
            left = newsmod.minutes_left(event, now_utc)
            if -after <= left <= before and rank[NEWS_PRE_EVENT] > rank[best["mode"]]:
                best = {
                    "mode": NEWS_PRE_EVENT,
                    "event": event,
                    "minutes_left": left,
                    "title": event.title,
                    "currency": event.currency,
                }
            continue
        left = newsmod.minutes_left(event, now_utc)
        if not (-after <= left <= before):
            continue
        printed = newsmod.has_actual(event) or left <= 0
        surprise_dir = _print_usd_dir(event) if printed else 0
        if not printed or left > 0 and not newsmod.has_actual(event):
            mode = NEWS_PRE_EVENT
        elif surprise_dir == 0:
            mode = NEWS_POST_NO_SURPRISE
        else:
            event_usd = surprise_dir if event.currency == "USD" else (
                -surprise_dir if event.currency in (base, quote) and "USD" in (base, quote) else 0
            )
            if event.currency != "USD":
                # Усиление базовой валюты помогает LONG, котируемой — SHORT.
                pair_dir = surprise_dir if event.currency == base else -surprise_dir
                mode = NEWS_SURPRISE_WITH if pair_dir == side else NEWS_SURPRISE_AGAINST
            else:
                mode = NEWS_SURPRISE_WITH if event_usd == usd_exp and usd_exp else (
                    NEWS_SURPRISE_AGAINST if usd_exp and event_usd == -usd_exp else NEWS_POST_NO_SURPRISE
                )
        if rank[mode] >= rank[best["mode"]]:
            best = {
                "mode": mode,
                "event": event,
                "minutes_left": left,
                "title": event.title,
                "currency": event.currency,
            }
    return best


def current_session_key(now_local: datetime | None = None) -> str:
    hour = (now_local or datetime.now()).hour
    if hour >= 15:
        return "AMERICA"
    if hour >= 9:
        return "EUROPE"
    return "ASIA"


def session_character(symbol: str, by_tf: dict, now_local: datetime | None = None) -> dict:
    phases = session_cycle_context.analyze_symbol(symbol, by_tf, now_local)
    key = current_session_key(now_local)
    current = next((p for p in phases if p.session == key), None)
    return {
        "session": key,
        "phase": getattr(current, "phase", ""),
        "status": getattr(current, "status", ""),
        "direction": int(getattr(current, "direction", 0) or 0),
        "reason": getattr(current, "reason", ""),
    }


def _same_usd_bet(symbol: str, side: str, other_symbol: str, other_side: str) -> bool:
    if not symbol or not side or not other_symbol or not other_side:
        return False
    if symbol == other_symbol:
        return True
    a_base, a_quote = split_pair(symbol)
    b_base, b_quote = split_pair(other_symbol)
    if "USD" not in (a_base, a_quote) or "USD" not in (b_base, b_quote):
        return False
    a_usd = 1 if (a_base == "USD" and side == "LONG") or (a_quote == "USD" and side == "SHORT") else -1
    if a_quote == "USD" and side == "LONG":
        a_usd = -1
    if a_base == "USD" and side == "SHORT":
        a_usd = -1
    b_usd = 1 if (b_base == "USD" and other_side == "LONG") or (b_quote == "USD" and other_side == "SHORT") else -1
    if b_quote == "USD" and other_side == "LONG":
        b_usd = -1
    if b_base == "USD" and other_side == "SHORT":
        b_usd = -1
    # Normalize via expected USD direction.
    def usd_dir(sym: str, sd: str) -> int:
        base, quote = split_pair(sym)
        side_i = 1 if sd == "LONG" else -1
        if base == "USD":
            return side_i
        if quote == "USD":
            return -side_i
        return 0
    return usd_dir(symbol, side) == usd_dir(other_symbol, other_side) and usd_dir(symbol, side) != 0


@dataclass
class Lifecycle:
    status: str
    reason: str
    news_mode: str
    session: str
    session_phase: str
    allow_new_entry: bool
    allow_manage: bool
    detail: str = ""

    def describe(self) -> str:
        status_ru = {
            NEW_ENTRY: "НОВЫЙ ВХОД",
            MANAGE: "СОПРОВОЖДЕНИЕ",
            WATCH: "НАБЛЮДЕНИЕ",
            STAND_DOWN: "ПАУЗА",
        }.get(self.status, self.status)
        line = f"Статус решения: {status_ru} · {reason_ru(self.reason)}"
        if self.detail:
            line += f" · {self.detail}"
        return line


def evaluate(
    symbol: str,
    side: int | str,
    by_tf: dict | None = None,
    events: list | None = None,
    now_utc: datetime | None = None,
    significance: dict | None = None,
    regime_name: str = "",
    active_routes: dict | None = None,
    now_local: datetime | None = None,
) -> Lifecycle:
    if isinstance(side, str):
        side_i = 1 if side.upper() == "LONG" else -1 if side.upper() == "SHORT" else 0
        side_s = side.upper()
    else:
        side_i = int(side or 0)
        side_s = "LONG" if side_i > 0 else "SHORT" if side_i < 0 else ""

    now_utc = now_utc or datetime.now(timezone.utc)
    news_info = classify_news(symbol, side_i, events or [], now_utc)
    sess = session_character(symbol, by_tf or {}, now_local)
    if not regime_name and by_tf:
        try:
            regime = market_regime.analyze_symbol(symbol, by_tf)
            regime_name = getattr(regime, "name", "") or ""
        except Exception:
            regime_name = ""

    sig = significance or {}
    sig_reason = str(sig.get("reason") or "")
    sig_eligible = bool(sig.get("eligible", True)) if significance is not None else True

    detail_bits = []
    if news_info.get("title"):
        detail_bits.append(f"{news_info['currency']} {news_info['title'][:48]}")
    if sess.get("phase"):
        detail_bits.append(f"{sess['session']}: {sess['phase']}")
    detail = " · ".join(detail_bits)

    # 1. News regimes
    mode = news_info["mode"]
    if mode == NEWS_SURPRISE_AGAINST:
        return Lifecycle(STAND_DOWN, "surprise_against", mode, sess["session"], sess["phase"], False, False, detail)
    if mode == NEWS_PRE_EVENT:
        return Lifecycle(STAND_DOWN, "pre_event_stand_down", mode, sess["session"], sess["phase"], False, True, detail)
    if mode == NEWS_POST_NO_SURPRISE:
        return Lifecycle(WATCH, "post_event_no_surprise", mode, sess["session"], sess["phase"], False, True, detail)

    # 2. Hard significance reasons that mean "direction may live, entry is late"
    late_reasons = {
        "insufficient_room_to_tr1", "insufficient_room_to_route",
        "late_after_impulse", "displacement_already_consumed",
        "stale_source", "short_horizon",
    }
    if significance is not None and not sig_eligible and sig_reason in late_reasons:
        return Lifecycle(MANAGE, sig_reason, mode, sess["session"], sess["phase"], False, True, detail)
    if significance is not None and not sig_eligible and sig_reason == "confirmed_structural_reversal":
        return Lifecycle(STAND_DOWN, sig_reason, mode, sess["session"], sess["phase"], False, False, detail)
    if significance is not None and not sig_eligible:
        return Lifecycle(WATCH, sig_reason or "watch_context_only", mode, sess["session"], sess["phase"], False, True, detail)

    # 3. Session / regime character
    phase = (sess.get("phase") or "").upper()
    if sess.get("session") == "ASIA" and any(x in phase for x in ("НАКОПЛЕНИЕ", "БАЛАНС")):
        return Lifecycle(WATCH, "asia_compression", mode, sess["session"], sess["phase"], False, True, detail)
    if any(x in phase for x in ("НАКОПЛЕНИЕ", "БАЛАНС / РАСПРЕДЕЛЕНИЕ")) and sess.get("session") != "EUROPE":
        return Lifecycle(WATCH, "session_balance", mode, sess["session"], sess["phase"], False, True, detail)
    if "COMPRESSION" in (regime_name or "").upper() or "СЖАТИЕ" in (regime_name or "").upper():
        return Lifecycle(WATCH, "regime_compression", mode, sess["session"], sess["phase"], False, True, detail)

    # 4. Correlated USD exposure against already active routes
    if active_routes and side_s:
        for other_symbol, route in (active_routes or {}).items():
            if not isinstance(route, dict):
                continue
            if route.get("status") != "ACTIVE":
                continue
            other_side = str(route.get("side") or "")
            if other_symbol != symbol and _same_usd_bet(symbol, side_s, str(other_symbol), other_side):
                return Lifecycle(
                    WATCH, "correlated_usd_exposure", mode, sess["session"], sess["phase"],
                    False, True, f"{other_symbol} {other_side}",
                )
            if other_symbol == symbol and other_side == side_s:
                return Lifecycle(MANAGE, "manage_active_route", mode, sess["session"], sess["phase"], False, True, detail)

    return Lifecycle(NEW_ENTRY, "ok_new_entry", mode, sess["session"], sess["phase"], True, True, detail)


def news_blocks_new_entry(symbol: str, events: list, now_utc: datetime | None = None, side: int = 1) -> bool:
    info = classify_news(symbol, side, events or [], now_utc)
    return info["mode"] in {NEWS_PRE_EVENT, NEWS_SURPRISE_AGAINST, NEWS_POST_NO_SURPRISE}
