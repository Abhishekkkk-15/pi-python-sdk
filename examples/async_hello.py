"""Async agent smoke test."""

from __future__ import annotations

import asyncio
import os
import sys

from pi_sdk import Agent


async def main() -> int:
    api_key = os.getenv("LLM_KEY") or os.getenv("MISTRAL_API_KEY")
    if not api_key:
        print("Set LLM_KEY or MISTRAL_API_KEY", file=sys.stderr)
        return 1

    agent = Agent.create(
        api_key=api_key,
        provider="mistral",
        model="mistral-small-latest",
        cwd=os.getcwd(),
        autonomous=True,
    )
    result = await agent.run(
        " ".join(sys.argv[1:]).strip()
        or "Say hi in one sentence."
    )
    print(result.text or result.error)
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
