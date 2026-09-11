
from pathlib import Path

def test_all_image_hooks_present():
    bot = Path("bot.py").read_text(encoding="utf-8")
    assert "daily_high_low.image_for_alert(text)" in bot
    assert "levels.image_for_alert(text)" in bot
    assert "retest_confirmation.image_for_alert(text)" in bot
    assert "order_block.image_for_alert(text)" in bot
    assert "liquidity_sweep.image_for_alert(text)" in bot

def test_echo_pivot_catchup_preserved():
    cfg = Path("config.py").read_text(encoding="utf-8")
    assert "SESSION_PROJECTIONS_CATCH_UP = True" in cfg
    bot = Path("bot.py").read_text(encoding="utf-8")
    assert "SESSION_PROJECTIONS_CATCH_UP" in bot

def test_daily_headers_are_wired():
    bot = Path("bot.py").read_text(encoding="utf-8")
    assert "📅 ПРОБОЙ МАКСИМУМА ДНЯ" in bot
    assert "📅 ПРОБОЙ МИНИМУМА ДНЯ" in bot
