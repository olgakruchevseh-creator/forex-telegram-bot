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
                  hours: int | None = None, confidence_floor: int = 50) -> dict:
    relevant = _pair_events(symbol, events)
    if hours is not None:
        from datetime import timedelta
        now_utc = datetime.now(timezone.utc)
        end_utc = now_utc + timedelta(hours=max(1, hours))
        relevant = [e for e in relevant if now_utc <= e.dt_utc <= end_utc]
    if not relevant:
        if newsmod.calendar_status() == "unavailable" and not events:
            return {
                "confidence": confidence,
                "headline": "📰 Календарь временно недоступен — новости по паре не подтверждены.",
                "lines": [],
                "status": "Технический сценарий без проверки новостного окна.",
                "risk": "UNKNOWN", "events": [],
            }
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
    adjusted = None if confidence is None else max(int(confidence_floor), int(confidence) - penalty)
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
    # Резервируем справа место под будущую полную ценовую шкалу/подписи.
    left, right, top, bottom = 80, 1045, 100, 590
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


def compact_photo_caption(report: dict, kind: str, limit: int = 1000) -> str:
    """Keep Echo/Next Pivot attached to its chart even when the full report is long.

    Telegram photo captions are shorter than ordinary messages.  The session
    projection is informational, so prefer one self-contained photo card over a
    detached second text message.  Full text remains in ``report["text"]`` for
    logs/tests; delivery uses this compact caption.
    """
    text = str((report or {}).get("text") or "")
    if len(text) <= limit:
        return text
    lines = [line.strip() for line in text.splitlines() if line.strip() and line.strip("━")]
    wanted = (
        "💱 Пара:", "Период:", "Режим расчёта:", "Направление к границе",
        "Вероятность направления:", "Достаточность данных", "Форма ожидаемого пути:",
        "Первичное движение к Pivot:", "Ожидаемая зона", "Окно Pivot", "Статус Pivot:",
        "Вероятность первичного", "Ожидаемая реакция", "Связка Echo → Pivot:",
        "📰", "⚠️ Это вероятностный",
    )
    title = "🔭 ЭХО — ПРОГНОЗ ДО СЛЕДУЮЩЕЙ СЕССИИ" if kind == "echo" else "🎯 NEXT PIVOT — ПРОГНОЗ ДО СЛЕДУЮЩЕЙ СЕССИИ"
    picked = [title]
    for line in lines:
        if line == title:
            continue
        if any(line.startswith(prefix) for prefix in wanted):
            picked.append(line)
    caption = "\n".join(dict.fromkeys(picked))
    if len(caption) <= limit:
        return caption
    # Last-resort safe trim: never create a detached text continuation merely
    # because optional explanatory lines exceeded the caption budget.
    suffix = "\n⚠️ Информационный сценарий, не торговый сигнал."
    return caption[: max(0, limit-len(suffix)-1)].rstrip() + suffix


