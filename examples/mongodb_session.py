"""
MongoDB session storage: create → run → resume with user_id.

Requires:
  pip install pi-sdk[mongodb]
  # or: pip install pymongo

  set LLM_KEY=...
  set PI_SDK_MONGODB_URI=mongodb://localhost:27017
  # optional: set MONGODB_URI=...

Run:
  python examples/mongodb_session.py
"""

from __future__ import annotations

import os
import sys

from pi_sdk import Agent


PROVIDER = "mistral"
MODEL = "mistral-small-latest"
USER_ID = "demo_user"


def main() -> int:
    api_key = os.getenv("LLM_KEY") or os.getenv("MISTRAL_API_KEY")
    uri = (
        os.getenv("PI_SDK_MONGODB_URI")
        or os.getenv("MONGODB_URI")
        or ""
    ).strip()
    if not api_key:
        print("Set LLM_KEY or MISTRAL_API_KEY", file=sys.stderr)
        return 1
    if not uri:
        print(
            "Set PI_SDK_MONGODB_URI or MONGODB_URI "
            "(e.g. mongodb://localhost:27017)",
            file=sys.stderr,
        )
        return 1

    try:
        agent = Agent.create(
            api_key=api_key,
            provider=PROVIDER,
            model=MODEL,
            cwd=os.getcwd(),
            autonomous=True,
            storage="mongodb",
            mongodb_uri=uri,
            mongodb_db=os.getenv("PI_SDK_MONGODB_DB", "pi_sdk"),
            user_id=USER_ID,
        )
    except ImportError as e:
        print(e, file=sys.stderr)
        return 1

    r1 = agent.run("Remember that the project codename is ORBIT. One sentence.")
    print(f"turn1 status={r1.status} session_id={r1.session_id}")
    print(r1.text or r1.error)
    if r1.status != "ok" or not r1.session_id:
        return 1

    agent2 = Agent.create(
        api_key=api_key,
        provider=PROVIDER,
        model=MODEL,
        cwd=os.getcwd(),
        autonomous=True,
        storage="mongodb",
        mongodb_uri=uri,
        user_id=USER_ID,
    )
    agent2.resume(r1.session_id)
    r2 = agent2.run("What is the project codename? One word.")
    print(f"\nturn2 status={r2.status} session_id={r2.session_id}")
    print(r2.text or r2.error)

    sessions = agent2.list_sessions()
    print(f"\nsessions for {USER_ID}: {len(sessions)}")
    return 0 if r2.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
