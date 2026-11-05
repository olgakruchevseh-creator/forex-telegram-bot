"""Одно математическое ядро для синхронной корзины семи USD-мажоров.

`percent_log_return_by_pair` is *100 times log-return* of the quoted pair.
The inverse USD-quote sign is handled here, then an eight-currency zero-sum
USD-star solution is used. No scores are interpreted as probabilities.
"""
from __future__ import annotations

import math
import config as cfg


def stable_log_return_pct(last: float, first: float) -> float:
    """log(a)-log(b) avoids overflow/underflow from a/b on extreme inputs."""
    a, b = float(last), float(first)
    if not all(math.isfinite(v) and v > 0 for v in (a, b)):
        raise ValueError('нечисловая или неположительная цена')
    return 100.0 * (math.log(a) - math.log(b))


def usd_star_scores(pair_log_returns_pct: dict[str, float]) -> dict[str, float]:
    names = tuple(cfg.CURRENCIES)
    if len(names) != 8 or len(set(names)) != 8 or 'USD' not in names or set(pair_log_returns_pct) != set(cfg.PAIRS):
        raise ValueError('неполная USD-корзина: необходимо 7 пар и 8 валют')
    relative = {}
    for pair in cfg.PAIRS:
        base, quote = pair.split('/')
        if (base == 'USD') == (quote == 'USD'):
            raise ValueError('ожидалась пара с USD: ' + pair)
        c = quote if base == 'USD' else base
        if c in relative or c not in names:
            raise ValueError('повтор валюты в USD-корзине')
        pct = float(pair_log_returns_pct[pair])
        if not math.isfinite(pct):
            raise ValueError('нечисловая доходность: ' + pair)
        relative[c] = -pct if base == 'USD' else pct
    if len(relative) != 7 or set(relative) != set(names) - {'USD'}:
        raise ValueError('отсутствует валюта в USD-корзине')
    usd = -math.fsum(relative.values()) / 8
    output = {c: (usd if c == 'USD' else usd + relative[c]) for c in names}
    if any(not math.isfinite(v) for v in output.values()):
        raise ValueError('переполнение валютной силы')
    return output
