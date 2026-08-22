"""Stream agent events to stdout."""

from __future__ import annotations

import asyncio
import os
import sys

from pi_sdk import Agent, EventType


async def main() -> int:
    api_key = os.getenv("LLM_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Set LLM_KEY or OPENAI_API_KEY", file=sys.stderr)
        return 1

    def on_event(event):
        if event.type == EventType.TEXT_DELTA:
            print(event.text, end="", flush=True)
        elif event.type == EventType.THINKING_DELTA:
            print(event.text, end="", flush=True, file=sys.stderr)
        elif event.type == EventType.TOOL_CALL:
            print(f"\n[tool:{event.data.get('name')}]", flush=True)
        elif event.type == EventType.TOOL_RESULT:
            preview = (event.data.get("content") or "")[:200]
            print(f"[result] {preview}", flush=True)
        elif event.type == EventType.RUN_COMPLETED:
            print("\n--- done ---", flush=True)

    agent = Agent.create(
        api_key=api_key,
        provider=os.getenv("LLM_PROVIDER", "mistral"),
        cwd=os.getcwd(),
        autonomous=True,
        on_event=on_event,
    )
    prompt = " ".join(sys.argv[1:]) or "What is in README.md? Use the read tool."
    result = await agent.run(prompt)
    if result.status != "ok":
        print(result.error or "failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
