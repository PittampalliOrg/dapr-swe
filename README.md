# dapr-swe: Distributed Coding Agent on Dapr Workflows + Agents

Async coding agent triggered by GitHub issue comments (`@dapr-swe` mention). Uses **Dapr Workflows** for durable orchestration and a team of specialized AI agents (**Planner**, **Developer**, **Reviewer**) running inside **OpenShell** sandboxes.

## Architecture

```
GitHub Issue Comment (@dapr-swe)
        │
        ▼
  GitHub Webhook ──► FastAPI ──► Dapr Workflow (resolve_issue)
                                    │
                                    ├─ 1. initialize_context  (create sandbox, clone repo, read AGENTS.md)
                                    ├─ 2. create_plan         (PlannerAgent explores codebase, produces steps)
                                    ├─ 3. implement_step      (DeveloperAgent, called per plan step)
                                    ├─ 4. review_changes      (ReviewerAgent checks full diff)
                                    ├─ 5. commit_and_open_pr  (commit, push branch, open GitHub PR)
                                    └─ 6. notify_completion   (post summary comment on issue)
```

Each workflow activity runs as a Dapr activity with automatic state checkpointing backed by Redis, so the pipeline survives restarts and can be replayed deterministically.

## How to Use

Comment on any issue in a **PittampalliOrg** repo:

```
@dapr-swe <describe what you want done>
```

The bot picks up the mention via webhook, plans the work, implements it in a sandboxed environment, reviews its own changes, and opens a pull request.

## Agent Team

| Agent | Role | Tools |
|-------|------|-------|
| **PlannerAgent** | Explores the codebase and produces a step-by-step implementation plan | `list_dir`, `read_file`, `search_files`, `execute` (sandbox shell) |
| **DeveloperAgent** | Implements code changes for each plan step | `execute`, `write_file`, `read_file` (sandbox shell) |
| **ReviewerAgent** | Senior code reviewer -- reviews the full git diff for correctness, style, and completeness | _(LLM-only, no tools)_ |

All agents use `dapr_agents.DurableAgent` backed by `anthropic/claude-sonnet-4-6` (configurable via `LLM_MODEL_ID`).

## Project Structure

```
dapr-swe/
├── src/
│   ├── main.py                  # FastAPI app, Dapr Workflow runtime setup
│   ├── config.py                # Environment variables
│   ├── agents/
│   │   ├── planner.py           # PlannerAgent factory + tools
│   │   ├── developer.py         # DeveloperAgent factory + tools
│   │   └── reviewer.py          # ReviewerAgent factory
│   ├── prompts/
│   │   ├── planner.py           # Planner system prompt
│   │   ├── developer.py         # Developer system prompt
│   │   └── reviewer.py          # Reviewer system prompt
│   ├── sandbox/
│   │   └── openshell.py         # OpenShell sandbox backend client
│   ├── tools/
│   │   ├── github.py            # GitHub API helpers
│   │   ├── git.py               # Git operations
│   │   ├── sandbox.py           # Sandbox tool wrappers
│   │   └── web.py               # Web search tools
│   ├── integrations/
│   │   └── github_app.py        # GitHub App auth (JWT + installation tokens)
│   ├── webhook/
│   │   ├── github.py            # Webhook route + HMAC verification
│   │   └── models.py            # Pydantic models for webhook payloads
│   └── workflow/
│       ├── resolve_issue.py     # Dapr Workflow definition
│       └── activities.py        # Workflow activity implementations
├── dapr/                        # Dapr component configs (state store, pubsub)
├── k8s/                         # Kubernetes manifests
├── tests/
├── Dockerfile
└── pyproject.toml
```

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `GITHUB_APP_ID` | GitHub App ID | _(required)_ |
| `GITHUB_APP_PRIVATE_KEY` | GitHub App private key (PEM) | _(required)_ |
| `GITHUB_APP_INSTALLATION_ID` | GitHub App installation ID | _(required)_ |
| `GITHUB_WEBHOOK_SECRET` | Webhook HMAC secret | _(required)_ |
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude | _(required)_ |
| `OPENSHELL_RUNTIME_URL` | OpenShell sandbox API endpoint | `http://openshell-agent-runtime.openshell.svc.cluster.local:8083` |
| `OPENSHELL_COMMAND_TIMEOUT_MS` | Sandbox command timeout (ms) | `600000` |
| `LLM_MODEL_ID` | LLM model identifier | `anthropic/claude-sonnet-4-6` |
| `DEFAULT_REPO_OWNER` | Default GitHub org | `PittampalliOrg` |
| `DAPR_STATE_STORE` | Dapr state store component name | `dapr-swe-statestore` |
| `DAPR_PUBSUB` | Dapr pub/sub component name | `pubsub` |

## Deployment

Deployed on Kubernetes in the `workflow-builder` namespace with a Dapr sidecar. Key infrastructure:

- **Sandbox**: OpenShell agent runtime (in-cluster, `openshell` namespace)
- **State store**: Redis (Dapr component)
- **Webhook ingress**: Tailscale Funnel

## Comparison with Open-SWE

| | **Open-SWE** | **Dapr-SWE** |
|---|---|---|
| Orchestration | LangGraph | Dapr Workflows |
| Architecture | Monolithic single-agent | Multi-agent team (Planner + Developer + Reviewer) |
| State | In-memory | Redis-backed durable state (survives restarts) |
| Sandbox | Docker / Modal | OpenShell (Kubernetes-native) |
| Agent framework | LangChain | dapr-agents (`DurableAgent`) |

## URLs

| Endpoint | URL |
|----------|-----|
| Webhook | `https://dapr-swe-webhooks-ryzen.tail286401.ts.net/webhooks/github` |
| Health | `https://dapr-swe-webhooks-ryzen.tail286401.ts.net/healthz` |
| Readiness | `https://dapr-swe-webhooks-ryzen.tail286401.ts.net/readyz` |
