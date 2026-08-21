"""
Register a custom tool (name, description, parameters, handler).

Setup:
  set LLM_KEY=your_mistral_api_key

Run:
  python examples/custom_tool.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from pi_sdk import Agent


def main() -> int:
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

    def utc_now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    agent.add_tool(
        name="utc_now",
        description="Return the current UTC date and time as a string.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=lambda: utc_now(),
        require_permission=False,
    )

    # Shorthand parameters form
    agent.add_tool(
        name="echo_upper",
        description="Echo the given text in UPPERCASE.",
        parameters={
            "text": {
                "type": "string",
                "description": "Text to uppercase",
            },
        },
        handler=lambda text: str(text).upper(),
        require_permission=False,
    )

    print("tools:", agent.list_tools())
    prompt = (
        " ".join(sys.argv[1:]).strip()
        or "Call utc_now, then echo_upper with text 'hello pi'. "
        "Reply with both results in two short lines."
    )
    result = agent.run(prompt)
    print(result.text or result.error)
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