def _echo_report(symbol: str, by_tf: dict, events: list[newsmod.NewsEvent], hours: int,
                 current_name: str, next_name: str, strength: dict | None = None,
                 dxy_bias: int = 0, session_side: str | None = None) -> dict:
    checkpoints = sorted(set((
        max(1, int(round(hours * 0.25))),
        max(1, int(round(hours * 0.50))),
        max(1, int(round(hours * 0.75))),
        hours,
    )))
    result = echo_projection.analyze(
        symbol, by_tf, checkpoints, strength=strength or {}, dxy_bias=int(dxy_bias or 0))
    news = _news_context(symbol, events, result["direction_probability"] if result else None, hours)
    if result:
        weak = bool(result.get("weak") or result.get("estimated"))
        side = result["side"]
        icon = "🟡" if weak else ("🟢" if side == "LONG" else "🔴")
        direction_probability = news["confidence"]
        data_quality = int(result.get("data_quality", 0))
        trajectory_available = bool(result.get("trajectory_available")) and not weak
        high_news = [e for e in news.get("events", []) if e.impact == "HIGH"]
        if high_news:
            news_split = (f"до {high_news[0].local_hm} — технический сценарий; "
                          "после новости — участок повышенного риска")
        elif news.get("risk") == "MEDIUM":
            news_split = "средние новости учтены снижением вероятности направления"
        else:
            news_split = "значимого новостного разрыва нет"

        analog_pct = int(result.get("raw_confidence") or direction_probability)
        context_support = int(result.get("context_support") or 0)
        mode = ("КОНТЕКСТНАЯ ОЦЕНКА · СЛАБАЯ" if result.get("estimated")
                else ("ИСТОРИЧЕСКАЯ ПРОЕКЦИЯ · СЛАБАЯ" if weak else "ИСТОРИЧЕСКАЯ ПРОЕКЦИЯ"))
        scenario = [
            f"Режим расчёта: {mode}",
            f"Направление к границе следующей сессии: {side} {icon}",
            f"Вероятность направления: {direction_probability}%",
            f"Аналоги / контекст: {analog_pct}% / {context_support:+d}",
            f"Достаточность данных для траектории: {data_quality}%",
            f"Новостной слой: {news_split}",
        ]
        if trajectory_available:
            values = result.get("horizons") or {}
            route = " · ".join(f"+{h}ч: {values.get(str(h), 0)}%" for h in checkpoints)
            path = result.get("expected_by_horizon") or {}
            signed = [float(path.get(str(h), 0.0)) for h in checkpoints]
            turns = sum(1 for a, b in zip(signed, signed[1:])
                        if (b-a) * (1 if side == "LONG" else -1) < 0)
            scenario.extend([
                f"Форма ожидаемого пути: {'волна с вероятным откатом' if turns else 'направленное движение'}",
                f"Траектория по историческим аналогам: {route}",
                f"Исторических аналогов: {result['sample']}",
            ])
        else:
            scenario.extend([
                "Статус траектории: 🟡 НЕДОСТАТОЧНО ДАННЫХ ДЛЯ ТОЧЕК +N Ч",
                "Внутрисессионный путь не интерполируется: показано только контекстное направление.",
                "Исторических аналогов недостаточно для формы пути.",
            ])
        if session_side and side and session_side != side:
            scenario.append(
                f"Сверка с брифингом: тезис сессии {session_side}; Эхо смотрит {side}. "
                "Это оценка к границе следующей сессии, не смена рабочего тезиса."
            )
        elif session_side and side == session_side:
            scenario.append(f"Сверка с брифингом: совпадает с тезисом сессии {session_side}.")
        chart_result = dict(result)
        chart_result["confidence"] = direction_probability
        chart_result["weak"] = not trajectory_available
        chart_result["news_risk"] = news.get("risk", "NONE")
        chart_result["news_markers"] = [
            {"time": e.local_hm, "impact": e.impact, "currency": e.currency,
             "title": newsmod.translate_title(e.title)} for e in news.get("events", [])
        ]
        try:
            image = (echo_projection.render_chart(chart_result, by_tf) if trajectory_available
                     else _minimal_echo_ray(symbol, by_tf, side))
        except Exception:
            log.exception("ECHO_CHART_FAILED symbol=%s; using minimal ray", symbol)
            image = _minimal_echo_ray(symbol, by_tf, side)
    else:
        scenario = ["Направление: НЕЙТРАЛЬНО 🟡",
                    "Вероятность: недостаточно надёжных исторических совпадений"]
        if session_side:
            scenario.append(
                f"Сверка с брифингом: тезис сессии {session_side} сохраняется; "
                "нейтральное Эхо не спорит с ним и не даёт второго голоса."
            )
        image = _neutral_image(symbol, "ЭХО", by_tf, "Недостаточно данных для надёжного сценария")
    text = "\n".join([
        "━━━━━━━━━━━━━━━━━━", "🔭 ЭХО — ПРОГНОЗ ДО СЛЕДУЮЩЕЙ СЕССИИ", "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {symbol}", f"Период: {current_name} → {next_name} · около {hours} ч",
        *scenario, "", news["headline"], *news["lines"], news["status"], "",
        "⚠️ Это вероятностный технический сценарий, а не торговый сигнал.", "━━━━━━━━━━━━━━━━━━",
    ])
    return {"text": text, "image": image, "result": result}

