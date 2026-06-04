"""Shared error-handling helpers.

Azure OpenAI's content-management policy can block a request *before* it reaches
the model (e.g. a jailbreak / prompt-injection attempt embedded in a generated
test case). The Agent Framework surfaces this as an
``OpenAIContentFilterException`` whose message embeds a Python-``repr`` dict like::

    Error code: 400 - {'error': {'message': '…', 'code': 'content_filter',
      'content_filters': [{'source_type': 'prompt',
        'content_filter_results': {'jailbreak': {'detected': True,
          'filtered': True}, 'hate': {'filtered': False, 'severity': 'safe'},
          …}}]}}

These helpers detect that error and turn it into a short, human-readable summary
so the UI can show *why* a request was blocked instead of a raw traceback. They
are deliberately defensive — the exact text varies across SDK versions, so every
parse path has a fallback and never raises.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

# Prefix used to mark a target response that was blocked by the content filter,
# so downstream UIs can detect and badge it.
CONTENT_FILTER_MARKER = "[CONTENT_FILTER_BLOCKED]"

# Categories whose ``severity`` (rather than a boolean ``filtered``) signals a
# trigger. Anything other than "safe" counts.
_SEVERITY_TRIGGER = {"low", "medium", "high"}


@dataclass
class ContentFilterInfo:
    """Parsed summary of an Azure content-filter block."""

    source: str = "request"  # "prompt" or "completion"
    categories: list[str] = field(default_factory=list)
    code: str = "content_filter"
    message: str = ""
    raw: str = ""

    @property
    def category_label(self) -> str:
        if not self.categories:
            return "content policy"
        return ", ".join(c.replace("_", " ") for c in self.categories)


def _extract_dict(text: str) -> dict | None:
    """Extract and evaluate the first balanced ``{…}`` dict literal in ``text``."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    quote = ""
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if ch == quote:
                in_str = False
            continue
        if ch in ("'", '"'):
            in_str = True
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                snippet = text[start : i + 1]
                try:
                    value = ast.literal_eval(snippet)
                    return value if isinstance(value, dict) else None
                except (ValueError, SyntaxError):
                    return None
    return None


def _categories_from_results(results: dict) -> list[str]:
    cats: list[str] = []
    for name, info in results.items():
        if not isinstance(info, dict):
            continue
        triggered = bool(info.get("filtered")) or bool(info.get("detected"))
        severity = str(info.get("severity", "")).lower()
        if triggered or severity in _SEVERITY_TRIGGER:
            cats.append(name)
    return cats


def _fallback_categories(text: str) -> list[str]:
    """Regex fallback: find ``'<cat>': {… 'filtered': True}`` style triggers."""
    cats: list[str] = []
    pattern = re.compile(
        r"'([a-z_]+)'\s*:\s*\{[^{}]*?'(?:filtered|detected)'\s*:\s*True", re.I
    )
    for match in pattern.finditer(text):
        name = match.group(1)
        if name not in ("content_filter_results",):
            cats.append(name)
    return cats


def is_content_filter_error(text: str | None) -> bool:
    """True when ``text`` looks like an Azure content-filter block."""
    if not text:
        return False
    lowered = text.lower()
    return (
        "content_filter" in lowered
        or "contentfilter" in lowered
        or "content management policy" in lowered
    )


def parse_content_filter(text: str | None) -> ContentFilterInfo | None:
    """Parse a content-filter error string into :class:`ContentFilterInfo`.

    Returns ``None`` when ``text`` is not a recognizable content-filter error.
    """
    if not is_content_filter_error(text):
        return None
    assert text is not None

    info = ContentFilterInfo(raw=text)
    data = _extract_dict(text)
    error = {}
    if isinstance(data, dict):
        error = data.get("error", data) if isinstance(data.get("error"), dict) else data

    info.message = str(error.get("message", "") or "")
    info.code = str(error.get("code", "") or "content_filter")
    param = str(error.get("param", "") or "")

    categories: list[str] = []
    source = param
    for cf in error.get("content_filters", []) or []:
        if not isinstance(cf, dict):
            continue
        source = source or str(cf.get("source_type", "") or "")
        results = cf.get("content_filter_results", {}) or {}
        if isinstance(results, dict):
            categories.extend(_categories_from_results(results))

    if not categories:
        categories = _fallback_categories(text)

    info.categories = list(dict.fromkeys(categories))
    info.source = source or ("prompt" if "'param': 'prompt'" in text else "request")
    return info


def readable_content_filter(text: str | None) -> str | None:
    """Return a one-paragraph, human-readable summary of a content-filter block.

    Returns ``None`` when ``text`` is not a content-filter error.
    """
    info = parse_content_filter(text)
    if info is None:
        return None
    where = "prompt" if "prompt" in (info.source or "") else (info.source or "request")
    return (
        "Azure OpenAI's content safety filter blocked this request before it "
        f"reached the model. It triggered on the {where} for: "
        f"**{info.category_label}**. This is expected when a test sends an "
        "adversarial or policy-violating prompt — the agent's safety system "
        "refused it rather than answering."
    )
