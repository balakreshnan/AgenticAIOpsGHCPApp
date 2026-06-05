"""Agentic AIOps Studio — Streamlit entrypoint.

Run with:  ``streamlit run app.py``
"""

from __future__ import annotations

import streamlit as st

from common.config import ensure_utf8_streams
from common.styles import inject_theme
from common.ui import app_header
from tabs import (
    assert_tab,
    chat_tab,
    evaluations_tab,
    governance_tab,
    rampart_tab,
    redteam_tab,
    tracing_tab,
)

# Make stdout/stderr UTF-8 before any SDK logs emoji (e.g. the red-team scanner),
# so Windows' cp1252 console codec can't crash a run with UnicodeEncodeError.
ensure_utf8_streams()

st.set_page_config(
    page_title="Agentic AIOps Studio",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

inject_theme()
app_header()

chat, evals, assert_t, governance, redteam, rampart, tracing = st.tabs(
    [
        "💬  Chat",
        "📊  Evaluations",
        "✅  Assert",
        "⚖️  Governance",
        "🛡️  Red Team",
        "🧪  RAMPART",
        "🔍  Tracing",
    ]
)

with chat:
    chat_tab.render()

with evals:
    evaluations_tab.render()

with assert_t:
    assert_tab.render()

with governance:
    governance_tab.render()

with redteam:
    redteam_tab.render()

with rampart:
    rampart_tab.render()

with tracing:
    tracing_tab.render()
