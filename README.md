# AgenticAIOpsGHCPApp
Agentic AI Ops using Microsoft Foundry, Microsoft Agent framework, Guardrails, Evaluation, Rubric, Red Team, agent governance toolkit

## Agentic AIOps Studio (Streamlit)

> 📖 **New here? Read the [full documentation in `docs/`](docs/README.md)** —
> getting-started guide, per-tab tutorials, architecture (with diagrams), and the
> business value story.

A Material 3, business-professional Streamlit UI that lets you discover the agents
in your Microsoft Foundry project and chat with a selected agent. Multi-agent
execution is powered by the **Microsoft Agent Framework**; agent discovery uses the
**Azure AI Projects** SDK.

### Features

- **Chat tab** — pick a Foundry agent and converse with it.
  - Left container: scrollable conversation history (fixed height 500).
  - Right container: `st.expander`s for **Agent Output**, **Token Usage** and
    **Debug Information** for the latest turn.
  - `st.chat_input` for asking questions.
- **Evaluations tab** — evaluate the Foundry **rfpagent** agent's responses
  (`evaldata/datarfp.jsonl`) with the **Azure AI Evaluation** SDK across all
  applicable metrics, with a one-click **Run / Rerun** button and detailed output:
  - **NLP / lexical**: F1, BLEU, GLEU, ROUGE-L, METEOR.
  - **AI-assisted quality** (LLM judge): groundedness, relevance, coherence,
    fluency, similarity, retrieval, response completeness.
  - **Risk & safety** (Azure AI RAI): violence, sexual, self-harm, hate/unfairness
    (enabled when subscription / resource-group / project are configured).
  - Aggregate metric cards plus a per-row breakdown with judge reasons.
- **Red Team**, **Tracing** tabs — scaffolded placeholders for upcoming functionality.
- One-screen Material 3 layout with a pleasant professional palette.

### Project layout

```
app.py                  # Streamlit entrypoint: theme, header, top tabs
common/                 # reusable utilities shared across tabs
  config.py             # .env loading + validation (DefaultAzureCredential only)
  azure_clients.py      # cached AIProjectClient + credential
  agents.py             # list agents + run an agent via Agent Framework
  chat.py               # conversation session-state helpers
  styles.py             # Material 3 theme / CSS
  ui.py                 # shared UI components
  evaluation.py         # Azure AI Evaluation helpers (rfpagent metrics)
tabs/                   # one module per tab
evaldata/               # evaluation datasets (datarfp.jsonl, datarfpagent.jsonl)
.env.example            # configuration template
```

### Prerequisites

- Python 3.10+
- An Azure account signed in via the Azure CLI (`az login`) with access to your
  Microsoft Foundry project. **Authentication is handled exclusively by
  `DefaultAzureCredential`** — no keys or service-principal secrets are used.

### Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env   # then edit .env with your Foundry endpoint
az login
```

Set in `.env`:

- `AZURE_AI_PROJECT_ENDPOINT` — your Foundry project endpoint, e.g.
  `https://<resource>.services.ai.azure.com/api/projects/<project>`
- `AZURE_AI_MODEL_DEPLOYMENT` — default model deployment (e.g. `gpt-5.4-mini`)

For the **Evaluations** tab (optional — sensible defaults are derived):

- `AZURE_EVAL_JUDGE_MODEL` — chat deployment used as the LLM judge for
  AI-assisted metrics (defaults to `AZURE_AI_MODEL_DEPLOYMENT`). Reasoning models
  (o-series / gpt-5) are detected automatically.
- `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_VERSION` — judge model endpoint
  (defaults to the account endpoint derived from `AZURE_AI_PROJECT_ENDPOINT`).
- `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP_NAME`, `AZURE_AI_PROJECT_NAME` —
  required only to enable the Risk & Safety evaluators.

### Run

```bash
streamlit run app.py
```

The app requires a live, configured Azure connection. If `AZURE_AI_PROJECT_ENDPOINT`
is missing or sign-in fails, the Chat tab shows a friendly error explaining how to
fix it.
