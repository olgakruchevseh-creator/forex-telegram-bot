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
# Лидер брифинга не выбирается при практически равной силе валют.
BRIEFING_LEADER_MIN_STRENGTH_GAP = 0.05

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
# Единый бюджет для всех торговых модулей после одной закрытой H1.
# Часовой брифинг и новостные сообщения в этот лимит не входят.
MAX_MODULE_ALERTS_PER_H1 = 3
SIGNAL_JOURNAL_ENABLED = True
JOURNAL_TARGET_ATR = 1.0
JOURNAL_INVALIDATION_ATR = 0.75
# Время Europe/Amsterdam; скан отправит отчёт в ближайший 5-минутный запуск.
# Один окончательный отчёт утром за предыдущий торговый день (Europe/Amsterdam).
JOURNAL_DAILY_REPORT_HM = (9, 10)
JOURNAL_WEEKLY_REPORT_HM = (22, 30)
MASTER_DIRECTION_ENABLED = True
MASTER_STRENGTH_MIN_GAP = 0.08
MASTER_MIN_QUALITY = 82
MASTER_REQUIRE_MODULE_TRIGGER = True
MASTER_MAX_SIGNALS_PER_H1 = 2
# Echo — историческая проекция, только внутренний фильтр Master Direction.
ECHO_ENABLED = True
ECHO_HORIZONS_H1 = (1, 2, 4, 8)
ECHO_MIN_ANALOGS = 30
ECHO_MAX_ANALOGS = 50
ECHO_MAX_DISTANCE = 2.6
ECHO_MIN_CONFIDENCE = 0.60
# Только уверенная противоположная проекция блокирует торговую карточку.
ECHO_BLOCK_OPPOSITE_CONFIDENCE = 0.68
# Ранний локальный сценарий: только завершённый AMD, подтверждённые M15/M5
# и ещё не более 45% уже пройденного структурного маршрута.
LOCAL_AMD_EARLY_ENABLED = True
LOCAL_AMD_MIN_STRENGTH_GAP = 0.08
LOCAL_AMD_MAX_PROGRESS_PCT = 45
MASTER_NEWS_BLOCK_BEFORE_MINUTES = 60
MASTER_NEWS_BLOCK_AFTER_MINUTES = 30

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
NEWS_CACHE_MAX_AGE_HOURS = 168
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
# Отбой отправляется только после следующей H1 и при поддержке силы валют.
LEVEL_BOUNCE_REQUIRE_FOLLOW_THROUGH = True
LEVEL_BOUNCE_MIN_STRENGTH_GAP = 0.05
LEVEL_FALSE_BREAK_MIN_STRENGTH_GAP = 0.05
# Связывать противоположные подтверждённые реакции одной пары внутри дня.
LEVEL_DIRECTION_MEMORY_HOURS = 12
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
AMD_POWER_OF_THREE_ENABLED = True
AMD_RANGE_BARS = 20
AMD_MAX_MANIPULATION_AGE_BARS = 6
AMD_MAX_RANGE_ATR = 5.5
AMD_MAX_RANGE_EFFICIENCY = 0.35
AMD_MIN_EDGE_TOUCHES = 2
AMD_MIN_SWEEP_ATR = 0.08
AMD_BREAK_BUFFER_ATR = 0.08
AMD_BREAK_BODY_ATR = 0.50
AMD_MIN_STRENGTH_GAP = 0.06
MOVEMENT_PROGRESS_ENABLED = True
MOVEMENT_PROGRESS_MIN_STRENGTH_GAP = 0.03
MOVEMENT_PULLBACK_MAX_OPPOSITE_STRENGTH = 0.03
MOVEMENT_PROGRESS_MIN_TARGET_ATR = 0.8
MOVEMENT_PROGRESS_MIN_REPORT_PCT = 15
MOVEMENT_PROGRESS_MIN_CHANGE_PCT = 8
# Неотправленный модульный сигнал ждёт подтверждения максимум 4 закрытых H1.
SIGNAL_CANDIDATE_TTL_HOURS = 4
# Одно предупреждение перед структурной целью; промежуточные проценты не спамят.
SIGNAL_NEAR_TARGET_PCT = 85
# Экстремумы H4/D1 ближе этого расстояния объединяются в одну цель TR.
MOVEMENT_TARGET_MERGE_ATR = 0.15
LIQUIDITY_SWEEP_ENABLED = True
LIQUIDITY_MIN_SWEEP_ATR = 0.08
LIQUIDITY_EQUAL_TOLERANCE_ATR = 0.20
LIQUIDITY_CHOCH_LOOKBACK = 5
LIQUIDITY_CHOCH_BUFFER_ATR = 0.05
LIQUIDITY_CONFIRM_BODY_ATR = 0.35
LIQUIDITY_MAX_CONFIRM_BARS = 6
LIQUIDITY_INVALIDATION_ATR = 0.15
LIQUIDITY_MIN_STRENGTH_GAP = 0.05
ORDER_BLOCK_ENABLED = True
ORDER_BLOCK_PIVOT_BARS = 3
ORDER_BLOCK_BOS_BUFFER_ATR = 0.08
ORDER_BLOCK_IMPULSE_BODY_ATR = 0.75
ORDER_BLOCK_SEARCH_BACK = 7
ORDER_BLOCK_REACTION_BODY_ATR = 0.30
ORDER_BLOCK_INVALIDATION_ATR = 0.12
ORDER_BLOCK_MAX_RETEST_H1_BARS = 24
ORDER_BLOCK_MIN_STRENGTH_GAP = 0.05
PHASE_RANGE_BARS = 24
PHASE_PRIOR_BARS = 16
PHASE_MAX_WIDTH_ATR = 7.5
PHASE_MAX_EFFICIENCY = 0.32
PHASE_MIN_PRIOR_MOVE_ATR = 1.8
PHASE_MIN_QUALITY = 74
PHASE_EXIT_STRENGTH_GAP = 0.05
# Сам диапазон рассчитывается внутри; наружу идёт только подтверждённый выход.
PHASE_NOTIFY_FORMATION = False
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
RETEST_CONFIRMATION_ENABLED = True
RETEST_PIVOT_BARS = 3
RETEST_BOS_BUFFER_ATR = 0.08
RETEST_BOS_BODY_ATR = 0.50
RETEST_HOLD_BUFFER_ATR = 0.08
RETEST_TOUCH_TOLERANCE_ATR = 0.18
RETEST_REACTION_BODY_ATR = 0.30
RETEST_INVALIDATION_ATR = 0.18
RETEST_MAX_H1_BARS = 12
RETEST_MIN_STRENGTH_GAP = 0.05
FIBONACCI_ENABLED = True
FIBONACCI_MIN_IMPULSE_ATR = 2.0
FIBONACCI_REACTION_BODY_ATR = 0.30
FIBONACCI_MIN_STRENGTH_GAP = 0.05
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
# Гармоническая геометрия сама по себе не является торговым сигналом.
# Для отправки требуется подтвержденный импульс H1 и слом структуры M15,
# поддержанные относительной силой валют.
HARMONIC_MIN_STRENGTH_GAP = 0.05
HARMONIC_CONFIRM_BODY_ATR = 0.35
HARMONIC_CONFIRM_LOOKBACK = 8
