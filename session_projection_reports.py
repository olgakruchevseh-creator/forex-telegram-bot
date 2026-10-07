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
import zigzag_scanner
import session_pullback_consensus
import pair_character_matrix
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
        return "🟢 ЛОНГ"
    if side == "SHORT":
        return "🔴 ШОРТ"
    return "🟡 НЕЙТРАЛЬНО"


def combined_pair_caption(bundle: dict, limit: int = 1000) -> str:
    """Compact vertical stack first; plain-text explanation below it."""
    symbol = str(bundle.get("symbol") or "")
    echo = bundle.get("echo") or {}
    pivot = bundle.get("pivot") or {}
    zz = bundle.get("zigzag") or {}
    er = echo.get("result") or {}
    pr = pivot.get("result") or {}
    eside = er.get("side")
    pside = pr.get("side")

    # Telegram scan block: exactly one item per line.  Direction circles are
    # intentionally restricted to these three engine rows.
    lines = [f"Пара: {symbol}"]
    if eside:
        lines.append(f"Эхо: {_side_badge(eside)} · вероятность {er.get('direction_probability', '—')}%")
    else:
        lines.append(f"Эхо: {_side_badge(None)}")

    if pr:
        decimals = 3 if "JPY" in symbol else 5
        kind = "ВЕРШИНЫ" if pr.get("kind") == "high" else "ОСНОВАНИЯ"
        lines.append(f"Next Pivot: {_side_badge(pside)} · {kind} {pr.get('zone_low', 0):.{decimals}f}–{pr.get('zone_high', 0):.{decimals}f}")
    else:
        lines.append(f"Next Pivot: {_side_badge(None)} · надёжная следующая зона пока не рассчитана")

    zdir = int((zz.get("zigzag_directions") or {}).get("H1", 0) or 0)
    zside = "LONG" if zdir > 0 else ("SHORT" if zdir < 0 else None)
    zlow, zhigh = int(zz.get("duration_low") or 0), int(zz.get("duration_high") or 0)
    if zlow and zhigh:
        zwindow = str(zlow) if zlow == zhigh else f"{zlow}–{zhigh}"
        lines.append(f"Adaptive ZigZag: {_side_badge(zside)} · ≈ {zwindow} закрытых H1-свечей до вероятного угла")
    else:
        lines.append(f"Adaptive ZigZag: {_side_badge(zside)} · окно до следующего угла пока без достаточной статистики")

    # Everything below the engine stack is deliberately emoji-free.
    lines.append("")
    period = str(bundle.get("period") or "")
    if period:
        lines.append(f"Период: {period}")
    character = bundle.get("pair_character") or {}
    if character:
        lines.append(pair_character_matrix.compact_text(character))
    pullback = bundle.get("pullback_consensus") or {}
    if pullback.get("text"):
        lines.append(str(pullback["text"]))
    if pr:
        reaction = "SHORT" if pside == "LONG" else ("LONG" if pside == "SHORT" else None)
        reaction_text = {"LONG": "ЛОНГ", "SHORT": "ШОРТ"}.get(reaction, "НЕЙТРАЛЬНО")
        echo_text = {"LONG": "ЛОНГ", "SHORT": "ШОРТ"}.get(str(eside or "").upper(), "НЕЙТРАЛЬНО")
        pivot_text = {"LONG": "ЛОНГ", "SHORT": "ШОРТ"}.get(str(pside or "").upper(), "НЕЙТРАЛЬНО")
        if eside and pside and eside != pside:
            lines.append(f"Связка: Echo {echo_text} — общий фон; Pivot {pivot_text} — локальный крюк к зоне, не смена тезиса.")
        elif eside and pside:
            lines.append(f"Связка: Echo {echo_text} → движение к Pivot {pivot_text} → после зоны возможна реакция {reaction_text}.")
        else:
            lines.append(f"Связка: Pivot — локальная зона; реакция {reaction_text} учитывается только после подтверждения M15/H1.")
    lines.append("Единый график: Adaptive ZigZag + Echo + Next Pivot")
    lines.append("Информационный вероятностный сценарий, не торговый сигнал.")
    text = "\n".join(lines)
    return text if len(text) <= limit else text[:limit-1].rstrip() + "…"



