"""Factories for Azure / Foundry clients.

The synchronous :class:`AIProjectClient` (used to *enumerate* agents) is cached as
a Streamlit resource. The Agent Framework :class:`FoundryChatClient` is async and
short-lived, so it is created per request inside the chat utility rather than
cached here.
"""

from __future__ import annotations

import streamlit as st
from azure.identity import DefaultAzureCredential

from .config import Settings, require_settings


@st.cache_resource(show_spinner=False)
def get_credential() -> DefaultAzureCredential:
    """Return a cached synchronous :class:`DefaultAzureCredential`."""
    return DefaultAzureCredential(exclude_interactive_browser_credential=False)


@st.cache_resource(show_spinner=False)
def get_project_client():
    """Return a cached :class:`AIProjectClient` for the configured Foundry project.

    Raises :class:`~common.config.ConfigError` when configuration is missing and
    propagates any Azure authentication error so the UI can surface it.
    """
    from azure.ai.projects import AIProjectClient

    settings: Settings = require_settings()
    return AIProjectClient(
        endpoint=settings.project_endpoint,
        credential=get_credential(),
    )
