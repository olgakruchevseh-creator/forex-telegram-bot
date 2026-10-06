# System Validation Brain — 2026-10-06

Режим: **OBSERVE_ONLY**. Этот слой не меняет LONG/SHORT, пороги, veto, маршрутизацию или Telegram.

## Что закрывает
1. Calibration / Reliability: Brier, ECE, reliability bins и проверка ранжирования confidence.
2. Signal Attribution: фактический success rate, efficiency, MFE/MAE по источнику сигнала.
3. Outcome / Failure Engine: success, adverse-first, no-followthrough, invalidation-like, timeout/unresolved.
4. Walk-forward + anti-overfitting: последовательные OOS-подобные folds + уже существующий CPCV с embargo.
5. Audit correlated voting: считает решения, где несколько подтверждений принадлежат одному семейству.
6. Внешние методы оставлены как validation-only: PSR/DSR, PBO/CSCV, purge+embargo, placebo/permutation, parameter plateau. Они не получают права влиять на live-сигнал без накопленной статистики.

Выход: `system_validation_report.json` в STATE_DIR. Обновляется вместе с replay calibration.

## Важная граница
Текущие journal/replay данные позволяют честно измерять калибровку и исходы, но не позволяют корректно вычислять DSR/PBO для перебора параметров без истории всех реально испытанных конфигураций. Поэтому код не выдумывает trial count и не выдаёт ложную статистическую гарантию.
