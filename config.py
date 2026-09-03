"""
Пороги и настройки. Если логика будет врать — крутим ТОЛЬКО здесь.
Тариф: Twelve Data, 337 кредитов/мин.
"""
import os

PAIRS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "USD/CHF",
    "AUD/USD",
    "NZD/USD",
    "USD/CAD",
]

CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD"]

# Старший → младший. Сигнал идёт сверху вниз.
TIMEFRAMES = [
    {"key": "W1", "api": "1week", "label": "Неделя", "candles": 100, "ttl_min": 30},
    {"key": "D1", "api": "1day", "label": "День", "candles": 200, "ttl_min": 15},
    {"key": "H4", "api": "4h", "label": "4 часа", "candles": 250, "ttl_min": 5},
    {"key": "H1", "api": "1h", "label": "Час", "candles": 250, "ttl_min": 3},
    {"key": "M15", "api": "15min", "label": "15 минут", "candles": 250, "ttl_min": 2},
    {"key": "M5", "api": "5min", "label": "5 минут", "candles": 250, "ttl_min": 1},
]

STRENGTH_TF = "H1"
# Сила = движение за последнюю ЗАКРЫТУЮ часовую свечу.
STRENGTH_LOOKBACK = 1
STRENGTH_RANK_JUMP = 2
# Автосообщение — только когда закрылась новая H1, не чаще раза за эту свечу.
STRENGTH_REPORT_EVERY_HOURS = 1
PAIR_STRENGTH_MIN = 0.20

SCAN_EVERY_MINUTES = 5
# Автоматические сканы и уведомления: только понедельник–пятница.
# Номера дней Python: понедельник=0, воскресенье=6.
AUTOMATIC_WEEKDAYS_ONLY = True
AUTOMATIC_ACTIVE_WEEKDAYS = (0, 1, 2, 3, 4)
# 337 кредитов/мин. Полный скан = 6 batch ≈ 42 кредита. Запас большой.
REQUEST_PAUSE_SEC = 0.08
PAID_PLAN = True
PLAN_NAME = "337"

ZIGZAG_PCT = {
    "W1": 1.20,
    "D1": 0.70,
    "H4": 0.35,
    "H1": 0.18,
    "M15": 0.12,
    "M5": 0.08,
}
ZIGZAG_MIN_BARS = 3
# Адаптивный ZigZag: процентный порог дополняется текущей волатильностью ATR.
ZIGZAG_ADAPTIVE_PCT_FACTOR = 0.55
ZIGZAG_MIN_MOVE_ATR = 0.55
SIGNAL_BLOCK_OPPOSITE_H4_ZIGZAG = True
DXY_IMPULSE_MIN_CHANGE_PCT = 0.05
DXY_IMPULSE_MIN_ADX = 25

ADX_PERIOD = 14
ADX_TREND = 22
EMA_FAST = 21
EMA_SLOW = 50

FVG_NEAR_ATR = 1.2
ATR_PERIOD = 14

HTF_KEYS = ["W1", "D1", "H4"]
HTF_MIN_AGREE = 2

LTF_KEYS = ["H1", "M15", "M5"]
LTF_MIN_AGREE = 2

SIGNAL_COOLDOWN_HOURS = 6

