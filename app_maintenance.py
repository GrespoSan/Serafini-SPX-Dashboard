"""Maintenance gate for Streamlit Community Cloud entrypoints.

By default the app is PAUSED. In each app's Streamlit Secrets,
MAINTENANCE_MODE = false explicitly reopens the Streamlit UI.
The independent Cboe/GEX acquisition freeze remains in force.
"""
DEFAULT_MAINTENANCE_MODE = True
MAINTENANCE_MESSAGE = (
    "Acquisizioni Cboe, Replay e studi sospesi in attesa del ripristino "
    "della fonte dati. Nessun archivio è stato eliminato."
)


def maintenance_enabled(secrets):
    """Fail closed if Secrets are unavailable or the toggle is ambiguous."""
    try:
        value = secrets.get("MAINTENANCE_MODE", DEFAULT_MAINTENANCE_MODE)
    except Exception:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "false":
            return False
        if normalized == "true":
            return True
    return True


def suspend_if_enabled(st, app_name):
    """Stop Streamlit before importing data/network modules or doing cache I/O."""
    if not maintenance_enabled(st.secrets):
        return False
    st.title(app_name)
    st.warning("Applicazione temporaneamente sospesa")
    st.info(MAINTENANCE_MESSAGE)
    st.caption("Modalità manutenzione attiva. I dati salvati restano intatti.")
    st.stop()
    return True
