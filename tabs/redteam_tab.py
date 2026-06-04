"""Red Team tab (scaffolded placeholder)."""

from __future__ import annotations

from common.ui import placeholder


def render() -> None:
    placeholder(
        "Red Team Scans",
        "Probe your agents for safety and security risks with automated adversarial "
        "attacks using the Azure AI Projects red team (preview) capability.",
        [
            "Attack strategies across risk categories",
            "Attack success rate and per-category breakdown",
            "Conversation transcripts for failed defenses",
            "Scorecards to track hardening over time",
        ],
    )
