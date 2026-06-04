"""Material 3 inspired theming for the Streamlit app.

A single :func:`inject_theme` call applies a professional colour palette, tightens
default Streamlit spacing so the layout fits on one screen, and styles the shared
components (cards, expanders, chat bubbles, tabs).
"""

from __future__ import annotations

import streamlit as st

# Material 3 inspired, business-professional palette.
COLORS = {
    "primary": "#3D5AFE",        # indigo accent
    "primary_dark": "#2A3EB1",
    "on_primary": "#FFFFFF",
    "secondary": "#00897B",      # teal
    "surface": "#FFFFFF",
    "surface_variant": "#F4F6FB",
    "surface_container": "#EEF1F8",
    "background": "#F7F9FC",
    "outline": "#D6DBE8",
    "on_surface": "#1B1F2A",
    "on_surface_variant": "#5A6072",
    "success": "#2E7D32",
    "warning": "#ED6C02",
    "error": "#C62828",
}


def _css() -> str:
    c = COLORS
    return f"""
    <style>
    :root {{
        --md-primary: {c['primary']};
        --md-primary-dark: {c['primary_dark']};
        --md-secondary: {c['secondary']};
        --md-surface: {c['surface']};
        --md-surface-variant: {c['surface_variant']};
        --md-surface-container: {c['surface_container']};
        --md-outline: {c['outline']};
        --md-on-surface: {c['on_surface']};
        --md-on-surface-variant: {c['on_surface_variant']};
    }}

    .stApp {{
        background: {c['background']};
        color: {c['on_surface']};
        font-family: "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }}

    /* Compact the main container so the app fits one screen. */
    .block-container {{
        padding-top: 1.1rem;
        padding-bottom: 0.6rem;
        max-width: 1320px;
    }}

    #MainMenu, footer, header {{ visibility: hidden; }}

    /* ---- App header ---- */
    .aiops-header {{
        display: flex;
        align-items: center;
        gap: 0.75rem;
        margin-bottom: 0.4rem;
    }}
    .aiops-header .logo {{
        width: 40px; height: 40px;
        border-radius: 12px;
        background: linear-gradient(135deg, var(--md-primary), var(--md-secondary));
        display: flex; align-items: center; justify-content: center;
        color: #fff; font-size: 1.25rem; font-weight: 700;
        box-shadow: 0 4px 12px rgba(61,90,254,0.28);
    }}
    .aiops-header h1 {{
        font-size: 1.4rem; font-weight: 700; margin: 0;
        color: var(--md-on-surface);
    }}
    .aiops-header p {{
        margin: 0; font-size: 0.8rem; color: var(--md-on-surface-variant);
    }}

    /* ---- Tabs (Material 3 style) ---- */
    .stTabs [data-baseweb="tab-list"] {{
        gap: 4px;
        border-bottom: 1px solid var(--md-outline);
    }}
    .stTabs [data-baseweb="tab"] {{
        height: 42px;
        border-radius: 10px 10px 0 0;
        padding: 0 18px;
        font-weight: 600;
        color: var(--md-on-surface-variant);
        background: transparent;
    }}
    .stTabs [aria-selected="true"] {{
        color: var(--md-primary);
        background: var(--md-surface-container);
    }}

    /* ---- Cards / containers with a height become elevated surfaces ---- */
    div[data-testid="stVerticalBlockBorderWrapper"] {{
        background: var(--md-surface);
        border: 1px solid var(--md-outline);
        border-radius: 16px;
        box-shadow: 0 1px 3px rgba(16,24,40,0.06), 0 4px 16px rgba(16,24,40,0.04);
    }}

    /* ---- Expanders ---- */
    div[data-testid="stExpander"] details {{
        border: 1px solid var(--md-outline);
        border-radius: 12px;
        background: var(--md-surface-variant);
        overflow: hidden;
    }}
    div[data-testid="stExpander"] summary {{
        font-weight: 600;
        color: var(--md-on-surface);
    }}

    /* ---- Buttons ---- */
    .stButton > button {{
        border-radius: 10px;
        border: 1px solid var(--md-outline);
        font-weight: 600;
        transition: all 0.15s ease;
    }}
    .stButton > button[kind="primary"] {{
        background: var(--md-primary);
        border-color: var(--md-primary);
        color: {c['on_primary']};
        box-shadow: 0 2px 8px rgba(61,90,254,0.30);
    }}
    .stButton > button[kind="primary"]:hover {{
        background: var(--md-primary-dark);
        border-color: var(--md-primary-dark);
    }}

    /* ---- Chat bubbles inside the history container ---- */
    .chat-row {{ display: flex; margin: 0.35rem 0; }}
    .chat-row.user {{ justify-content: flex-end; }}
    .chat-row.assistant {{ justify-content: flex-start; }}
    .chat-bubble {{
        max-width: 82%;
        padding: 0.6rem 0.85rem;
        border-radius: 16px;
        font-size: 0.9rem;
        line-height: 1.4;
        box-shadow: 0 1px 2px rgba(16,24,40,0.08);
        white-space: pre-wrap;
        word-wrap: break-word;
    }}
    .chat-row.user .chat-bubble {{
        background: var(--md-primary);
        color: {c['on_primary']};
        border-bottom-right-radius: 4px;
    }}
    .chat-row.assistant .chat-bubble {{
        background: var(--md-surface-container);
        color: var(--md-on-surface);
        border-bottom-left-radius: 4px;
    }}
    .chat-meta {{
        font-size: 0.68rem;
        color: var(--md-on-surface-variant);
        margin: 0 0.25rem 0.15rem;
    }}

    /* ---- Status chip ---- */
    .status-chip {{
        display: inline-flex; align-items: center; gap: 6px;
        padding: 3px 12px; border-radius: 999px;
        font-size: 0.78rem; font-weight: 600;
    }}
    .status-chip .dot {{ width: 8px; height: 8px; border-radius: 50%; }}
    .status-chip.ok {{ background: rgba(46,125,50,0.12); color: {c['success']}; }}
    .status-chip.ok .dot {{ background: {c['success']}; }}
    .status-chip.err {{ background: rgba(198,40,40,0.12); color: {c['error']}; }}
    .status-chip.err .dot {{ background: {c['error']}; }}

    /* ---- Metric-ish token usage pills ---- */
    .usage-grid {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .usage-pill {{
        flex: 1; min-width: 90px;
        background: var(--md-surface);
        border: 1px solid var(--md-outline);
        border-radius: 12px;
        padding: 8px 12px;
    }}
    .usage-pill .label {{
        font-size: 0.7rem; color: var(--md-on-surface-variant);
        text-transform: uppercase; letter-spacing: 0.04em;
    }}
    .usage-pill .value {{
        font-size: 1.15rem; font-weight: 700; color: var(--md-on-surface);
    }}

    /* ---- Placeholder panels ---- */
    .placeholder {{
        text-align: center;
        padding: 2.4rem 1rem;
        color: var(--md-on-surface-variant);
    }}
    .placeholder .pill {{
        display: inline-block; margin-bottom: 0.6rem;
        padding: 4px 14px; border-radius: 999px;
        background: var(--md-surface-container);
        color: var(--md-primary); font-weight: 600; font-size: 0.78rem;
    }}
    .placeholder h3 {{ margin: 0.2rem 0; color: var(--md-on-surface); }}
    </style>
    """


def inject_theme() -> None:
    """Inject the Material 3 CSS into the current Streamlit page."""
    st.markdown(_css(), unsafe_allow_html=True)
