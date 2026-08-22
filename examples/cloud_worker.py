"""Minimal FastAPI-style cloud worker sketch (no server deps required to import)."""

from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable

from pi_sdk import Agent, AgentEvent, EventType, RunResult


async def run_cloud_task(
    *,
    prompt: str,
    workspace: str,
    api_key: str | None = None,
    provider: str = "mistral",
    model: str | None = None,
    on_event: Callable[[AgentEvent], None | Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """
    Run an agent against a checked-out workspace (e.g. cloned repo on a VM).

    Wire `on_event` to WebSocket / SSE push in your API layer.
    """
    agent = Agent.create(
        api_key=api_key or os.getenv("LLM_KEY"),
        provider=provider,
        model=model,
        cwd=workspace,
        autonomous=True,
        on_event=on_event,
    )
    result: RunResult = await agent.run(prompt, collect_events=True)
    return {
        "status": result.status,
        "text": result.text,
        "session_id": result.session_id,
        "error": result.error,
        "usage": {
            "prompt_tokens": result.usage.prompt_tokens,
            "completion_tokens": result.usage.completion_tokens,
            "total_tokens": result.usage.total_tokens,
            "estimated_cost_usd": result.usage.estimated_cost_usd,
        },
        "events": [
            {"type": e.type.value, "data": e.data}
            for e in result.events
            if e.type
            not in (EventType.TEXT_DELTA, EventType.THINKING_DELTA)
        ],
    }


if __name__ == "__main__":
    import json
    import sys

    ws = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    prompt = sys.argv[2] if len(sys.argv) > 2 else "Summarize this repo briefly."
    print(json.dumps(asyncio.run(run_cloud_task(prompt=prompt, workspace=ws)), indent=2))
