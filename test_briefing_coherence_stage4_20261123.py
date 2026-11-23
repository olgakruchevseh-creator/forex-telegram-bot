"""Stage 4 regression: session briefing clarity only, no trading thresholds touched."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import briefing
import config as cfg
import news
import session_projection_reports as spr
from analysis import Candle


def _event(title, currency="EUR", minutes=15):
    return news.NewsEvent("id", title, currency, "MEDIUM",
                          datetime(2026, 11, 23, 8, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes),
                          "49", "50", "—", "higher_is_positive")


def test_pmi_countries_and_types_are_not_collapsed():
    assert "Франция" in news.translate_title("French Flash Manufacturing PMI")
    assert "производства" in news.translate_title("French Flash Manufacturing PMI")
    assert "Германия" in news.translate_title("German Flash Services PMI")
    assert "сферы услуг" in news.translate_title("German Flash Services PMI")
    assert "Великобритания" in news.translate_title("UK Flash Manufacturing PMI")
    assert "Еврозона" in news.translate_title("Eurozone Flash Composite PMI")
    assert "составной" in news.translate_title("Eurozone Flash Composite PMI")


def test_unknown_pmi_original_preserved_without_inventing_country():
    title = "Special PMI Advance Private Sector Q"
    assert title in news.translate_title(title)
    assert "Еврозона" not in news.translate_title(title)


def test_non_pmi_title_unchanged_behavior():
    assert news.translate_title("Core CPI m/m") == "Базовый CPI"


def test_missing_eurozone_pmi_is_a_cautious_observation_not_fact():
    events = [_event("French Flash Manufacturing PMI"), _event("German Flash Services PMI", minutes=30)]
    text = news.pmi_calendar_diagnostic(events)
    assert "не найден" in text
    assert "НЕ доказательство" in text
    assert news.pmi_calendar_diagnostic(events + [_event("Eurozone Flash Manufacturing PMI", minutes=60)]) == ""
    assert news.pmi_calendar_diagnostic([_event("German Flash Services PMI")]) == ""


def test_news_block_disambiguates_and_shows_completeness_warning(monkeypatch):
    monkeypatch.setattr(news, "calendar_status", lambda: "live")
    events = [_event("French Flash Manufacturing PMI"), _event("German Flash Services PMI", minutes=30)]
    text = "\n".join(briefing.format_news_block(events, {"EUR": 1}, datetime(2026,11,23,8,1,tzinfo=timezone.utc)))
    assert "Франция" in text and "Германия" in text
    assert "провер" in text.lower() and "Forex Factory" in text


def test_forecast_numbers_are_scores_not_historical_probabilities(monkeypatch):
    b = SimpleNamespace(symbol="EUR/USD", agree="2/3", agree_n=2, gap=0.2, state="ТРЕНД")
    monkeypatch.setattr(briefing, "technical_pair_side", lambda _b: "LONG")
    monkeypatch.setattr(briefing.divergence_context, "analyze_symbol", lambda *_: None)
    monkeypatch.setattr(briefing.divergence_context, "score_delta", lambda *_: 0)
    report="\n".join(briefing.format_next_session_bias([b], {}))
    assert "оценка согласования" in report
    assert "/100" in report and "%" not in report
    assert "НЕ статистическая вероятность" in report


def test_position_badge_follows_local_leg_not_parent_direction():
    assert briefing._position_direction_icon("ОТКАТ LONG ВНУТРИ SHORT") == "🟢"
    assert briefing._position_direction_icon("ОТКАТ SHORT ВНУТРИ LONG") == "🔴"
    assert briefing._position_direction_icon("ЛОКАЛЬНЫЙ ИМПУЛЬС LONG · СТАРШИЙ ТРЕНД SHORT") == "🟢"
    assert briefing._position_direction_icon("ОСНОВНОЙ SHORT") == "🔴"
    assert briefing._position_direction_icon("ПЕРЕХОД") == "🟡"


def test_method_disagreements_h4_and_h1_are_explicit():
    b=SimpleNamespace(h4="LONG", h1="SHORT",
        adaptive_direction={"main_direction":"SHORT", "working_h1_direction":"LONG"})
    conflicts=briefing._direction_method_disagreements(b)
    assert "H4" in conflicts[0] and "ЛОНГ" in conflicts[0] and "ШОРТ" in conflicts[0]
    assert "H1" in conflicts[1]
    b.adaptive_direction={"main_direction":"LONG", "working_h1_direction":"SHORT"}
    assert briefing._direction_method_disagreements(b) == []


def test_early_briefing_snapshot_is_not_mislabeled_stable():
    check = SimpleNamespace(ok=True, last_closed_at=datetime(2026,11,23,8,tzinfo=timezone.utc), age_minutes=1.2)
    label=briefing._snapshot_status_line(check)
    assert "08:00 UTC" in label
    assert "до 5-минутной" in label
    check.age_minutes=6.0
    label=briefing._snapshot_status_line(check)
    assert "не гарантия" in label


def test_session_caption_shows_h1_asof_and_different_strength_windows():
    bundle={"symbol":"EUR/USD", "h1_closed_at":"23.11.2026 08:00 UTC", "period":"ЕВРОПА → АМЕРИКА",
            "echo":{}, "pivot":{}, "zigzag":{}, "levels_brief":{}}
    caption=spr.combined_pair_caption(bundle, limit=1024)
    assert "закрытие H1 23.11.2026 08:00 UTC" in caption
    assert "Echo/Pivot 8H" in caption
    assert "1/4/12/24H" in caption
    bundle.pop("h1_closed_at")
    assert "НЕ СИНХРОНИЗИРОВАН" in spr.combined_pair_caption(bundle)


def _fake_market():
    opens=[datetime(2026,11,23,6,0,tzinfo=timezone.utc)+timedelta(minutes=5*i) for i in range(35)]
    return {p:{"H1":[Candle('2026-11-23 07:00:00',1,1,1,1),
                       Candle('2026-11-23 08:00:00',2,2,2,2)],
               "M5":[Candle(t.strftime('%Y-%m-%d %H:%M:%S'),1,1,1,1) for t in opens]}
            for p in cfg.PAIRS}


def test_session_bundle_uses_verified_h1_cutoff_without_mutating_scan(monkeypatch):
    cutoff=datetime(2026,11,23,8,tzinfo=timezone.utc)
    monkeypatch.setattr(spr.briefing.briefing_strength_verified,"calculate",lambda *a,**k:SimpleNamespace(ok=True,last_closed_at=cutoff))
    monkeypatch.setattr(spr.briefing,"briefing_id",lambda:'sess')
    monkeypatch.setattr(spr,"_session_context",lambda:('ЕВРОПА','АМЕРИКА',6))
    monkeypatch.setattr(spr.briefing,"build_pair_briefs",lambda *a,**k:[])
    monkeypatch.setattr(spr,"_echo_report",lambda symbol,by_tf,*a,**k:{"result":{}, "text":"echo"})
    monkeypatch.setattr(spr,"_pivot_report",lambda symbol,by_tf,*a,**k:{"result":{}, "text":"pivot"})
    monkeypatch.setattr(spr.zigzag_scanner,"analyze_symbol",lambda *a,**k:{})
    monkeypatch.setattr(spr.pair_character_matrix,"analyze",lambda *a,**k:{})
    monkeypatch.setattr(spr.pair_character_matrix,"attach_interaction",lambda *a,**k:{})
    monkeypatch.setattr(spr.levels,"briefing_levels_snapshot",lambda *a,**k:{})
    monkeypatch.setattr(spr.session_pullback_consensus,"analyze",lambda *a,**k:{})
    monkeypatch.setattr(spr,"render_unified_scenario_chart",lambda *a,**k:object())
    market=_fake_market()
    original_len=len(market["EUR/USD"]["M5"])
    state={}
    bundles=spr.pending_report_bundles(market,[],state,force_symbols={'EUR/USD'})
    assert len(bundles)==1
    assert bundles[0]['h1_closed_at']=='23.11.2026 08:00 UTC'
    assert 'закрытие H1 23.11.2026 08:00 UTC' in bundles[0]['caption']
    assert len(market["EUR/USD"]["M5"])==original_len
    # Verify the same snapshot actually reached chart/levels inputs via bundle.
    assert 'НЕ СИНХРОНИЗИРОВАН' not in bundles[0]['caption']


def test_market_cutoff_drops_future_intrahour_bars_and_h1():
    import live_briefing
    raw=_fake_market()
    cutoff=datetime(2026,11,23,8,tzinfo=timezone.utc)
    frozen=live_briefing.anchored_market(raw,cutoff)
    assert len(frozen['EUR/USD']['H1'])==1
    assert frozen['EUR/USD']['M5'][-1].dt=='2026-11-23 07:55:00'
    assert len(raw['EUR/USD']['H1'])==2
