"""Agent evaluation utilities built on the **Azure AI Evaluation** SDK.

The Evaluations tab uses these helpers to score the responses produced by the
Foundry ``rfpagent`` agent (captured in ``evaldata/datarfp.jsonl``) across the
full set of applicable metrics:

* **NLP / lexical** (no model needed): F1, BLEU, GLEU, ROUGE-L, METEOR.
* **AI-assisted quality** (judge LLM via :class:`DefaultAzureCredential`):
  groundedness, relevance, coherence, fluency, similarity, retrieval and
  response completeness.
* **Risk & safety** (Azure AI RAI service): violence, sexual, self-harm and
  hate/unfairness — enabled only when subscription / resource-group / project
  are configured.

All authentication uses ``DefaultAzureCredential`` — no keys or service
principals. The judge model talks to the Azure OpenAI endpoint derived from the
Foundry project endpoint using an AAD token provider.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import Settings, require_settings

# The agent whose responses the shipped dataset represents.
AGENT_NAME = "rfpagent"

# Metric categories surfaced in the UI. Order is preserved for display.
CATEGORY_NLP = "NLP / Lexical"
CATEGORY_QUALITY = "AI-assisted Quality"
CATEGORY_SAFETY = "Risk & Safety"
ALL_CATEGORIES = (CATEGORY_NLP, CATEGORY_QUALITY, CATEGORY_SAFETY)

# Human-friendly descriptions per category for the UI.
CATEGORY_HELP = {
    CATEGORY_NLP: "Lexical overlap with the ground truth (no judge model).",
    CATEGORY_QUALITY: "LLM-judged quality of the agent response.",
    CATEGORY_SAFETY: "Content-safety risk via the Azure AI RAI service.",
}


@dataclass
class EvalRun:
    """Structured result of an evaluation batch for the UI."""

    metrics: dict[str, float] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)
    evaluators: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    judge_model: str = ""
    dataset_path: str = ""
    row_count: int = 0
    duration_s: float = 0.0
    studio_url: str | None = None
    error: str | None = None


def _model_config(settings: Settings) -> dict[str, Any]:
    """Build an Azure OpenAI judge-model config (AAD — no api_key)."""
    return {
        "azure_endpoint": settings.aoai_endpoint,
        "azure_deployment": settings.judge_model,
        "api_version": settings.aoai_api_version,
    }


def _build_evaluators(
    categories: tuple[str, ...],
    settings: Settings,
    credential: Any,
) -> tuple[dict[str, Any], dict[str, dict], list[tuple[str, str]]]:
    """Instantiate evaluators + per-evaluator column mappings.

    Returns ``(evaluators, evaluator_config, skipped)`` where ``skipped`` is a
    list of ``(category-or-name, reason)`` pairs to surface in the UI.
    """
    import azure.ai.evaluation as ev

    evaluators: dict[str, Any] = {}
    config: dict[str, dict] = {}
    skipped: list[tuple[str, str]] = []

    def cm(**mapping: str) -> dict:
        return {"column_mapping": {k: f"${{data.{v}}}" for k, v in mapping.items()}}

    # ---- NLP / lexical (no model) -----------------------------------------
    if CATEGORY_NLP in categories:
        evaluators["f1_score"] = ev.F1ScoreEvaluator()
        config["f1_score"] = cm(response="response", ground_truth="ground_truth")
        evaluators["bleu_score"] = ev.BleuScoreEvaluator()
        config["bleu_score"] = cm(response="response", ground_truth="ground_truth")
        evaluators["gleu_score"] = ev.GleuScoreEvaluator()
        config["gleu_score"] = cm(response="response", ground_truth="ground_truth")
        evaluators["meteor_score"] = ev.MeteorScoreEvaluator()
        config["meteor_score"] = cm(response="response", ground_truth="ground_truth")
        try:
            from azure.ai.evaluation import RougeType

            evaluators["rouge_score"] = ev.RougeScoreEvaluator(
                rouge_type=RougeType.ROUGE_L
            )
            config["rouge_score"] = cm(
                response="response", ground_truth="ground_truth"
            )
        except Exception as exc:  # pragma: no cover - defensive
            skipped.append(("rouge_score", f"{type(exc).__name__}: {exc}"))

    # ---- AI-assisted quality (judge model) --------------------------------
    if CATEGORY_QUALITY in categories:
        if not settings.aoai_endpoint or not settings.judge_model:
            skipped.append(
                (
                    CATEGORY_QUALITY,
                    "Judge model endpoint/deployment not configured "
                    "(set AZURE_OPENAI_ENDPOINT / AZURE_EVAL_JUDGE_MODEL).",
                )
            )
        else:
            mc = _model_config(settings)
            rk = {"is_reasoning_model": settings.judge_is_reasoning}
            quality_specs: list[tuple[str, Callable[[], Any], dict]] = [
                (
                    "groundedness",
                    lambda: ev.GroundednessEvaluator(mc, credential=credential, **rk),
                    cm(query="query", context="context", response="response"),
                ),
                (
                    "relevance",
                    lambda: ev.RelevanceEvaluator(mc, credential=credential, **rk),
                    cm(query="query", response="response"),
                ),
                (
                    "coherence",
                    lambda: ev.CoherenceEvaluator(mc, credential=credential, **rk),
                    cm(query="query", response="response"),
                ),
                (
                    "fluency",
                    lambda: ev.FluencyEvaluator(mc, credential=credential, **rk),
                    cm(response="response"),
                ),
                (
                    "similarity",
                    lambda: ev.SimilarityEvaluator(mc, credential=credential, **rk),
                    cm(
                        query="query",
                        response="response",
                        ground_truth="ground_truth",
                    ),
                ),
                (
                    "retrieval",
                    lambda: ev.RetrievalEvaluator(mc, credential=credential, **rk),
                    cm(query="query", context="context"),
                ),
                (
                    "response_completeness",
                    lambda: ev.ResponseCompletenessEvaluator(
                        mc, credential=credential, **rk
                    ),
                    cm(response="response", ground_truth="ground_truth"),
                ),
            ]
            for name, factory, mapping in quality_specs:
                try:
                    evaluators[name] = factory()
                    config[name] = mapping
                except Exception as exc:  # pragma: no cover - defensive
                    skipped.append((name, f"{type(exc).__name__}: {exc}"))

    # ---- Risk & safety (RAI service) --------------------------------------
    if CATEGORY_SAFETY in categories:
        project = settings.project_endpoint
        if not project:
            skipped.append(
                (CATEGORY_SAFETY, "AZURE_AI_PROJECT_ENDPOINT is not configured.")
            )
        else:
            safety_specs: list[tuple[str, Callable[[], Any]]] = [
                ("violence", lambda: ev.ViolenceEvaluator(credential, project)),
                ("sexual", lambda: ev.SexualEvaluator(credential, project)),
                ("self_harm", lambda: ev.SelfHarmEvaluator(credential, project)),
                (
                    "hate_unfairness",
                    lambda: ev.HateUnfairnessEvaluator(credential, project),
                ),
            ]
            for name, factory in safety_specs:
                try:
                    evaluators[name] = factory()
                    config[name] = cm(query="query", response="response")
                except Exception as exc:  # pragma: no cover - defensive
                    skipped.append((name, f"{type(exc).__name__}: {exc}"))

    return evaluators, config, skipped


def _count_rows(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def run_evaluation(
    categories: tuple[str, ...] = ALL_CATEGORIES,
    dataset_path: str | None = None,
) -> EvalRun:
    """Run an evaluation batch and return a structured :class:`EvalRun`.

    The credential is a synchronous ``DefaultAzureCredential``; the judge model
    authenticates via an AAD token provider derived from it.
    """
    from azure.ai.evaluation import evaluate
    from azure.identity import DefaultAzureCredential

    settings = require_settings()
    path = dataset_path or settings.eval_dataset_path
    run = EvalRun(
        judge_model=settings.judge_model,
        dataset_path=path,
        row_count=_count_rows(path),
    )

    credential = DefaultAzureCredential()
    evaluators, config, skipped = _build_evaluators(
        tuple(categories), settings, credential
    )
    run.skipped = skipped

    if not evaluators:
        run.error = (
            "No evaluators could be constructed for the selected categories. "
            "See the skipped list for details."
        )
        return run

    started = time.perf_counter()
    try:
        result = evaluate(
            data=path,
            evaluators=evaluators,
            evaluator_config=config,
            evaluation_name=f"{AGENT_NAME}-eval",
            fail_on_evaluator_errors=False,
        )
    except Exception as exc:
        run.error = f"{type(exc).__name__}: {exc}"
        run.duration_s = round(time.perf_counter() - started, 2)
        return run

    run.duration_s = round(time.perf_counter() - started, 2)
    run.evaluators = list(evaluators.keys())
    run.metrics = dict(result.get("metrics", {}) or {})
    run.rows = list(result.get("rows", []) or [])
    run.studio_url = result.get("studio_url")
    return run
