# PI SDK

Headless Python coding-agent SDK. Embed an agent in a backend, cloud worker, CI job, or automation — **no CLI, no Rich UI**.

Built from the same core as the PI harness: tools, providers, sessions, compaction, skills, permissions.

| | |
|---|---|
| Package | `pi-sdk` (`import pi_sdk`) |
| Python | 3.11+ |
| Entry | `Agent.create` → `run` / `stream` / `resume` |

---

## Install

```bash
cd sdk
pip install -e .
# or
uv pip install -e .
```

Requirements (pulled in automatically): `openai`, `python-dotenv`, `tokenizers`, `tavily-python`, `google-genai`.

---

## Quick start

```python
from pi_sdk import Agent

agent = Agent.create(
    api_key="sk-...",                 # or LLM_KEY / OPENAI_API_KEY
    provider="mistral",               # mistral | openai | groq | vertex | custom
    model="mistral-large-latest",
    cwd=".",                          # workspace tools read/write
    autonomous=True,                  # allow tools without prompts (servers)
)

result = agent.run("List Python files and summarize this repo")
print(result.status)       # "ok" | "error"
print(result.text)         # final assistant reply
print(result.session_id)   # give this back to your user/client
print(result.usage.total_tokens)
```

`agent.send(...)` is an alias for `agent.run(...)`.

---

## Returning a session to the user

Every successful run creates (or continues) a session. **Send `session_id` to the client** so they can continue later.

### First message (new chat)

```python
result = agent.run(user_prompt)
# HTTP/JSON response to the user:
payload = {
    "session_id": result.session_id,
    "text": result.text,
    "status": result.status,
    "usage": {
        "prompt_tokens": result.usage.prompt_tokens,
        "completion_tokens": result.usage.completion_tokens,
        "total_tokens": result.usage.total_tokens,
        "estimated_cost_usd": result.usage.estimated_cost_usd,
    },
}
```

### Follow-up message (same chat)

Client sends `session_id` + next prompt:

```python
agent = Agent.create(api_key="...", cwd=workspace, autonomous=True)
agent.resume(session_id)              # load history from disk
result = agent.run(next_prompt)
# return result.session_id again (same id)
```

### Other session helpers

```python
agent.session_id                      # current id or None
agent.messages                        # list[Message]
agent.list_sessions()                 # all sessions in data_dir
agent.new_session("title")            # force a fresh session
agent.reset_conversation()            # clear in-memory history
```

### Where sessions are stored

**Disk (default)** — under `~/.pi-sdk/` (or `data_dir` / `PI_SDK_DATA_DIR`):

```
~/.pi-sdk/
├── <session-id>/
│   ├── metadata.json
│   └── conversation_history.jsonl
└── skills/
```

**MongoDB (opt-in)** — session metadata + chat messages only (not workspace files):

```bash
pip install "pi-sdk[mongodb]"
```

```python
agent = Agent.create(
    api_key="...",
    storage="mongodb",
    mongodb_uri="mongodb://localhost:27017",  # or PI_SDK_MONGODB_URI / MONGODB_URI
    mongodb_db="pi_sdk",
    user_id="user_42",   # optional tenant scope for list/resume
    cwd="/workspace/proj",
)
result = agent.run("...")
# later:
agent2 = Agent.create(..., storage="mongodb", mongodb_uri="...", user_id="user_42")
agent2.resume(result.session_id)
```

Collections: `sessions` (`_id` = session id) and `messages` (`session_id` + `seq`).  
Workspace **files** stay on the filesystem (`cwd`); Mongo only holds conversation state.

You can also inject a custom store: `Agent.create(store=my_store, ...)`.

The **workspace** (`cwd`) is the project the tools edit — independent of session storage.

---

## Agent.create options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `api_key` | `str` | env `LLM_KEY` / `OPENAI_API_KEY` | Primary provider key |
| `api_keys` | `list[str]` | `[]` | Primary + secondary; rotates on 429 |
| `provider` | `str` | `"mistral"` | `mistral`, `openai`, `groq`, `vertex`, or custom name |
| `model` | `str` | provider default | Model id |
| `base_url` | `str` | builtin | OpenAI-compatible endpoint (or Vertex location) |
| `cwd` | `str`/`Path` | process cwd | Workspace for tools / skills |
| `data_dir` | `str`/`Path` | `~/.pi-sdk` | Disk session root (when `storage="disk"`) |
| `storage` | `str` | `"disk"` | `"disk"` or `"mongodb"` |
| `mongodb_uri` | `str` | `PI_SDK_MONGODB_URI` / `MONGODB_URI` | Mongo connection string |
| `mongodb_db` | `str` | `"pi_sdk"` | Mongo database name |
| `user_id` | `str` | `None` | Tenant scope for create/list/resume |
| `store` | `SessionStore` | `None` | Inject custom store (wins over `storage`) |
| `tavily_api_key` | `str` | `TAVILY_API_KEY` | Enables `web_search` |
| `autonomous` | `bool` | `True` | Skip permission checks |
| `permission_callback` | `callable` | `None` | `(tool, target, details) -> bool` |
| `on_event` | `callable` | `None` | Live `AgentEvent` handler |
| `compaction_enabled` | `bool` | `True` | Auto-summarize long context |
| `compact_at_tokens` | `int` | `80000` | Compaction threshold |
| `keep_recent_tokens` | `int` | `20000` | Raw tail kept after compact |
| `max_tokens` | `int` | `None` | Completion cap |
| `reasoning_effort` | `str` | `None` | `low` / `medium` / `high` |
| `skill_names` | `list[str]` | `None` | Pin skills (skip auto-select) |
| `system_prompt_extra` | `str` | `None` | Appended to system prompt |
| `input_price_per_mtok` / `output_price_per_mtok` | `float` | `0` | Cost estimate inputs |

