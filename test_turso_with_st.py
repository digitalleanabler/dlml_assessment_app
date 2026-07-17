"""Minimal Streamlit connectivity check for the Turso database.

Run from the ``app`` directory with:
    streamlit run test_turso_with_st.py

The app reads credentials from ``.streamlit/secrets.toml`` rather than source
code, using the same ``libsql`` driver as ``test_turso.py``.
"""

from __future__ import annotations

import streamlit as st

try:
    import libsql
except ImportError as exc:
    st.error("The libsql package is not installed. Run: pip install -r requirements.txt")
    st.exception(exc)
    st.stop()


st.set_page_config(page_title="Turso connection test", page_icon="🔌")
st.title("Turso connection test")
st.caption("Checks the Turso database using the same libsql driver as test_turso.py.")


def get_turso_credentials() -> tuple[str, str]:
    """Read Turso credentials from the [turso] Streamlit secrets section."""
    config = st.secrets.get("turso", {})
    database_url = str(config.get("TURSO_DATABASE_URL", "")).strip()
    auth_token = str(config.get("TURSO_AUTH_TOKEN", "")).strip()
    return database_url, auth_token


database_url, auth_token = get_turso_credentials()

if not database_url or not auth_token:
    st.error("Turso credentials are missing from Streamlit secrets.")
    st.code(
        '[turso]\n'
        'TURSO_DATABASE_URL = "libsql://your-database.turso.io"\n'
        'TURSO_AUTH_TOKEN = "your-token"',
        language="toml",
    )
    st.stop()

st.success("Turso URL and authentication token were found in Streamlit secrets.")

try:
    connection = libsql.connect(database_url, auth_token=auth_token)
    health_check = connection.execute("SELECT 1 AS connection_ok").fetchone()
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
except Exception as exc:
    st.error("Turso connection test failed.")
    st.exception(exc)
else:
    st.success(f"Turso connection test passed (SELECT 1 returned {health_check[0]}).")
    st.subheader("Database tables")
    st.dataframe({"table_name": [row[0] for row in tables]}, hide_index=True)
