"""
Пороги и настройки. Если логика будет врать — крутим ТОЛЬКО здесь.
Тариф: Twelve Data, 337 кредитов/мин.
"""

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
