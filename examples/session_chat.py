"""
Multi-turn chat: return session_id to the "user", then resume.

Demonstrates the pattern for a cloud API:
  1) first message  -> create session, send session_id back
  2) follow-ups     -> resume(session_id) + run(next_prompt)

Setup:
  set LLM_KEY=your_mistral_api_key

Run:
  python examples/session_chat.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from pi_sdk import Agent

PROVIDER = "mistral"
MODEL = "mistral-small-latest"


def get_api_key() -> str | None:
    return os.getenv("LLM_KEY") or os.getenv("MISTRAL_API_KEY")


def make_agent() -> Agent:
    api_key = get_api_key()
    if not api_key:
        raise SystemExit("Set LLM_KEY or MISTRAL_API_KEY")
    return Agent.create(
        api_key=api_key,
        provider=PROVIDER,
        model=MODEL,
        cwd=os.getcwd(),
        autonomous=True,
    )


async def turn(agent: Agent, prompt: str, label: str) -> str:
    print(f"\n=== {label} ===")
    print(f"user: {prompt}")
    result = await agent.run(prompt)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"assistant: {result.text}")
    print(f"(session_id={result.session_id}, tokens={result.usage.total_tokens})")
    assert result.session_id
    return result.session_id


async def main() -> int:
    agent = make_agent()
    session_id = await turn(
        agent,
        "Remember that my favorite color is teal. Reply in one sentence.",
        "turn 1 (new session)",
    )

    print("\n... client stores session_id and comes back later ...\n")

    agent2 = make_agent()
    await agent2.resume(session_id)
    await turn(
        agent2,
        "What is my favorite color? One word only.",
        "turn 2 (resumed)",
    )

    await turn(
        agent2,
        "Thanks. List the tools you have available in one short line.",
        "turn 3 (same agent)",
    )

    print(f"\nDone. Reuse this session_id later: {session_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
