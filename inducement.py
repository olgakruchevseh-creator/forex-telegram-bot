"""Compatibility alias for the single IDM layer in idm.py.

Keep this module so Echo / Next Pivot imports do not break.
Do not add a second inducement detector here.
"""
from idm import IDMContext, analyze_symbol, describe

__all__ = ["IDMContext", "analyze_symbol", "describe"]
