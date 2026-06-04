"""Application configuration: environment loading and validation.

All environment variables are read here so the rest of the app never touches
``os.environ`` directly. ``get_settings`` is cached so the ``.env`` file is
parsed once per Streamlit session.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

# Load .env once at import time. ``override=False`` keeps real environment
# variables (e.g. those injected by a host) authoritative over the file.
load_dotenv(override=False)


def ensure_utf8_streams() -> None:
    """Make all log output UTF-8 so emoji never crash the app on Windows.

    Several SDKs (notably the Azure AI Evaluation **red-team** scanner) log
    progress messages containing emoji such as ``🚀``. On Windows two things
    default to the legacy cp1252 ("charmap") codec, which cannot encode those
    characters and raises ``UnicodeEncodeError`` mid-scan:

    1. **The standard streams** (``stdout`` / ``stderr``) used by console log
       handlers — reconfigured to UTF-8 below.
    2. **`logging.FileHandler`** opened without an explicit ``encoding`` (the
       red-team logger writes a DEBUG ``redteam.log`` this way). We patch the
       handler so it defaults to UTF-8 instead of the locale encoding.

    Both are best-effort and idempotent — safe to call on every app start.
    """
    for name in ("stdout", "stderr", "__stdout__", "__stderr__"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        encoding = (getattr(stream, "encoding", "") or "").lower()
        if encoding in ("utf-8", "utf8"):
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError, RuntimeError):
            # Stream already detached / not a TextIOWrapper — nothing we can do.
            pass

    _patch_file_handler_utf8()


def _patch_file_handler_utf8() -> None:
    """Default ``logging.FileHandler`` to UTF-8 when no encoding is given.

    Third-party loggers (e.g. the red-team scanner's ``redteam.log``) create a
    DEBUG file handler with no ``encoding``, which on Windows falls back to
    cp1252 and crashes when an emoji is logged. Patching the constructor's
    default is the only way to fix that without editing the SDK. Idempotent.
    """
    import logging

    if getattr(logging.FileHandler, "_utf8_patched", False):
        return

    original_init = logging.FileHandler.__init__

    def init(self, filename, mode="a", encoding=None, delay=False, errors=None):
        if encoding is None:
            encoding = "utf-8"
        if errors is None:
            errors = "backslashreplace"
        original_init(
            self, filename, mode=mode, encoding=encoding, delay=delay, errors=errors
        )

    logging.FileHandler.__init__ = init  # type: ignore[method-assign]
    logging.FileHandler._utf8_patched = True  # type: ignore[attr-defined]


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """Strongly-typed view over the app's environment configuration."""

    project_endpoint: str
    model_deployment: str
    judge_model: str
    aoai_endpoint: str
    aoai_api_version: str
    judge_is_reasoning: bool
    eval_dataset_path: str
    eval_agent_dataset_path: str
    assert_target_agent: str = "rfpagent"
    governance_sponsor_email: str = "agent-owner@contoso.com"
    subscription_id: str = ""
    resource_group: str = ""
    project_name: str = ""
    missing: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_configured(self) -> bool:
        """True when every required Azure setting is present."""
        return not self.missing


def _clean(value: str | None) -> str | None:
    """Return a stripped value or ``None`` when empty/whitespace."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _derive_aoai_endpoint(project_endpoint: str | None) -> str:
    """Derive the Azure OpenAI account endpoint from a Foundry project endpoint.

    ``https://<res>.services.ai.azure.com/api/projects/<proj>`` becomes
    ``https://<res>.services.ai.azure.com`` which is the OpenAI-compatible base
    used by the evaluation judge model.
    """
    if not project_endpoint:
        return ""
    return project_endpoint.split("/api/projects")[0].rstrip("/")


def _is_reasoning_model(model: str) -> bool:
    """Heuristic: reasoning models (o-series, gpt-5+) need max_completion_tokens."""
    name = (model or "").lower().replace(" ", "")
    if name.startswith(("o1", "o3", "o4")):
        return True
    return "gpt-5" in name or "gpt5" in name


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached :class:`Settings` parsed from the environment."""
    project_endpoint = _clean(os.getenv("AZURE_AI_PROJECT_ENDPOINT"))
    model_deployment = _clean(os.getenv("AZURE_AI_MODEL_DEPLOYMENT")) or "gpt-4o-mini"

    judge_model = _clean(os.getenv("AZURE_EVAL_JUDGE_MODEL")) or model_deployment
    aoai_endpoint = _clean(os.getenv("AZURE_OPENAI_ENDPOINT")) or _derive_aoai_endpoint(
        project_endpoint
    )
    aoai_api_version = (
        _clean(os.getenv("AZURE_OPENAI_API_VERSION")) or "2024-10-21"
    )
    reasoning_override = _clean(os.getenv("AZURE_EVAL_JUDGE_IS_REASONING"))
    if reasoning_override is not None:
        judge_is_reasoning = reasoning_override.lower() in ("1", "true", "yes")
    else:
        judge_is_reasoning = _is_reasoning_model(judge_model)

    _here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    eval_dataset_path = _clean(os.getenv("AZURE_EVAL_DATASET")) or os.path.join(
        _here, "evaldata", "datarfp.jsonl"
    )
    eval_agent_dataset_path = _clean(
        os.getenv("AZURE_EVAL_AGENT_DATASET")
    ) or os.path.join(_here, "evaldata", "datarfpagent.jsonl")

    missing: list[str] = []
    if not project_endpoint:
        missing.append("AZURE_AI_PROJECT_ENDPOINT")

    return Settings(
        project_endpoint=project_endpoint or "",
        model_deployment=model_deployment,
        judge_model=judge_model,
        aoai_endpoint=aoai_endpoint,
        aoai_api_version=aoai_api_version,
        judge_is_reasoning=judge_is_reasoning,
        eval_dataset_path=eval_dataset_path,
        eval_agent_dataset_path=eval_agent_dataset_path,
        assert_target_agent=_clean(os.getenv("ASSERT_TARGET_AGENT")) or "rfpagent",
        governance_sponsor_email=_clean(os.getenv("GOVERNANCE_SPONSOR_EMAIL"))
        or "agent-owner@contoso.com",
        subscription_id=_clean(os.getenv("AZURE_SUBSCRIPTION_ID")) or "",
        resource_group=_clean(os.getenv("AZURE_RESOURCE_GROUP_NAME")) or "",
        project_name=_clean(os.getenv("AZURE_AI_PROJECT_NAME")) or "",
        missing=tuple(missing),
    )


def require_settings() -> Settings:
    """Return settings or raise :class:`ConfigError` if anything is missing."""
    settings = get_settings()
    if not settings.is_configured:
        joined = ", ".join(settings.missing)
        raise ConfigError(
            f"Missing required environment variable(s): {joined}. "
            "Copy .env.example to .env and fill in your Foundry project values."
        )
    return settings
