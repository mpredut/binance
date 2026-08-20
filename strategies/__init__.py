"""Venue-neutral strategy engines.

Each engine owns financial decisions while execution stays behind provider
contracts.  Venue-specific launchers remain responsible for configuration,
market selection, notifications, and live-order gates.
"""

from .spot_dca import StratParams, Strategy

__all__ = ["StratParams", "Strategy"]
