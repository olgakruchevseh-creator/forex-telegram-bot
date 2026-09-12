from datetime import datetime, timedelta, timezone
import news


def ev(title="CPI y/y", actual="3.2%", forecast="3.1%", previous="3.0%", minutes=0, currency="USD"):
    dt=datetime.now(timezone.utc)+timedelta(minutes=minutes)
    return news.NewsEvent("x", title, currency, "HIGH", dt, previous, forecast, actual, news.classify_effect(title))


def test_cpi_01pp_surprise_is_not_lost_by_old_relative_filter():
    e=ev()
    assert news.interpret_print(e) == "positive"
    assert round(news.cpi_surprise(e)["diff_pp"], 2) == 0.10


def test_core_cpi_translation_is_specific():
    assert news.translate_title("Core CPI m/m") == "Базовый CPI"


def test_mixed_headline_core_is_not_forced_directional():
    anchor=ev("CPI y/y", "3.2%", "3.1%")
    core=news.NewsEvent("y", "Core CPI y/y", "USD", "HIGH", anchor.dt_utc,
                       "3.0%", "3.1%", "3.0%", news.classify_effect("Core CPI y/y"))
    assert news.cpi_release_consensus([anchor,core], "USD", anchor)["verdict"] == "mixed"


def test_pair_guard_before_and_after_cpi():
    before=ev(minutes=30)
    assert "вход запрещён" in news.cpi_pair_guard("EUR/USD", [before])
    after=ev(minutes=-10)
    assert "пост-CPI" in news.cpi_pair_guard("EUR/USD", [after])
    assert news.cpi_pair_guard("AUD/NZD", [after]) == ""
