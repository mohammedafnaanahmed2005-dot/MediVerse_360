"""Snowflake session access. In Streamlit in Snowflake the connection is embedded
(``st.connection("snowflake")``); locally set SNOWFLAKE_DEFAULT_CONNECTION_NAME to a
connection from ~/.snowflake/connections.toml."""

from __future__ import annotations

import os

import streamlit as st


def session():
    # st.connection passes its first argument through as connection_name, so the named
    # connections.toml entry is selected by name rather than by a connection_name kwarg.
    name = os.getenv("SNOWFLAKE_DEFAULT_CONNECTION_NAME") or "snowflake"
    return st.connection(name, type="snowflake").session()
