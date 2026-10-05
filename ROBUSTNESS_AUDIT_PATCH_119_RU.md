# Patch 119 — Passive Robustness Audit

Дата: 2026-10-05
Режим: OBSERVE_ONLY.

Добавлен независимый статистический слой поверх Decision Replay. Он не меняет LONG/SHORT, KILLER, Master Direction, Navigator, пороги, veto или Telegram routing.

Файл `robustness_audit.py` формирует `robustness_audit.json` и считает только по уже созревшим SENT outcomes:
- expectancy proxy в ATR;
- win-rate proxy;
- Profit Factor proxy;
- Sharpe/Sortino proxy;
- max drawdown proxy;
- средние MFE/MAE;
- разрезы pair/source/regime;
- rolling out-of-sample stability без оптимизации параметров;
- deterministic bootstrap Monte-Carlo.

Важно: proxy = MFE_ATR(3h) - MAE_ATR(3h). Это не P&L брокера. Комиссия/slippage не выдумываются при отсутствии реальных fills.

Тесты: `test_robustness_audit.py`.