def _pivot_report(symbol: str, by_tf: dict, events: list[newsmod.NewsEvent], hours: int,
                  current_name: str, next_name: str, strength: dict | None = None,
                  dxy_bias: int = 0, session_side: str | None = None) -> dict:
    result = next_pivot_projection.analyze_session_symbol(symbol, by_tf, hours, strength=strength or {})
    news = _news_context(symbol, events, result["probability"] if result else None, hours, confidence_floor=30)
    if result:
        side = result["side"]
        probability = int(news["confidence"] or result["probability"])
        weak = bool(probability < 50 or result.get("estimated") or result.get("outside_session"))
        icon = "🟡" if weak else ("🟢" if side == "LONG" else "🔴")
        reaction = "SHORT" if side == "LONG" else "LONG"
        reaction_icon = "🔴" if reaction == "SHORT" else "🟢"
        kind = "ВЕРШИНЫ" if result["kind"] == "high" else "ОСНОВАНИЯ"
        decimals = 3 if "JPY" in symbol else 5
        mode = ("ОЦЕНОЧНАЯ СЕССИОННАЯ ПРОЕКЦИЯ" if result.get("estimated")
                else "СТАТИСТИЧЕСКАЯ PIVOT-ПРОЕКЦИЯ")
        conflict = " · ⚠️ есть структурное противоречие" if result.get("zigzag_conflict") else ""
        if result.get("pivot_active"):
            window_line = "Статус Pivot: зона уже активна · оценивается реакция от неё"
        elif result.get("outside_session"):
            window_line = (f"Статус Pivot: за пределами текущей сессии · статистическое окно "
                           f"{result['bars_low']}–{result['bars_high']} H1")
        else:
            hi = min(int(hours), int(result["bars_high"]))
            window_line = f"Окно Pivot в этой сессии: через {result['bars_low']}–{hi} закрытых H1"
        confirms = ", ".join(result.get("smc_confirmations") or []) or "нет свежего подтверждения"
        cautions = ", ".join(result.get("smc_cautions") or []) or "нет"
        zq = result.get("zone_quality") or {}
        if zq.get("confirmed"):
            zone_quality_line = (f"Качество Pivot-зоны: {int(zq.get('score', 50))}% · "
                                 f"исторических касаний {int(zq.get('touches', 0))} · "
                                 f"пробоев {int(zq.get('violations', 0))}")
        else:
            zone_quality_line = "Качество Pivot-зоны: новая/неподтверждённая область · без штрафа направления"

        # Echo + Pivot — последовательный сценарий, а не два конкурирующих сигнала.
        echo = echo_projection.analyze(
            symbol, by_tf, sorted(set((max(1, hours//2), hours))),
            strength=strength or {}, dxy_bias=int(dxy_bias or 0))
        echo_side = echo.get("side") if echo else None
        thesis = session_side or echo_side
        against_thesis = bool(thesis and side and thesis != side)
        muted = bool(weak or result.get("zigzag_conflict") or (not echo_side and against_thesis))
        if muted:
            icon = "🟡"
        if not echo_side and muted:
            link = (f"Эхо нейтрально; слабый Pivot {side} не читается как направление сессии"
                    + (f" и не спорит с тезисом {thesis}" if thesis else "")
                    + f". Зона — только магнит, затем возможна реакция {reaction}.")
        elif against_thesis:
            link = (f"Тезис сессии / Эхо {thesis}. Pivot описывает локальный крюк {side} к зоне, "
                    f"затем возможна реакция {reaction}. Это не второй торговый голос и не смена тезиса.")
        elif echo_side == side:
            link = f"Echo {echo_side} → первичное движение к Pivot {side} → после зоны возможна реакция {reaction}"
        elif echo_side:
            link = (f"Echo {echo_side} задаёт общий сессионный фон; Pivot ожидает первичное движение {side} "
                    f"к зоне, затем возможна реакция {reaction}. Это разные этапы сценария.")
        else:
            link = f"Первичное движение {side} к Pivot → после зоны возможна реакция {reaction}"

        primary = f"Первичное движение к Pivot: {side} {icon}"
        if muted and against_thesis:
            primary += f" · локальный крюк к зоне, не смена тезиса сессии ({thesis})"

        scenario = [
            f"Режим расчёта: {mode}",
            primary,
            *((["Статус: 🟡 СЛАБАЯ PIVOT-ГИПОТЕЗА · не отображается как полноценный LONG/SHORT-сигнал"] if (weak or muted) else [])),
            f"Ожидаемая зона {kind}: {result['zone_low']:.{decimals}f}–{result['zone_high']:.{decimals}f}",
            window_line,
            f"Сверка с ZigZag D1/H4/H1: {result.get('zigzag_check', 'нет данных')}{conflict}",
            zone_quality_line,
            f"SMC-подтверждения: {confirms}",
            f"SMC-предупреждения: {cautions}",
            f"Вероятность первичного движения к Pivot: {probability}%",
            f"Ожидаемая реакция после зоны: {reaction} {reaction_icon} · вероятность {result.get('reaction_score', 0)}% · только после M15/H1",
            f"Связка Echo → Pivot: {link}",
        ]
        chart_result = dict(result)
        chart_result["display_probability"] = probability
        chart_result["weak_projection"] = weak
        chart_result["news_risk"] = news.get("risk", "NONE")
        chart_result["news_markers"] = [
            {"time": e.local_hm, "impact": e.impact, "currency": e.currency,
             "title": newsmod.translate_title(e.title)} for e in news.get("events", [])
        ]
        image = next_pivot_projection.render_chart(chart_result, by_tf)
    else:
        scenario = ["Состояние: НЕЙТРАЛЬНО 🟡", "Надёжная следующая Pivot-зона пока не рассчитана"]
        image = _neutral_image(symbol, "СЛЕДУЮЩИЙ PIVOT", by_tf, "Недостаточно подтверждённых исторических Pivot")
    text = "\n".join([
        "━━━━━━━━━━━━━━━━━━", "🔭 СЛЕДУЮЩИЙ PIVOT — СЕССИОННАЯ ПРОЕКЦИЯ", "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {symbol}", f"Период: {current_name} → {next_name} · около {hours} ч",
        *scenario, "", news["headline"], *news["lines"], news["status"], "",
        "⚠️ Pivot — зона вероятной реакции. Первичное движение к зоне и реакция после неё — разные этапы сценария.", "━━━━━━━━━━━━━━━━━━",
    ])
    return {"text": text, "image": image, "result": result}

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
    dxy_bias = 0
    try:
        cached = getattr(briefing, "_DXY_CACHE", {}) or {}
        dxy_bias = int(briefing.effective_dxy_bias(cached.get("view")) or 0)
    except Exception:
        usd = float((strength or {}).get("USD") or 0)
        gap = float(getattr(cfg, "ECHO_STRENGTH_MIN_GAP", 0.04))
        dxy_bias = 1 if usd >= gap else (-1 if usd <= -gap else 0)
    session_sides: dict[str, str] = {}
    try:
        briefs = briefing.build_pair_briefs(market, strength or {}, events, datetime.now(timezone.utc))
        for brief in briefs:
            side = brief.side or briefing.technical_pair_side(brief)
            if side:
                session_sides[brief.symbol] = side
    except Exception:
        log.exception("SESSION_THESIS_CONTEXT_FAILED; Echo/Pivot без сверки с брифингом")
    reports = []
    modules = []
    if getattr(cfg, "ECHO_ENABLED", True):
        modules.append("echo")
    if getattr(cfg, "NEXT_PIVOT_ENABLED", True):
        modules.append("pivot")
    for module in modules:
        for symbol in cfg.PAIRS:
            key = f"{session_id}|{module}|{symbol}"
            if delivered.get(key):
                continue
            by_tf = market.get(symbol) or {}
            thesis = session_sides.get(symbol)
            try:
                report = (_echo_report(symbol, by_tf, events, hours, current_name, next_name, strength, dxy_bias, thesis)
                          if module == "echo" else
                          _pivot_report(symbol, by_tf, events, hours, current_name, next_name, strength, dxy_bias, thesis))
                report["key"] = key
                reports.append(report)
            except Exception:
                # Одна повреждённая серия или картинка не отменяет отчёты по
                # остальным парам. Эта карточка останется недоставленной и
                # будет заново рассчитана при следующем сканировании.
                log.exception("SESSION_REPORT_BUILD_FAILED module=%s symbol=%s session=%s",
                              module, symbol, session_id)
    return reports



def _side_badge(side: str | None) -> str:
    """Consistent visual direction badge for the combined Echo + Next Pivot card."""
    side = str(side or "").upper()
    if side == "LONG":
        return "🟢 LONG"
    if side == "SHORT":
        return "🔴 SHORT"
    return "🟡 НЕЙТРАЛЬНО"


def combined_pair_caption(bundle: dict, limit: int = 1000) -> str:
    """One concise caption for the Echo + Next Pivot album of one pair."""
    symbol = str(bundle.get("symbol") or "")
    echo = bundle.get("echo") or {}
    pivot = bundle.get("pivot") or {}
    er = echo.get("result") or {}
    pr = pivot.get("result") or {}
    lines = ["🔭 ЭХО + 🎯 СЛЕДУЮЩИЙ PIVOT — ЕДИНЫЙ СЦЕНАРИЙ", f"💱 Пара: {symbol}"]
    period = str(bundle.get("period") or "")
    if period:
        lines.append(f"Период: {period}")
    eside = er.get("side")
    pside = pr.get("side")
    if eside:
        lines.append(f"Эхо · фон к следующей сессии: {_side_badge(eside)} · вероятность {er.get('direction_probability', '—')}%")
    else:
        lines.append(f"Эхо · фон к следующей сессии: {_side_badge(None)}")
    if pr:
        decimals = 3 if "JPY" in symbol else 5
        kind = "ВЕРШИНЫ" if pr.get("kind") == "high" else "ОСНОВАНИЯ"
        lines.append(f"Pivot · путь к зоне: {_side_badge(pside)} · {kind} {pr.get('zone_low', 0):.{decimals}f}–{pr.get('zone_high', 0):.{decimals}f}")
        reaction = "SHORT" if pside == "LONG" else ("LONG" if pside == "SHORT" else "НЕЙТРАЛЬНО")
        if eside and pside and eside != pside:
            lines.append(f"Связка: Echo {_side_badge(eside)} — общий фон; Pivot {_side_badge(pside)} — локальный крюк к зоне, не смена тезиса.")
        elif eside and pside:
            lines.append(f"Связка: Echo {_side_badge(eside)} → движение к Pivot {_side_badge(pside)} → после зоны возможна реакция {_side_badge(reaction)}.")
        else:
            lines.append(f"Связка: Pivot — локальная зона; реакция {_side_badge(reaction)} учитывается только после подтверждения M15/H1.")
    else:
        lines.append("Pivot: надёжная следующая зона пока не рассчитана.")
    lines.append("🖼 1/2 — Echo · 2/2 — Next Pivot")
    lines.append("⚠️ Информационный вероятностный сценарий, не торговый сигнал.")
    text = "\n".join(lines)
    return text if len(text) <= limit else text[:limit-1].rstrip() + "…"


def pending_report_bundles(market: dict, events: list[newsmod.NewsEvent], state: dict) -> list[dict]:
    """Seven pair albums instead of fourteen unrelated notifications."""
    session_id = briefing.briefing_id()
    delivered = state.setdefault("session_projection_delivered", {})
    current_name, next_name, hours = _session_context()
    try:
        h1_market = {symbol: (market.get(symbol) or {}).get("H1") or [] for symbol in cfg.PAIRS}
        strength = currency_strength(h1_market, 8)
    except Exception:
        log.exception("ECHO_STRENGTH_CONTEXT_FAILED; continuing without strength")
        strength = {}
    try:
        cached = getattr(briefing, "_DXY_CACHE", {}) or {}
        dxy_bias = int(briefing.effective_dxy_bias(cached.get("view")) or 0)
    except Exception:
        usd = float((strength or {}).get("USD") or 0)
        gap = float(getattr(cfg, "ECHO_STRENGTH_MIN_GAP", 0.04))
        dxy_bias = 1 if usd >= gap else (-1 if usd <= -gap else 0)
    session_sides = {}
    try:
        briefs = briefing.build_pair_briefs(market, strength or {}, events, datetime.now(timezone.utc))
        for brief in briefs:
            side = brief.side or briefing.technical_pair_side(brief)
            if side:
                session_sides[brief.symbol] = side
    except Exception:
        log.exception("SESSION_THESIS_CONTEXT_FAILED; Echo/Pivot без сверки с брифингом")

    bundles = []
    for symbol in cfg.PAIRS:
        key = f"{session_id}|echo_pivot|{symbol}"
        legacy_echo = f"{session_id}|echo|{symbol}"
        legacy_pivot = f"{session_id}|pivot|{symbol}"
        if delivered.get(key) or (delivered.get(legacy_echo) and delivered.get(legacy_pivot)):
            continue
        by_tf = market.get(symbol) or {}
        thesis = session_sides.get(symbol)
        try:
            echo = (_echo_report(symbol, by_tf, events, hours, current_name, next_name, strength, dxy_bias, thesis)
                    if getattr(cfg, "ECHO_ENABLED", True) else None)
            pivot = (_pivot_report(symbol, by_tf, events, hours, current_name, next_name, strength, dxy_bias, thesis)
                     if getattr(cfg, "NEXT_PIVOT_ENABLED", True) else None)
            if not echo and not pivot:
                continue
            bundle = {"key": key, "symbol": symbol, "echo": echo, "pivot": pivot,
                      "period": f"{current_name} → {next_name} · около {hours} ч"}
            bundle["caption"] = combined_pair_caption(bundle)
            bundles.append(bundle)
        except Exception:
            log.exception("SESSION_BUNDLE_BUILD_FAILED symbol=%s session=%s", symbol, session_id)
    return bundles

def mark_delivered(state: dict, key: str) -> None:
    delivered = state.setdefault("session_projection_delivered", {})
    delivered[key] = datetime.now(timezone.utc).timestamp()
    if len(delivered) > 100:
        state["session_projection_delivered"] = dict(list(delivered.items())[-70:])
