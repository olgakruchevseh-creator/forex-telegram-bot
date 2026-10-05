"""Полный read-only снимок числовых параметров принятия решений.

OBSERVE_ONLY: ничего не меняет и не участвует в verdict; нужен только для
воспроизводимой калибровки и сравнения недель.
"""
from __future__ import annotations
import config as cfg

def _numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)

def _flatten(prefix, value, out):
    if _numeric(value):
        out[prefix] = value
    elif isinstance(value, dict):
        for key, item in value.items():
            _flatten(f"{prefix}.{key}", item, out)
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            if _numeric(item): out[f"{prefix}[{i}]"] = item

def snapshot() -> dict:
    out = {"schema": int(getattr(cfg, "NUMERICAL_CALIBRATION_SCHEMA", 2))}
    for key in sorted(k for k in dir(cfg) if k.isupper() and not k.startswith("__")):
        try: _flatten(key, getattr(cfg, key), out)
        except Exception: continue
    return out
