# Модуль 11 — POC / Volume / Volume Profile — глубокая доработка

Дата: 2026-10-07.

## Принцип
Spot FX OHLC не выдаётся за централизованный биржевой volume-at-price. Основной профиль остаётся TPO/price-acceptance proxy. VWAP включается только при фактическом provider volume.

## Внесено
- Основной финальный факт переведён с закрытой M15 на закрытую H1; M15 — только вспомогательный veto/confirmation.
- HVN/LVN определяются как локальные экстремумы распределения acceptance; количество узлов ограничено.
- Добавлена форма профиля: BALANCED / TOP_HEAVY / BOTTOM_HEAVY.
- Добавлен Composite POC по более длинному H1 окну как контекст, не отдельный голос.
- Добавлен Naked POC: POC завершённого 24-H1 профиля считается naked только если последующие закрытые H1 его не тестировали.
- Добавлен Initial Balance текущего UTC-дня по первым закрытым H1; при неразбираемом timestamp значение недоступно, а не угадывается.
- Добавлен Balanced Target как симметричная measured objective от POC/Value Area; это цель-контекст, не сигнал.
- Near-HVN даёт только ограниченный +2 quality bonus.
- Новая геометрия передаётся в Evidence Bus; Telegram остаётся event-driven и не получает каждое изменение developing POC.
- Добавлены deterministic/non-repaint тесты профиля и тесты новой геометрии.

## Намеренно не внедрено
- Fake footprint, bid/ask delta, VPOC и volume-at-price из OHLC без реального источника объёма.
- Самостоятельные LONG/SHORT голоса от IB, Naked POC, Composite POC, HVN/LVN или Balanced Target.
- Telegram-уведомление на каждое POC_CHANGED/VA_CHANGED: это создало бы шум. Изменения остаются внутренним контекстом до подтверждённой H1 реакции.

## Проверка
`pytest -q test_poc_profile.py test_module_evidence_bridge.py test_swing_profile.py` -> 11 passed.
