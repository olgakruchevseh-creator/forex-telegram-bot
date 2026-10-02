from pathlib import Path


def test_master_uses_realtime_utc_not_closed_h1_clock():
    text = Path('bot.py').read_text(encoding='utf-8')
    call = text[text.index('master_results = master_direction.analyze_market('):]
    call = call[:call.index(')\n', call.index('master_results = master_direction.analyze_market(')) + 2]
    assert 'now_utc=datetime.now(timezone.utc)' in call
    assert 'now_utc=closed_bar_utc(closed_dt)' not in call
