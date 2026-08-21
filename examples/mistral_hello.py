"""
Simplest Mistral example — small model + API key.

Setup:
  set LLM_KEY=your_mistral_api_key
  # or: set MISTRAL_API_KEY=...

Run (from sdk/ after pip install -e .):
  python examples/mistral_hello.py
  python examples/mistral_hello.py "What files are in this folder?"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pi_sdk import Agent

# Explicit Mistral defaults for this example
PROVIDER = "mistral"
MODEL = "mistral-small-latest"  # cheap/fast; use mistral-large-latest for harder tasks
BASE_URL = "https://api.mistral.ai/v1"


def get_api_key() -> str | None:
    return (
        os.getenv("LLM_KEY")
        or os.getenv("MISTRAL_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )


def main() -> int:
    api_key = get_api_key()
    if not api_key:
        print(
            "Missing API key. Set one of:\n"
            "  LLM_KEY\n"
            "  MISTRAL_API_KEY\n"
            "Example (PowerShell):  $env:LLM_KEY = 'your_key_here'",
            file=sys.stderr,
        )
        return 1

    # Workspace = this repo's sdk folder parent (or cwd)
    workspace = str(Path.cwd())

    agent = Agent.create(
        api_key=api_key,
        provider=PROVIDER,
        model=MODEL,
        base_url=BASE_URL,
        cwd=workspace,
        autonomous=True,
    )

    prompt = (
        " ".join(sys.argv[1:]).strip()
        or "Reply with one short sentence confirming you can help with coding tasks."
    )

    print(f"provider={PROVIDER} model={MODEL}")
    print(f"prompt={prompt!r}\n")

    result = agent.run(prompt)

    print("---")
    print(f"status:     {result.status}")
    print(f"session_id: {result.session_id}")
    print(f"tokens:     {result.usage.total_tokens}")
    print("---")
    print(result.text or result.error or "(empty)")
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
