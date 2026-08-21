"""One-shot agent run."""

from __future__ import annotations

import os
import sys

from pi_sdk import Agent


def main() -> int:
    api_key = os.getenv("LLM_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Set LLM_KEY or OPENAI_API_KEY", file=sys.stderr)
        return 1

    agent = Agent.create(
        api_key=api_key,
        provider=os.getenv("LLM_PROVIDER", "mistral"),
        model=os.getenv("LLM_MODEL"),
        cwd=os.getcwd(),
        autonomous=True,
    )

    prompt = " ".join(sys.argv[1:]) or "Summarize this repository in 5 bullets."
    result = agent.run(prompt)
    print(f"status={result.status} session={result.session_id}")
    print(result.text or result.error or "")
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
