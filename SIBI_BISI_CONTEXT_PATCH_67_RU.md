# SIBI/BISI context patch — ZIP 67

- SIBI/BISI встроены внутрь существующего `Imbalance/FVG`, без нового Telegram-модуля.
- BISI = bullish FVG / LONG context; SIBI = bearish FVG / SHORT context.
- Классификация хранится в состоянии FVG и показывается в существующей карточке/сообщении.
- Старые сохранённые FVG автоматически получают классификацию по стороне, без миграции state-файла.
- SIBI/BISI входят только в коррелированное семейство `imbalance` Killer и не дают дополнительный голос/вес.
- Существующая цепочка реакции не изменена: возврат в FVG -> Zone Reaction Confirmation -> OHLC Movement -> early/late-entry check -> дальнейший Navigator/Killer pipeline.
- Простая классификация SIBI/BISI не создаёт отдельного события и не добавляет Telegram-спам.
