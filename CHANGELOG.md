# Changelog

## 0.3.0 — 2026-08-22

**Breaking:** PI SDK is **async-only**. All I/O methods require `await`.

### Changed
- `Agent.run`, `send`, `stream`, `resume`, `new_session`, `list_sessions`, `grant_permission` are async
- `Agent.add_tool`, `remove_tool`, `disable_tools` are async
- `LLMProvider.complete` is async (`AsyncOpenAI`, async streaming)
- `SessionStore` / `Memory` methods are async (`aiofiles` for disk)
- MongoDB backend uses **Motor** instead of pymongo (`pip install pi-sdk[mongodb]`)
- Built-in tools (`read`, `write`, `edit`, `bash`, `grep`, `web_search`) are async
- `EventEmitter.emit` supports async `on_event` callbacks
- `Skills` loading methods are async

### Added
- Dependencies: `aiofiles`, `httpx`
- `examples/async_hello.py`

### Migration (0.2.x → 0.3.0)

```python
# Before
result = agent.run(prompt)
agent.resume(session_id)

# After
import asyncio

async def main():
    result = await agent.run(prompt)
    await agent.resume(session_id)

asyncio.run(main())
```

In FastAPI:

```python
@router.post("/run")
async def run(body: RunRequest):
    result = await agent.run(body.prompt)
    return {"text": result.text, "session_id": result.session_id}
```

`Agent.create(...)` remains synchronous.

## 0.2.2

- Prior sync release (custom tools, disable_tools, MongoDB storage, etc.)
