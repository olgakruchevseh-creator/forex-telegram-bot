import session_projection_reports as reports


def test_long_echo_report_is_compacted_for_photo_caption():
    report = {"text": "\n".join([
        "🔭 ЭХО — ПРОГНОЗ ДО СЛЕДУЮЩЕЙ СЕССИИ",
        "💱 Пара: EUR/USD", "Период: ЕВРОПА → АМЕРИКА · около 6 ч",
        "Направление к границе следующей сессии: LONG 🟢",
        "Вероятность направления: 72%",
        "Достаточность данных для траектории: 80%",
        "📰 До следующей сессии значимых новостей по паре нет.",
        "x" * 1600,
        "⚠️ Это вероятностный технический сценарий, а не торговый сигнал.",
    ])}
    caption = reports.compact_photo_caption(report, "echo")
    assert len(caption) <= 1000
    assert "EUR/USD" in caption
    assert "72%" in caption
    assert "полный разбор — следующим сообщением" not in caption.lower()


def test_long_pivot_report_stays_one_photo_caption():
    report = {"text": "\n".join([
        "🎯 NEXT PIVOT — ПРОГНОЗ ДО СЛЕДУЮЩЕЙ СЕССИИ",
        "💱 Пара: USD/CHF", "Период: ЕВРОПА → АМЕРИКА · около 6 ч",
        "Первичное движение к Pivot: SHORT 🔴",
        "Ожидаемая зона ВЕРШИНЫ: 0.83000–0.83200",
        "Вероятность первичного движения к Pivot: 68%",
        "y" * 1600,
    ])}
    caption = reports.compact_photo_caption(report, "pivot")
    assert len(caption) <= 1000
    assert "USD/CHF" in caption
    assert "0.83000–0.83200" in caption