def render_unified_scenario_chart(bundle: dict, by_tf: dict) -> io.BytesIO:
    """One H1 canvas: confirmed Adaptive ZigZag + Echo path + Next Pivot zone/reaction.

    The layers keep their roles separate: ZigZag is confirmed structure, Echo is a
    probabilistic session path, and Pivot is a target/reaction zone.  No layer is
    allowed to manufacture a trading signal for another one.
    """
    from PIL import Image, ImageDraw, ImageFont
    from analysis import closed_candles, zigzag, atr

    symbol = str(bundle.get("symbol") or "")
    by_tf = by_tf or {}
    bars = closed_candles(by_tf.get("H1") or [], 60)[-40:]
    if len(bars) < 8:
        raise ValueError("Unified Echo/Pivot/ZigZag chart requires closed H1 candles")
    echo = ((bundle.get("echo") or {}).get("result") or {})
    pivot = ((bundle.get("pivot") or {}).get("result") or {})
    zz = bundle.get("zigzag") or zigzag_scanner.analyze_symbol(symbol, by_tf)

    width, height = 1200, 720
    image = Image.new("RGB", (width, height), "#10131d")
    draw = ImageDraw.Draw(image, "RGBA")
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
    except OSError:
        font = ImageFont.load_default(size=22); small = ImageFont.load_default(size=17)

    left, right, top, bottom = 75, 1045, 82, 620
    # Give the structural projection enough room for its first estimated turn.
    # The session horizon still controls Echo; ZigZag may extend a few H1 bars
    # farther when its own historical wave-duration model requires it.
    session_future = max(8, min(14, int(echo.get("session_hours") or 8)))
    zz_low = int(zz.get("duration_low") or 0)
    zz_high = int(zz.get("duration_high") or 0)
    zz_mid = max(1, int(round((zz_low + zz_high) / 2))) if zz_high else 0
    future = max(session_future, min(18, zz_high + 4 if zz_high else session_future))
    total = len(bars) + future
    current = float(bars[-1].close)
    av = float(echo.get("atr") or atr(bars, 14) or max(current*.0005, 1e-8))
    expected = echo.get("expected_by_horizon") or {}
    echo_points = [(0, current)]
    for h in sorted(int(k) for k in expected if str(k).isdigit()):
        if h <= session_future:
            echo_points.append((h, current + av*float(expected.get(str(h), 0))))
    if len(echo_points) == 1 and echo.get("side"):
        sign = 1 if echo.get("side") == "LONG" else -1
        echo_points.append((session_future, current + sign*av*.8))

    # Adaptive ZigZag forward geometry.  This is deliberately separate from
    # Echo/Pivot: it estimates the next structural corner from the pair's own
    # completed H1 swing lengths/amplitudes, then shows a possible opposite leg.
    # Both legs are projections only and never become confirmed swing points.
    zz_swings = zigzag(bars, float(cfg.ZIGZAG_PCT.get("H1", .18)), cfg.ZIGZAG_MIN_BARS)
    zz_projection = []
    if zz_swings and zz_mid:
        amplitudes = [abs(b.price-a.price) for a,b in zip(zz_swings, zz_swings[1:]) if b.index > a.index]
        if amplitudes:
            from statistics import median
            typical_amp = float(median(amplitudes[-10:]))
            last = zz_swings[-1]
            next_sign = 1 if last.kind == "low" else -1
            corner_price = float(last.price) + next_sign * typical_amp
            # A projected corner must remain beyond the current close in the
            # expected direction; otherwise the already-travelled part of the
            # unfinished leg would create a visually backwards forecast.
            corner_price = max(corner_price, current + av*.35) if next_sign > 0 else min(corner_price, current-av*.35)
            corner_h = min(future-3, max(1, zz_mid))
            reverse_h = min(future, corner_h + max(3, min(6, int(round(corner_h*.55)))))
            reverse_price = corner_price - next_sign * max(typical_amp*.62, av*.55)
            zz_projection = [(0, current), (corner_h, corner_price), (reverse_h, reverse_price)]

    prices = [v for b in bars for v in (b.low,b.high)] + [v for _,v in echo_points] + [v for _,v in zz_projection]
    if pivot:
        prices += [float(pivot.get("zone_low", current)), float(pivot.get("zone_high", current))]
        sign = 1 if pivot.get("side") == "LONG" else -1
        prices.append((float(pivot.get("zone_low", current))+float(pivot.get("zone_high", current)))/2 - sign*av*.9)
    lo, hi = min(prices), max(prices); pad=max((hi-lo)*.09,av*.25); lo-=pad; hi+=pad
    def x_at(i): return left + i/max(1,total-1)*(right-left)
    def y_at(v): return bottom-(v-lo)/max(1e-12,hi-lo)*(bottom-top)

    decimals = 3 if "JPY" in symbol else 5
    for row in range(6):
        y=top+row*(bottom-top)/5; draw.line((left,y,right,y),fill="#293143",width=1)
        draw.text((right+12,y-10),f"{hi-row*(hi-lo)/5:.{decimals}f}",fill="#9aa4b5",font=small)
    cw=max(4,int((right-left)/total*.55))
    for i,b in enumerate(bars):
        x=x_at(i); c="#37d67a" if b.close>=b.open else "#ff5c6c"
        draw.line((x,y_at(b.high),x,y_at(b.low)),fill=c,width=2)
        y1,y2=y_at(b.open),y_at(b.close); draw.rectangle((x-cw/2,min(y1,y2),x+cw/2,max(y1,y2)+1),fill=c)

    # Adaptive ZigZag: confirmed structure is solid white; its forward route is
    # white dotted geometry so it cannot be confused with red/green Echo.
    zpts=[(x_at(p.index),y_at(p.price),p) for p in zz_swings]
    if len(zpts)>=2: draw.line([(x,y) for x,y,_ in zpts],fill="#f1f5fb",width=4)
    ph=pl=None
    for x,y,p in zpts:
        if p.kind=="high": label="H" if ph is None else ("HH" if p.price>ph else "LH"); ph=p.price
        else: label="L" if pl is None else ("HL" if p.price>pl else "LL"); pl=p.price
        draw.ellipse((x-5,y-5,x+5,y+5),fill="#f1f5fb")
        draw.text((x-11,y-25 if p.kind=="high" else y+8),label,fill="#f1f5fb",font=small)

    def dotted(a, b, color, width=4, steps=18):
        for n in range(0, steps, 2):
            t1=n/steps; t2=min(1,(n+1)/steps)
            draw.line((a[0]+(b[0]-a[0])*t1,a[1]+(b[1]-a[1])*t1,
                       a[0]+(b[0]-a[0])*t2,a[1]+(b[1]-a[1])*t2),fill=color,width=width)

    if zpts:
        # First connect the last confirmed extremum to NOW; this segment is
        # unfinished history and therefore grey, not a new confirmed pivot.
        a=zpts[-1][:2]; b=(x_at(len(bars)-1),y_at(current))
        dotted(a,b,"#8f99aa",3,12)
    if len(zz_projection) >= 3:
        zp=[(x_at(len(bars)-1+h),y_at(v)) for h,v in zz_projection]
        dotted(zp[0],zp[1],"#f1f5fb",4)
        dotted(zp[1],zp[2],"#f1f5fb",4)
        cx,cy=zp[1]
        draw.ellipse((cx-6,cy-6,cx+6,cy+6),outline="#f1f5fb",width=2)
        if zz_low and zz_high:
            draw.text((max(left,cx-95),max(top,cy-31)),f"≈ {zz_low}–{zz_high} H1 до угла",fill="#f1f5fb",font=small)
        draw.text((min(right-210,zp[2][0]-80),max(top,zp[2][1]+8)),"возможное продолжение",fill="#cbd3df",font=small)

    boundary=x_at(len(bars)-1); draw.line((boundary,top,boundary,bottom),fill="#707b8d",width=2)
    draw.text((max(left,boundary-72),bottom-25),"СЕЙЧАС",fill="#b9c3d3",font=small)

    # Echo: dotted probabilistic route.
    if len(echo_points)>=2:
        ecol="#42e889" if echo.get("side")=="LONG" else "#ff6575"
        pts=[(x_at(len(bars)-1+h),y_at(v)) for h,v in echo_points]
        for a,b in zip(pts,pts[1:]):
            for n in range(0,14,2):
                t1=n/14;t2=min(1,(n+1)/14);draw.line((a[0]+(b[0]-a[0])*t1,a[1]+(b[1]-a[1])*t1,a[0]+(b[0]-a[0])*t2,a[1]+(b[1]-a[1])*t2),fill=ecol,width=5)
        draw.text((boundary+12,top+8),f"ЭХО {echo.get('side','—')} · {echo.get('direction_probability',echo.get('confidence','—'))}%",fill=ecol,font=small)

    # Pivot: zone + route into it + projected reaction after touch.
    if pivot:
        zl,zh=float(pivot["zone_low"]),float(pivot["zone_high"]); mid=(zl+zh)/2
        low_b=max(1,int(pivot.get("bars_low") or 1)); high_b=max(low_b,int(pivot.get("bars_high") or low_b+2)); high_b=min(future,high_b)
        zx1=x_at(len(bars)-1+min(future,low_b)); zx2=x_at(len(bars)-1+max(min(future,high_b),min(future,low_b)+1))
        draw.rectangle((zx1,y_at(zh),zx2,y_at(zl)),fill="#5ca8ff2b",outline="#8cc8ff",width=2)
        pc="#42e889" if pivot.get("side")=="LONG" else "#ff6575"
        target=((zx1+zx2)/2,y_at(mid)); start=(boundary,y_at(current))
        draw.line((start[0],start[1],target[0],target[1]),fill=pc,width=4)
        sign=1 if pivot.get("side")=="LONG" else -1; reaction=mid-sign*av*.9
        end=(x_at(min(total-1,len(bars)-1+high_b+3)),y_at(reaction)); draw.line((target[0],target[1],end[0],end[1]),fill="#d889ff",width=4)
        kind="ВЕРШИНА" if pivot.get("kind")=="high" else "ОСНОВАНИЕ"
        draw.text((max(left,zx1-15),max(top+32,y_at(zh)-26)),f"PIVOT {kind}",fill="#8cc8ff",font=small)

    zdir=(zz.get("zigzag_directions") or {}).get("H1",0); zword="LONG" if zdir>0 else "SHORT" if zdir<0 else "RANGE"
    draw.text((left,24),f"{symbol} · H1 · ЭХО + NEXT PIVOT + ADAPTIVE ZIGZAG",fill="#f1f5fb",font=font)
    eta = f" · до вероятного угла ≈ {zz_low}–{zz_high} H1" if zz_low and zz_high else ""
    draw.text((left,51),f"ZigZag H1: {zword} · белый: структура + прогноз двумя ветвями{eta}",fill="#b9c3d3",font=small)
    draw.text((left,665),"Единый вероятностный сценарий · закрытые H1 · не торговая гарантия",fill="#9aa4b5",font=small)
    out=io.BytesIO(); out.name=f"echo_pivot_zigzag_{symbol.replace('/','')}.png"; image.save(out,format="PNG",optimize=True); out.seek(0); return out

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
            zz = zigzag_scanner.analyze_symbol(symbol, by_tf)
            character = pair_character_matrix.analyze(symbol, by_tf, strength)
            bundle = {"key": key, "symbol": symbol, "echo": echo, "pivot": pivot, "zigzag": zz,
                      "pair_character": character,
                      "period": f"{current_name} → {next_name} · около {hours} ч"}
            pb_states = state.setdefault("session_pullback_consensus", {})
            previous_pb = pb_states.get(symbol) or {}
            pullback = session_pullback_consensus.analyze(symbol, bundle, by_tf, previous_pb)
            bundle["pullback_consensus"] = pullback
            pb_states[symbol] = {"stage": pullback.get("stage"), "continuation": pullback.get("continuation")}
            if len(pb_states) > 20:
                state["session_pullback_consensus"] = {k: pb_states[k] for k in list(pb_states)[-20:]}
            bundle["caption"] = combined_pair_caption(bundle)
            bundle["image"] = render_unified_scenario_chart(bundle, by_tf)
            bundles.append(bundle)
        except Exception:
            log.exception("SESSION_BUNDLE_BUILD_FAILED symbol=%s session=%s", symbol, session_id)
    return bundles

def mark_delivered(state: dict, key: str) -> None:
    delivered = state.setdefault("session_projection_delivered", {})
    delivered[key] = datetime.now(timezone.utc).timestamp()
    if len(delivered) > 100:
        state["session_projection_delivered"] = dict(list(delivered.items())[-70:])
