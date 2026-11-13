"""Refresh only short-lived SPX dashboard data sources.

Never reset Streamlit session state (authentication, selected snapshot, user
inputs) or flush the global cache: raw Cboe snapshots and historical model
calculations are immutable, large, and should not be downloaded again.
"""

def refresh_current_data():
    """Invalidate current/listing caches; rerun is already triggered by the button."""
    from storage import _cached_list
    from edge_latest_source import _latest_verified_replay
    from edge_replay_panel import _cached_slots, _latest_es_quote
    from replay_prices import _yahoo_candles, _massive_candles
    from vix_spot_history import _download_vix_history
    from macro_calendar import load_calendar

    for cache in (
        _cached_list,            # New official Cboe + logs, Replay and Edge files
        _latest_verified_replay, # Re-evaluate newest verified 0DTE Edge source
        _cached_slots,          # Newly acquired intraday replay slots
        _latest_es_quote,       # Last closed ES bar (not the old scenario)
        _yahoo_candles,         # Price-feed caches used by Replay/current ES
        _massive_candles,
        _download_vix_history,  # VIX spot/historical data, not static settlements
        load_calendar,          # Calendar and incoming news updates
    ):
        cache.clear()
