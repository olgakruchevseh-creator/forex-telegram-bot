# HMM Regime Context V1 — полная causal OBSERVE_ONLY версия

## Назначение
Hidden Markov Model не заменяет Market Regime и не создаёт LONG/SHORT. Он оценивает скрытое вероятностное состояние закрытого H1-потока и устойчивость/риск перехода.

## Академическая база
Реализованы стандартные компоненты Gaussian HMM: Markov transition matrix, diagonal Gaussian emissions, Baum–Welch EM, forward/backward для обучения. Production-публикация состояния использует только causal forward-filter P(S_t|X_1..X_t). Viterbi/smoothed posterior в live-решение не передаются.

## Наблюдения
Признаки H1: log-return, log rolling volatility, 8H efficiency ratio, нормализованный candle range. Все признаки строятся только из закрытых H1. Перед fit выполняется стандартизация внутри доступного окна.

## Устойчивость
- 3 состояния по умолчанию; без направления LONG/SHORT.
- diagonal covariance с floor для численной устойчивости.
- deterministic initialization; без случайного seed drift.
- log-space forward/backward против underflow.
- transition rows нормируются.
- canonical state mapping после fit снижает label switching.
- entropy превращается в confidence как 100*(1-normalized entropy).
- transition_risk = 1-A_ii.
- expected_duration = 1/(1-A_ii).
- insufficient-data fallback без выдуманной вероятности.

## Состояния
QUIET / DIRECTIONAL / TURBULENT — это описательные имена emission-профилей, а не торговое направление. HMM не имеет права сам превращать их в LONG/SHORT.

## Интеграция
На V1 live_effect=NONE. Слой предназначен для наблюдения и последующей калибровки рядом с Character Matrix, Market Regime, Navigator и Change-Point Brain. Никаких veto, Master Direction delta или самостоятельных Telegram-сигналов не добавлено.
