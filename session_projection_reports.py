"""Сессионные карточки Эхо и Next Pivot по всем отслеживаемым парам.

Карточки информационные: они не участвуют в лимите торговых сигналов и не
проходят через Навигатор. Новости меняют только статус риска и техническую
уверенность; до публикации бот не угадывает фактическое значение.
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timezone

import config as cfg
import briefing
import echo_projection
import news as newsmod
import next_pivot_projection
from analysis import closed_candles

log = logging.getLogger("fxbot.session_projections")


def _session_context() -> tuple[str, str, int]:
    now = briefing.now_local()
    current = briefing.current_session(now)
    following, following_start = briefing.next_session(now)
    hours = max(1, int(round((following_start - now).total_seconds() / 3600)))
    return current["name"], following["name"], hours


def _pair_events(symbol: str, events: list[newsmod.NewsEvent]) -> list[newsmod.NewsEvent]:
    base, quote = symbol.split("/")
    return [event for event in events
            if event.currency in (base, quote)
            and (event.impact in ("HIGH", "MEDIUM") or newsmod.is_briefing_low_watch(event))]


def _news_context(symbol: str, events: list[newsmod.NewsEvent], confidence: int | None) -> dict:
    relevant = _pair_events(symbol, events)
    if not relevant:
        return {
            "confidence": confidence,
            "headline": "📰 До следующей сессии значимых новостей по паре нет.",
            "lines": [],
            "status": "Технический сценарий не зависит от запланированной новости.",
        }
    penalty = 0
    lines = []
    has_high = False
    uncertain = False
    for event in relevant:
        icon = "🔴" if event.impact == "HIGH" else ("🟠" if event.impact == "MEDIUM" else "🟡")
        importance = "высокая" if event.impact == "HIGH" else ("средняя" if event.impact == "MEDIUM" else "наблюдение")
        lines.append(f"{icon} {event.local_hm} · {event.currency} · {newsmod.translate_title(event.title)} · {importance}")
        if event.impact == "HIGH":
            has_high = True
            penalty = max(penalty, 12 if event.economic_effect == "context_dependent" else 8)
        elif event.impact == "MEDIUM":
            penalty = max(penalty, 5)
        uncertain = uncertain or event.economic_effect == "context_dependent"
    adjusted = None if confidence is None else max(50, int(confidence) - penalty)
    if has_high:
        status = ("⚠️ Сценарий действует только до важной новости; после публикации нужна новая закрытая M15/H1."
                  if not uncertain else
                  "⚠️ Направление новости заранее неопределимо; после публикации нужна подтверждённая реакция M15/H1.")
    else:
        status = "⚠️ Новость может увеличить волатильность; направление подтверждаем реакцией закрытой свечи."
    return {
        "confidence": adjusted,
        "headline": "📰 Новости, способные повлиять на пару:",
        "lines": lines,
        "status": status,
    }


def _neutral_image(symbol: str, module: str, by_tf: dict, reason: str) -> io.BytesIO:
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1200, 720
    image = Image.new("RGB", (width, height), "#10131d")
    draw = ImageDraw.Draw(image, "RGBA")
    try:
        title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
        normal = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    except OSError:
        title = ImageFont.load_default(size=28)
        normal = ImageFont.load_default(size=22)
    draw.text((70, 55), f"{symbol} · {module}", fill="#f1f5fb", font=title)
    bars = closed_candles(by_tf.get("H1") or [], 60)[-36:]
    if bars:
        left, right, top, bottom = 70, 1130, 130, 560
        lo, hi = min(x.low for x in bars), max(x.high for x in bars)
        span = max(hi-lo, 1e-9)
        xstep = (right-left)/max(1, len(bars)-1)
        points = [(left+i*xstep, bottom-(bar.close-lo)/span*(bottom-top)) for i, bar in enumerate(bars)]
        draw.line(points, fill="#8d98aa", width=4)
        draw.line((left, (top+bottom)/2, right, (top+bottom)/2), fill="#ffd44d80", width=2)
    draw.rounded_rectangle((235, 275, 965, 425), radius=18, fill="#202636ee", outline="#ffd44d", width=3)
    draw.text((315, 310), "НЕЙТРАЛЬНО · НАДЁЖНОГО СЦЕНАРИЯ НЕТ", fill="#ffd44d", font=normal)
    draw.text((70, 635), reason, fill="#aeb7c6", font=normal)
    out = io.BytesIO()
    out.name = f"session_{module.lower().replace(' ', '_')}_{symbol.replace('/', '')}.png"
    image.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out


def _echo_report(symbol: str, by_tf: dict, events: list[newsmod.NewsEvent], hours: int,
                 current_name: str, next_name: str) -> dict:
    checkpoints = sorted(set((1, max(2, hours // 2), hours)))
    result = echo_projection.analyze(symbol, by_tf, checkpoints)
    news = _news_context(symbol, events, result["confidence"] if result else None)
    if result:
        side, icon = result["side"], ("🟢" if result["side"] == "LONG" else "🔴")
        values = result.get("horizons") or {}
        route = " · ".join(f"через {h}ч: {values.get(str(h), 0)}%" for h in checkpoints)
        confidence = news["confidence"]
        scenario = [f"Направление: {side} {icon}",
                    f"Техническая вероятность с учётом новостного риска: {confidence}%",
                    f"Контрольные точки: {route}",
                    f"Исторических аналогов: {result['sample']}"]
        image = echo_projection.render_chart(result, by_tf)
    else:
        scenario = ["Направление: НЕЙТРАЛЬНО 🟡",
                    "Вероятность: недостаточно надёжных исторических совпадений"]
        image = _neutral_image(symbol, "ЭХО", by_tf, "Недостаточно надёжных исторических аналогов")
    text = "\n".join([
        "━━━━━━━━━━━━━━━━━━", "🔭 ЭХО — ПРОГНОЗ ДО СЛЕДУЮЩЕЙ СЕССИИ", "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {symbol}", f"Период: {current_name} → {next_name} · около {hours} ч",
        *scenario, "", news["headline"], *news["lines"], news["status"], "",
        "⚠️ Это вероятностный технический сценарий, а не гарантия движения.", "━━━━━━━━━━━━━━━━━━",
    ])
    return {"text": text, "image": image}


def _pivot_report(symbol: str, by_tf: dict, events: list[newsmod.NewsEvent], hours: int,
                  current_name: str, next_name: str) -> dict:
    result = next_pivot_projection.analyze_symbol(symbol, by_tf)
    news = _news_context(symbol, events, result["probability"] if result else None)
    if result:
        side, icon = result["side"], ("🟢" if result["side"] == "LONG" else "🔴")
        reaction = "SHORT 🔴" if side == "LONG" else "LONG 🟢"
        kind = "ВЕРШИНЫ" if result["kind"] == "high" else "ОСНОВАНИЯ"
        decimals = 3 if "JPY" in symbol else 5
        inside_window = int(result["bars_low"]) <= hours
        scenario = [
            f"Текущее движение к зоне: {side} {icon}",
            f"Ожидаемая зона {kind}: {result['zone_low']:.{decimals}f}–{result['zone_high']:.{decimals}f}",
            f"Окно Pivot: через {result['bars_low']}–{result['bars_high']} закрытых H1",
            f"Попадает в текущий сессионный период: {'ДА' if inside_window else 'НЕТ'}",
            f"Вероятность с учётом новостного риска: {news['confidence']}%",
            f"Возможная реакция после зоны: {reaction} · только после подтверждения M15/H1",
        ]
        image = next_pivot_projection.render_chart(result, by_tf)
    else:
        scenario = ["Состояние: НЕЙТРАЛЬНО 🟡", "Надёжная следующая Pivot-зона пока не рассчитана"]
        image = _neutral_image(symbol, "СЛЕДУЮЩИЙ PIVOT", by_tf, "Недостаточно подтверждённых исторических Pivot")
    text = "\n".join([
        "━━━━━━━━━━━━━━━━━━", "🔭 СЛЕДУЮЩИЙ PIVOT — СЕССИОННАЯ ПРОЕКЦИЯ", "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {symbol}", f"Период: {current_name} → {next_name} · около {hours} ч",
        *scenario, "", news["headline"], *news["lines"], news["status"], "",
        "⚠️ Pivot — вероятная зона реакции, а не гарантированная точка разворота.", "━━━━━━━━━━━━━━━━━━",
    ])
    return {"text": text, "image": image}


def pending_reports(market: dict, events: list[newsmod.NewsEvent], state: dict) -> list[dict]:
    """Вернуть ещё не доставленные карточки текущей сессии в стабильном порядке."""
    session_id = briefing.briefing_id()
    delivered = state.setdefault("session_projection_delivered", {})
    current_name, next_name, hours = _session_context()
    reports = []
    for module in ("echo", "pivot"):
        for symbol in cfg.PAIRS:
            key = f"{session_id}|{module}|{symbol}"
            if delivered.get(key):
                continue
            by_tf = market.get(symbol) or {}
            try:
                report = (_echo_report(symbol, by_tf, events, hours, current_name, next_name)
                          if module == "echo" else
                          _pivot_report(symbol, by_tf, events, hours, current_name, next_name))
                report["key"] = key
                reports.append(report)
            except Exception:
                # Одна повреждённая серия или картинка не отменяет отчёты по
                # остальным парам. Эта карточка останется недоставленной и
                # будет заново рассчитана при следующем сканировании.
                log.exception("SESSION_REPORT_BUILD_FAILED module=%s symbol=%s session=%s",
                              module, symbol, session_id)
    return reports


def mark_delivered(state: dict, key: str) -> None:
    delivered = state.setdefault("session_projection_delivered", {})
    delivered[key] = datetime.now(timezone.utc).timestamp()
    if len(delivered) > 100:
        state["session_projection_delivered"] = dict(list(delivered.items())[-70:])
