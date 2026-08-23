"""
Run commands inside a Docker container with the docker_bash tool.

Setup:
  export LLM_KEY=your_mistral_api_key
  export PI_SDK_DOCKER_CONTAINER=my-app   # container name or ID

Run (from sdk/):
  python examples/docker_bash.py
  python examples/docker_bash.py my-app-container
  python examples/docker_bash.py my-app-container "Show pwd and list files in /app"
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

from pi_sdk import Agent, EventType

PROVIDER = "mistral"
MODEL = "mistral-small-latest"


def _resolve_container(argv: list[str]) -> str | None:
    if len(argv) > 1 and argv[1].strip():
        return argv[1].strip()
    for key in ("PI_SDK_DOCKER_CONTAINER", "DOCKER_CONTAINER"):
        val = (os.getenv(key) or "").strip()
        if val:
            return val
    return None


def _container_is_running(container: str) -> bool:
    try:
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and proc.stdout.strip().lower() == "true"


async def main() -> int:
    api_key = os.getenv("LLM_KEY") or os.getenv("MISTRAL_API_KEY")
    if not api_key:
        print("Set LLM_KEY or MISTRAL_API_KEY", file=sys.stderr)
        return 1

    container = _resolve_container(sys.argv)
    if not container:
        print(
            "Set PI_SDK_DOCKER_CONTAINER / DOCKER_CONTAINER or pass container name:\n"
            "  python examples/docker_bash.py <container>",
            file=sys.stderr,
        )
        return 1

    if not _container_is_running(container):
        print(
            f"Container {container!r} is not running. Start it first, e.g.:\n"
            f"  docker start {container}",
            file=sys.stderr,
        )
        return 1

    user_prompt = (
        " ".join(sys.argv[2:]).strip()
        or "Use docker_bash to run `pwd && ls -la` inside the container. "
        "Reply with the command output in 5 lines or fewer."
    )

    def on_event(event):
        if event.type == EventType.TOOL_CALL:
            print(f">> {event.data.get('name')}: {event.data.get('arguments')[:160]}...")
        elif event.type == EventType.TEXT_DELTA:
            print(event.text, end="", flush=True)

    agent = Agent.create(
        api_key=api_key,
        provider=PROVIDER,
        model=MODEL,
        cwd=os.getcwd(),
        autonomous=True,
        disable_tools=["bash"],
        on_event=on_event,
    )

    print(f"model={MODEL} container={container}\n")
    result = await agent.run(
        f"{user_prompt}\n\n"
        f"The default container is already configured as {container!r}; "
        "you do not need to pass container= unless switching containers."
    )
    print("\n\n---")
    if result.status != "ok":
        print(result.error, file=sys.stderr)
        return 1
    print(f"session_id={result.session_id} tokens={result.usage.total_tokens}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