LOCAL_TZ_NAME = "Europe/Amsterdam"
SESSIONS = [
    {"key": "ASIA", "name": "АЗИАТСКАЯ СЕССИЯ", "start_hm": "00:00"},
    {"key": "EUROPE", "name": "ЕВРОПЕЙСКАЯ СЕССИЯ", "start_hm": "09:00"},
    {"key": "AMERICA", "name": "АМЕРИКАНСКАЯ СЕССИЯ", "start_hm": "15:00"},
]
# Публичный /indices Twelve Data не содержит DXY; time_series на тарифе часто отдаёт DXY.
# Рабочий тикер можно задать в ENV, не меняя код.
DXY_SYMBOL = (os.getenv("DXY_SYMBOL") or "DXY").strip() or "DXY"
USDSEK_SYMBOL = "USD/SEK"
DXY_SEK_MAX_LAG_HOURS = int(os.getenv("DXY_SEK_MAX_LAG_HOURS") or "2")
DXY_MIN_PRICE = 50.0
DXY_MAX_PRICE = 200.0
BRIEFING_ENABLED = True
NEWS_WARN_MINUTES = 60
BRIEFING_OPEN_WINDOW_MIN = 15
LEVELS_ENABLED = True
LEVEL_NOTIFY_NEW = False
# Не объединять далёкие уровни в одну чрезмерно широкую торговую зону.
LEVEL_MAX_ZONE_H1_ATR = 3.0
LEVEL_MAX_ZONE_PIPS = 45
LEVEL_EVENT_COOLDOWN_MINUTES = 55
LEVEL_EXACT_EVENT_HISTORY = 1500
LEVEL_MIN_EVENT_QUALITY = 74
LEVEL_MIN_EVENT_CONFIDENCE = 70
ZIGZAG_SCANNER_ENABLED = True
PATTERNS_ENABLED = True
DISBALANCE_ENABLED = True
DISBALANCE_MIN_BODY_ATR = 1.25
DISBALANCE_MIN_BODY_RATIO = 0.62
DISBALANCE_BOS_LOOKBACK = 12
DISBALANCE_MIN_STRENGTH_GAP = 0.06
DISBALANCE_MIN_QUALITY = 76
IMBALANCE_ENABLED = True
IMBALANCE_MIN_GAP_ATR = 0.08
IMBALANCE_MIN_STRENGTH_GAP = 0.05
IMBALANCE_MIN_QUALITY = 74
ACCUMULATION_DISTRIBUTION_ENABLED = True
PHASE_RANGE_BARS = 24
PHASE_PRIOR_BARS = 16
PHASE_MAX_WIDTH_ATR = 7.5
PHASE_MAX_EFFICIENCY = 0.32
PHASE_MIN_PRIOR_MOVE_ATR = 1.8
PHASE_MIN_QUALITY = 74
PHASE_EXIT_STRENGTH_GAP = 0.05
DAILY_HIGH_LOW_ENABLED = True
DAILY_LEVEL_BREAK_BUFFER_ATR = 0.08
DAILY_LEVEL_TOUCH_ATR = 0.12
DAILY_LEVEL_MIN_CONFIRMATIONS = 2
DAILY_LEVEL_MIN_STRENGTH_GAP = 0.05
CHAIN_ENTRIES_ENABLED = True
CHAIN_PIVOT_BARS = 3
CHAIN_BOS_BUFFER_ATR = 0.08
CHAIN_BOS_BODY_ATR = 0.45
CHAIN_RETEST_TOLERANCE_ATR = 0.22
CHAIN_INVALIDATION_ATR = 0.32
CHAIN_MAX_RETEST_BARS = 12
CHAIN_MIN_CONFIRMATIONS = 2
CHAIN_MIN_STRENGTH_GAP = 0.06
PATTERN_MIN_QUALITY = 72
PATTERN_MIN_CONFIDENCE = 70
PATTERN_LOOKBACK = {"W1": 80, "D1": 150, "H4": 180, "H1": 180, "M15": 160, "M5": 140}
PATTERN_PIVOT = {"W1": 3, "D1": 3, "H4": 3, "H1": 2, "M15": 2, "M5": 2}
# Допуски Фибоначчи для гармонических и ABCD.
FIB_TOL = 0.06
HARMONIC_RATIOS = {
    "gartley": {"xb": (0.56, 0.66), "ac": (0.382, 0.886), "bd": (1.13, 1.618), "xd": (0.72, 0.85)},
    "bat": {"xb": (0.382, 0.50), "ac": (0.382, 0.886), "bd": (1.618, 2.618), "xd": (0.82, 0.95)},
    "alt_bat": {"xb": (0.382, 0.382), "ac": (0.382, 0.886), "bd": (2.0, 3.618), "xd": (1.08, 1.18)},
    "butterfly": {"xb": (0.76, 0.81), "ac": (0.382, 0.886), "bd": (1.618, 2.24), "xd": (1.20, 1.50)},
    "crab": {"xb": (0.382, 0.618), "ac": (0.382, 0.886), "bd": (2.24, 3.618), "xd": (1.55, 1.68)},
    "deep_crab": {"xb": (0.84, 0.924), "ac": (0.382, 0.886), "bd": (2.0, 3.618), "xd": (1.55, 1.68)},
    "shark": {"xb": (0.382, 0.618), "ac": (1.13, 1.618), "xd": (0.86, 1.13)},
    "cypher": {"xb": (0.382, 0.618), "ac": (1.13, 1.414), "xd": (0.75, 0.81)},
    "five_o": {"xb": (1.13, 1.618), "ac": (1.618, 2.24), "cd": (0.48, 0.62)},
    "abcd": {"bc": (0.382, 0.886), "cd_ab": (1.0, 1.68)},
}
ABCD_TIME_SYM = 0.45
PATTERN_MAIN_TFS = ["W1", "D1", "H4", "H1"]
PATTERN_CONFIRM_TFS = ["M15", "M5"]
