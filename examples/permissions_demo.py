"""
Permission callback: allow read/grep, deny write/bash by default.

Setup:
  set LLM_KEY=your_mistral_api_key

Run:
  python examples/permissions_demo.py
"""

from __future__ import annotations

import os
import sys

from pi_sdk import Agent, EventType

PROVIDER = "mistral"
MODEL = "mistral-small-latest"

# Tools we allow without human review
ALLOW = {"read", "grep"}


def approve(tool: str, target: str, details: str) -> bool:
    allowed = tool in ALLOW
    print(f"  [permission] {tool} target={target!r} -> {'ALLOW' if allowed else 'DENY'}")
    return allowed


def main() -> int:
    api_key = os.getenv("LLM_KEY") or os.getenv("MISTRAL_API_KEY")
    if not api_key:
        print("Set LLM_KEY or MISTRAL_API_KEY", file=sys.stderr)
        return 1

    def on_event(event):
        if event.type == EventType.TOOL_CALL:
            print(f"  [tool_call] {event.data.get('name')}")
        elif event.type == EventType.TOOL_RESULT:
            content = (event.data.get("content") or "")[:160].replace("\n", " ")
            print(f"  [tool_result] {content}")

    agent = Agent.create(
        api_key=api_key,
        provider=PROVIDER,
        model=MODEL,
        cwd=os.getcwd(),
        autonomous=False,  # require permission_callback
        permission_callback=approve,
        on_event=on_event,
    )

    # This should succeed via read/grep only
    prompt = (
        " ".join(sys.argv[1:]).strip()
        or "Use the read tool on README.md and summarize it in 3 bullets. "
        "Do not use bash or write."
    )
    print(f"prompt: {prompt}\n")
    result = agent.run(prompt)
    print("\n---")
    print(result.text or result.error)
    print(f"status={result.status} session_id={result.session_id}")
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