Environment variables: `LLM_KEY`, `LLM_PROVIDER`, `LLM_MODEL`, `TAVILY_API_KEY`, `PI_SDK_DATA_DIR`, `PI_SDK_MONGODB_URI`, `MONGODB_URI`.

---

## RunResult

```python
@dataclass
class RunResult:
    status: str                 # "ok" | "error"
    text: str                   # final assistant text
    reasoning: str | None       # model thinking if present
    session_id: str | None
    usage: UsageSummary
    events: list[AgentEvent]    # if collect_events=True
    error: str | None
    messages: list[Message]
```

```python
result = agent.run(prompt, collect_events=True)
for e in result.events:
    print(e.type, e.data)
```

---

## Events & streaming

### Live callback (best for WebSocket / SSE)

```python
from pi_sdk import Agent, EventType

def on_event(event):
    if event.type == EventType.TEXT_DELTA:
        push_sse({"delta": event.text})
    elif event.type == EventType.TOOL_CALL:
        push_sse({"tool": event.data["name"], "args": event.data["arguments"]})
    elif event.type == EventType.TOOL_RESULT:
        push_sse({"tool_result": event.data["content"][:500]})
    elif event.type == EventType.RUN_COMPLETED:
        push_sse({"done": True, "session_id": event.data.get("session_id")})

agent = Agent.create(api_key="...", on_event=on_event, autonomous=True)
agent.run("Refactor utils.py")
```

You can also set the handler later: `agent.on_event(callback)`.

### Iterator API

```python
for event in agent.stream("Explain agent.py"):
    print(event.type.value, event.data)
```

### Event types

| `EventType` | When | Useful `data` keys |
|-------------|------|--------------------|
| `RUN_STARTED` | Turn begins | `prompt`, `session_id` |
| `USER_MESSAGE` | User text stored | `text` |
| `THINKING_DELTA` / `THINKING` | Reasoning stream / final | `text` |
| `TEXT_DELTA` / `TEXT` | Assistant stream / final | `text` |
| `TOOL_CALL` | Before tool runs | `name`, `arguments`, `id` |
| `TOOL_RESULT` | After tool runs | `name`, `content`, `id` |
| `PERMISSION_REQUEST` | Needs approval | `tool`, `target`, `details` |
| `COMPACTION` | Context summarized | `message` |
| `USAGE` | Token totals updated | token fields |
| `ERROR` | Failure | `error` |
| `STATUS` | Soft status (e.g. key rotate) | `message` |
| `RUN_COMPLETED` / `RUN_FAILED` | Turn end | `text` / `error`, `session_id` |

`event.text` is a shortcut for `data["text"]` or `data["content"]`.

---

## Permissions

| Mode | Behavior |
|------|----------|
| `autonomous=True` (default) | All tools allowed |
| `autonomous=False` + `permission_callback` | Your function decides |
| `autonomous=False`, no callback | **Deny** (safe default for cloud) |

```python
def approve(tool: str, target: str, details: str) -> bool:
    if tool in ("read", "grep"):
        return True
    if tool == "bash" and target.startswith("pytest"):
        return True
    return False

agent = Agent.create(
    api_key="...",
    autonomous=False,
    permission_callback=approve,
)
```

Persist grants on the session (optional):

```python
from pi_sdk import PermissionDecision

agent.grant_permission(PermissionDecision.ALWAYS_TOOL, "read", ".")
# ALWAYS_TARGET | ALWAYS_ALL | ALLOW_ONCE also available
```

---

## Tools

| Tool | Purpose | Notes |
|------|---------|--------|
| `read` | Read file (offset/limit) | Text files |
| `write` | Create / overwrite file | |
| `edit` | Exact string replacements | |
| `bash` | Shell command | timeout, background |
| `grep` | Workspace search | glob, case fold |
| `web_search` | Live web search | needs Tavily key |

---

## Providers

### Built-ins

| Provider | Default model | `base_url` |
|----------|---------------|------------|
| `mistral` | `mistral-large-latest` | `https://api.mistral.ai/v1` |
| `openai` | `gpt-4o` | `https://api.openai.com/v1` |
| `groq` | `llama-3.3-70b-versatile` | `https://api.groq.com/openai/v1` |
| `vertex` | `gemini-2.5-flash` | GCP location (e.g. `us-central1`) |

### Examples

