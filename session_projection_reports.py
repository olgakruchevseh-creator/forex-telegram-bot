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
from analysis import closed_candles, currency_strength

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


def _news_context(symbol: str, events: list[newsmod.NewsEvent], confidence: int | None,
                  hours: int | None = None) -> dict:
    relevant = _pair_events(symbol, events)
    if hours is not None:
        from datetime import timedelta
        now_utc = datetime.now(timezone.utc)
        end_utc = now_utc + timedelta(hours=max(1, hours))
        relevant = [e for e in relevant if now_utc <= e.dt_utc <= end_utc]
    if not relevant:
        return {
            "confidence": confidence,
            "headline": "📰 До следующей сессии значимых новостей по паре нет.",
            "lines": [],
            "status": "Технический сценарий не зависит от запланированной новости.",
            "risk": "NONE", "events": [],
        }
    penalty = 0
    lines = []
    has_high = False
    uncertain = False
    seen_lines = set()
    for event in relevant:
        icon = "🔴" if event.impact == "HIGH" else ("🟠" if event.impact == "MEDIUM" else "🟡")
        importance = "высокая" if event.impact == "HIGH" else ("средняя" if event.impact == "MEDIUM" else "наблюдение")
        line = f"{icon} {event.local_hm} · {event.currency} · {newsmod.translate_title(event.title)} · {importance}"
        if line not in seen_lines:
            lines.append(line)
            seen_lines.add(line)
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
        "risk": "HIGH" if has_high else "MEDIUM",
        "events": relevant,
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



def _minimal_echo_ray(symbol: str, by_tf: dict, side: str) -> io.BytesIO:
    """Last-resort image; rendering failure must not block a session card."""
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new("RGB", (1200, 720), "#10131d")
    draw = ImageDraw.Draw(image, "RGBA")
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 25)
    except OSError:
        font = ImageFont.load_default(size=25)
    bars = closed_candles(by_tf.get("H1") or [], 60)[-30:]
    left, right, top, bottom = 80, 1120, 100, 590
    if bars:
        lo, hi = min(b.low for b in bars), max(b.high for b in bars); span=max(hi-lo,1e-9)
        step=(right-left)*.72/max(1,len(bars)-1)
        pts=[(left+i*step,bottom-(b.close-lo)/span*(bottom-top)) for i,b in enumerate(bars)]
        if len(pts)>1: draw.line(pts, fill="#8d98aa", width=3)
        sx,sy=pts[-1]
    else: sx,sy=820,350
    ex=min(right,sx+230); ey=max(top,min(bottom,sy+(-90 if side=="LONG" else 90)))
    color="#42e889" if side=="LONG" else "#ff6575"
    draw.line((sx,sy,ex,ey),fill=color,width=7); draw.ellipse((ex-7,ey-7,ex+7,ey+7),fill=color)
    draw.text((80,35),f"{symbol} · ЭХО {side} · резервный луч",fill="#f1f5fb",font=font)
    out=io.BytesIO(); out.name=f"echo_minimal_{symbol.replace('/', '')}.png"
    image.save(out,format="PNG"); out.seek(0); return out

def _echo_report(symbol: str, by_tf: dict, events: list[newsmod.NewsEvent], hours: int,
                 current_name: str, next_name: str, strength: dict | None = None) -> dict:
    # Вся оставшаяся сессия разбита на контрольные участки.
    # Последняя точка всегда совпадает с границей следующей сессии.
    checkpoints = sorted(set((
        max(1, int(round(hours * 0.25))),
        max(1, int(round(hours * 0.50))),
        max(1, int(round(hours * 0.75))),
        hours,
    )))
    result = echo_projection.analyze(symbol, by_tf, checkpoints, strength=strength or {})
    weak = False
    news = _news_context(symbol, events, result["confidence"] if result else None, hours)
    if result:
        side, icon = result["side"], ("🟢" if result["side"] == "LONG" else "🔴")
        values = result.get("horizons") or {}
        route = " · ".join(f"+{h}ч: {values.get(str(h), 0)}%" for h in checkpoints)
        confidence = news["confidence"]
        endpoint = result.get("session_end_probability", values.get(str(hours), 0))
        path = result.get("expected_by_horizon") or {}
        signed = [float(path.get(str(h), 0.0)) for h in checkpoints]
        turns = sum(1 for a, b in zip(signed, signed[1:])
                    if (b-a) * (1 if side == "LONG" else -1) < 0)
        shape = "волна с вероятным откатом" if turns else "направленное движение"
        high_news = [e for e in news.get("events", []) if e.impact == "HIGH"]
        if high_news:
            first_news = high_news[0]
            news_split = (f"до {first_news.local_hm} — техническая траектория; "
                          "после новости — участок повышенного риска")
        elif news.get("risk") == "MEDIUM":
            news_split = "средние новости учтены снижением надёжности траектории"
        else:
            news_split = "значимого новостного разрыва нет"
        scenario = [f"Направление до следующей сессии: {side} {icon}",
                    f"Вероятность направления у границы сессии: {endpoint}%",
                    f"Форма ожидаемого пути: {shape}",
                    f"Новостной слой: {news_split}",
                    *((["Статус: 🟡 СЛАБАЯ ОЦЕНОЧНАЯ ТРАЕКТОРИЯ"] if weak else [])),
                    f"Общая надёжность с учётом контекста/новостей: {confidence}%",
                    f"Траектория внутри сессии: {route}",
                    f"Исторических аналогов: {result['sample']}"]
        chart_result = dict(result)
        chart_result["weak"] = weak
        chart_result["news_risk"] = news.get("risk", "NONE")
        chart_result["news_markers"] = [
            {"time": e.local_hm, "impact": e.impact, "currency": e.currency,
             "title": newsmod.translate_title(e.title)}
            for e in news.get("events", [])
        ]
        try:
            image = echo_projection.render_chart(chart_result, by_tf)
        except Exception:
            log.exception("ECHO_CHART_FAILED symbol=%s; using minimal ray", symbol)
            image = _minimal_echo_ray(symbol, by_tf, side)
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
    try:
        h1_market = {symbol: (market.get(symbol) or {}).get("H1") or [] for symbol in cfg.PAIRS}
        strength = currency_strength(h1_market, 8)
    except Exception:
        log.exception("ECHO_STRENGTH_CONTEXT_FAILED; continuing without strength")
        strength = {}
    reports = []
    for module in ("echo", "pivot"):
        for symbol in cfg.PAIRS:
            key = f"{session_id}|{module}|{symbol}"
            if delivered.get(key):
                continue
            by_tf = market.get(symbol) or {}
            try:
                report = (_echo_report(symbol, by_tf, events, hours, current_name, next_name, strength)
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
