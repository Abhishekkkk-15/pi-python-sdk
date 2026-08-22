"""
Use tools: ask the agent to read a file and grep the package.

Setup:
  set LLM_KEY=your_mistral_api_key

Run (from sdk/):
  python examples/tools_read_grep.py
  python examples/tools_read_grep.py pi_sdk/agent.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from pi_sdk import Agent, EventType

PROVIDER = "mistral"
MODEL = "mistral-small-latest"


async def main() -> int:
    api_key = os.getenv("LLM_KEY") or os.getenv("MISTRAL_API_KEY")
    if not api_key:
        print("Set LLM_KEY or MISTRAL_API_KEY", file=sys.stderr)
        return 1

    target = sys.argv[1] if len(sys.argv) > 1 else "README.md"
    if not Path(target).exists():
        print(f"File not found: {target} (run from the sdk/ directory)", file=sys.stderr)
        return 1

    def on_event(event):
        if event.type == EventType.TOOL_CALL:
            print(f">> {event.data.get('name')}: {event.data.get('arguments')[:120]}...")
        elif event.type == EventType.TEXT_DELTA:
            print(event.text, end="", flush=True)

    agent = Agent.create(
        api_key=api_key,
        provider=PROVIDER,
        model=MODEL,
        cwd=os.getcwd(),
        autonomous=True,
        on_event=on_event,
    )

    prompt = (
        f"1) Read `{target}` with the read tool.\n"
        f"2) Grep this workspace for the string `Agent.create`.\n"
        f"3) Reply with a short summary of what you found (max 8 lines)."
    )
    print(f"model={MODEL}\n")
    result = await agent.run(prompt)
    print("\n\n---")
    if result.status != "ok":
        print(result.error, file=sys.stderr)
        return 1
    print(f"session_id={result.session_id} tokens={result.usage.total_tokens}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
