"""Agentic AIOps Studio — Streamlit entrypoint.

Run with:  ``streamlit run app.py``
"""

from __future__ import annotations

import streamlit as st

from common.styles import inject_theme
from common.ui import app_header
from tabs import chat_tab, evaluations_tab, redteam_tab, tracing_tab

st.set_page_config(
    page_title="Agentic AIOps Studio",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

inject_theme()
app_header()

chat, evals, redteam, tracing = st.tabs(
    ["💬  Chat", "📊  Evaluations", "🛡️  Red Team", "🔍  Tracing"]
)

with chat:
    chat_tab.render()

with evals:
    evaluations_tab.render()

with redteam:
    redteam_tab.render()

with tracing:
    tracing_tab.render()