```python
# OpenAI
Agent.create(provider="openai", api_key="...", model="gpt-4o")

# Custom OpenAI-compatible gateway
Agent.create(
    provider="custom",
    api_key="...",
    base_url="https://my-gateway.example/v1",
    model="my-model",
)

# Dual keys — automatic retry on HTTP 429
Agent.create(api_key="primary", api_keys=["primary", "secondary"], provider="mistral")
```

---

## Skills

Markdown skills loaded from the workspace (first match wins):

1. `<cwd>/.pi-sdk/skills/`
2. `<cwd>/.pi-python/skills/`
3. `<cwd>/skills/`
4. `<data_dir>/skills/`

Layouts: `skills/deploy/SKILL.md` or `skills/lint.md`.

- **Auto:** each turn, the model picks up to 3 relevant skills.
- **Pinned:** `Agent.create(skill_names=["deploy", "lint"])` skips auto-select.

---

## Compaction

Long sessions are summarized so the context window does not explode.

- Controlled by `compaction_enabled`, `compact_at_tokens`, `keep_recent_tokens`
- Manual: `agent.run_compaction(force=True)`

---

## Cloud / API integration pattern

```python
from pi_sdk import Agent, EventType

def handle_chat(user_id: str, prompt: str, session_id: str | None, workspace: str):
    agent = Agent.create(
        api_key=...,
        cwd=workspace,          # e.g. cloned repo on a VM
        data_dir=f"/data/pi/{user_id}",
        autonomous=True,
        on_event=lambda e: bus.publish(user_id, e),  # WebSocket/SSE
    )
    if session_id:
        agent.resume(session_id)
    result = agent.run(prompt)
    return {
        "session_id": result.session_id,
        "text": result.text,
        "status": result.status,
        "error": result.error,
    }
```

See `examples/cloud_worker.py` for a minimal worker that returns JSON + events.

---

## Examples

All examples default to **Mistral** (`mistral-small-latest`) unless noted.

```bash
cd sdk
pip install -e .

# PowerShell
$env:LLM_KEY = "your_mistral_api_key"
# bash
export LLM_KEY=your_mistral_api_key

python examples/mistral_hello.py
python examples/mistral_hello.py "Say hello in one line"

python examples/session_chat.py          # multi-turn + resume(session_id)
python examples/mongodb_session.py       # MongoDB create + resume (needs URI)
python examples/tools_read_grep.py       # read + grep tools
python examples/permissions_demo.py      # allow read/grep, deny write/bash
python examples/stream_events.py "What is in README.md?"
python examples/basic.py "Summarize this repo"
python examples/cloud_worker.py . "List the top-level files"
```

| Script | What it shows |
|--------|----------------|
| `mistral_hello.py` | Minimal Mistral key + small model |
| `session_chat.py` | Return `session_id`, resume later (disk) |
| `mongodb_session.py` | Same flow with `storage="mongodb"` + `user_id` |
| `tools_read_grep.py` | Tool use with live events |
| `permissions_demo.py` | `permission_callback` allow/deny |
| `stream_events.py` | Token streaming to stdout |
| `basic.py` | One-shot run |
| `cloud_worker.py` | JSON worker payload for APIs |

---

## Public exports

```python
from pi_sdk import (
    Agent,
    AgentError,
    AuthenticationError,
    AgentEvent,
    AgentOptions,
    Config,
    DiskSessionStore,
    EventType,
    Message,
    PermissionDecision,
    Role,
    RunResult,
    Session,
    SessionStore,
    UsageSummary,
    create_store,
    BUILTIN_PROVIDERS,
)
```

---

## Errors

| Exception | When |
|-----------|------|
| `AuthenticationError` | No API key (non-Vertex) at `create` |
| `AgentError` | e.g. `resume` with unknown `session_id` |
| `RunResult.status == "error"` | Turn failed; see `result.error` |

---

## Package layout

```
sdk/
├── pi_sdk/
│   ├── __init__.py       # public API
│   ├── agent.py          # Agent.create / run / stream / resume
│   ├── events.py         # EventType, AgentEvent
│   ├── config.py         # AgentOptions, Config
│   ├── memory.py         # façade over SessionStore
│   ├── models.py         # Message, Session, Role
│   ├── paths.py          # data_dir / workspace helpers
│   ├── storage/          # disk + mongodb backends
│   ├── tools.py
│   ├── permissions.py
│   ├── compaction.py
│   ├── skills.py
│   ├── tokenizer.py
│   ├── prompts/
│   └── providers/
├── examples/
├── pyproject.toml
└── README.md
```

The parent repo CLI (`../agent.py`, `../console.py`, …) is separate. This package under `sdk/` is standalone.

---

## Relation to the CLI

| | CLI (repo root) | SDK (`sdk/`) |
|--|-----------------|--------------|
| UI | Rich + slash commands | None |
| Auth | `/login`, `auth.json` | `Agent.create(api_key=...)` |
| Permissions | Interactive prompts | `autonomous` / callback |
| Sessions | `.pi-python` | `.pi-sdk` (configurable) |
| Use case | Local terminal | Backend / cloud / embed |

---

**PI SDK** © 2026 · MIT
