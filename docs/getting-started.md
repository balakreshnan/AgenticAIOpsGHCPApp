# Getting Started

This guide takes you from a fresh clone to a running app. It assumes you know how
to open a terminal, but **nothing** about this project. Follow it top to bottom.

> ⏱️ **Time to first run:** ~15 minutes (most of it is installing packages).

---

## 1. What you need before you start

| Requirement | Why | How to check |
|-------------|-----|--------------|
| **Python 3.10 or newer** | The app is written in Python. | `python --version` |
| **An Azure account** | The app talks to Azure AI Foundry. | You can sign in at [portal.azure.com](https://portal.azure.com). |
| **Azure CLI** (`az`) | Used to sign you in (no secrets needed). | `az version` |
| **A Microsoft Foundry project** | Hosts the agent you will test (e.g. `rfpagent`). | You can see it in [Azure AI Foundry](https://ai.azure.com). |
| **Access to that project** | You must be a member with at least *Reader* + evaluation rights. | Ask your Foundry admin if unsure. |

### The key idea: "DefaultAzureCredential"

This app never asks you for a password, key, or secret. Instead it uses your
existing Azure login. When you run `az login`, Azure stores a token on your
machine; the app picks that token up automatically through a helper called
`DefaultAzureCredential`. Everything the app does in Azure happens **as you**.

---

## 2. Get the code

```bash
git clone https://github.com/balakreshnan/AgenticAIOpsGHCPApp.git
cd AgenticAIOpsGHCPApp
```

---

## 3. Create a virtual environment

A *virtual environment* is a private, isolated copy of Python just for this
project, so its packages don't collide with anything else on your machine.

```bash
# Create it
python -m venv .venv

# Activate it
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate
```

When it's active, your prompt shows `(.venv)` at the start of the line.

---

## 4. Install the packages

```bash
pip install -r requirements.txt

# The RAMPART tab needs the RAMPART framework. It pins a PyRIT version that
# conflicts with azure-ai-evaluation, so install it WITHOUT dependencies — the
# app only uses its deterministic probe path, which doesn't need that PyRIT:
pip install --no-deps -r requirements-rampart.txt
```

This pulls in everything the app needs. Here is what each package is for:

| Package | Powers… |
|---------|---------|
| `streamlit` | The web user interface. |
| `azure-ai-projects` | Discovering agents in your Foundry project. |
| `azure-identity` | `DefaultAzureCredential` sign-in. |
| `agent-framework` + `agent-framework-foundry` | Running (orchestrating) the agents. |
| `azure-ai-evaluation[redteam]` | The Evaluations and Red Team tabs. |
| `assert-ai` | The Assert tab (spec-driven testing). |
| `agent-governance-toolkit[full]` | The Governance tab. |
| `RAMPART` *(installed `--no-deps`)* | The RAMPART tab (behavioural safety probes). |
| `python-dotenv` | Reading your `.env` configuration file. |

> 💡 Installation can take a few minutes — these are large Azure SDKs.

---

## 5. Sign in to Azure

```bash
az login
```

A browser window opens. Sign in with the corporate account that has access to
your Foundry project. After this, the app can reach Azure as you.

> If your organization uses multiple tenants, run
> `az login --tenant <your-tenant-id>` to pick the right one.

---

## 6. Configure the app (`.env`)

The app reads its settings from a file called `.env` in the project root. A
template is provided — copy it and fill in the blanks:

```bash
# Windows
copy .env.example .env
# macOS / Linux
cp .env.example .env
```

Open `.env` in any editor. The **only required** value is your Foundry project
endpoint:

```ini
AZURE_AI_PROJECT_ENDPOINT=https://<your-resource>.services.ai.azure.com/api/projects/<your-project>
```

> 📍 **Where do I find this?** In [Azure AI Foundry](https://ai.azure.com), open
> your project → **Overview** → copy the *Project endpoint*.

Everything else is optional and has sensible defaults. The most useful optional
settings:

| Setting | Default | When to change it |
|---------|---------|-------------------|
| `AZURE_AI_MODEL_DEPLOYMENT` | `gpt-5.4-mini` | If your default model deployment is named differently. |
| `AZURE_EVAL_JUDGE_MODEL` | same as above | To use a different model as the "judge" for AI-graded metrics. |
| `AZURE_OPENAI_ENDPOINT` | derived from the project endpoint | Rarely — only if your OpenAI endpoint differs. |
| `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP_NAME`, `AZURE_AI_PROJECT_NAME` | empty | To unlock the **Risk & Safety** evaluators in the Evaluations tab. |
| `ASSERT_TARGET_AGENT` | `rfpagent` | If the agent you want to test has a different name. |

> The `.env` file is listed in `.gitignore`, so your settings never get committed.

---

## 7. Run the app

```bash
streamlit run app.py
```

Streamlit prints a local URL (usually `http://localhost:8501`) and opens your
browser. You should see the **Agentic AIOps Studio** header and seven tabs.

---

## 8. Confirm it works

1. Land on the **💬 Chat** tab.
2. Look at the top-right status chip — it should say **"N agent(s)"** in green.
3. Pick an agent from the dropdown (e.g. `rfpagent`).
4. Type a question in the box at the bottom and press Enter.
5. Watch the left panel fill with the conversation and the right panel show the
   token usage and debug details.

🎉 **That's a successful first run.**

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Red **"Disconnected"** chip on the Chat tab | Not signed in, or wrong endpoint. | Re-run `az login`; double-check `AZURE_AI_PROJECT_ENDPOINT`. |
| *"Missing required environment variable(s)"* | `.env` not filled in. | Set `AZURE_AI_PROJECT_ENDPOINT` in `.env`. |
| *"No agents available"* | Your account can't see agents in the project. | Ask your Foundry admin to grant access. |
| Evaluations tab skips **Risk & Safety** | Subscription / resource-group / project not set. | Fill those three values in `.env`. |
| Tracing tab says *"No Application Insights resource is connected"* | The project has no telemetry sink. | Connect Application Insights in the Foundry project's *Tracing* settings. |
| `az login` opens the wrong tenant | Multi-tenant account. | `az login --tenant <tenant-id>`. |

If you change `.env` while the app is running, **stop** the app (`Ctrl+C` in the
terminal) and **start it again** so the new settings load.

---

➡️ **Next:** open **[tutorials.md](tutorials.md)** for a guided tour of every tab.
