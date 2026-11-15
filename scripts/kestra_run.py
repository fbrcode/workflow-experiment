"""Run a Kestra flow now, wait for it, and print what happened (`make kestra-run FLOW=...`).

Prints the execution state, each task run's state and duration, and the ERROR/WARN logs.
Reads KESTRA_URL, KESTRA_USER and KESTRA_PASSWORD from the environment (the Makefile sets them).
Exits 1 when the execution ends FAILED.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from datetime import datetime
from typing import Any

NAMESPACE = "experiment.email"


def api(method: str, path: str) -> Any:
    token = base64.b64encode(f"{os.environ['KESTRA_USER']}:{os.environ['KESTRA_PASSWORD']}".encode()).decode()
    request = urllib.request.Request(
        f"{os.environ['KESTRA_URL']}/api/v1/main{path}", method=method, headers={"Authorization": f"Basic {token}"}
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.load(response)


def seconds(state: dict[str, Any]) -> str:
    history = state.get("histories") or []
    if len(history) < 2:
        return ""
    start, end = (datetime.fromisoformat(h["date"].replace("Z", "+00:00")) for h in (history[0], history[-1]))
    return f"{(end - start).total_seconds():.1f}s"


def main(flow: str) -> int:
    execution = api("POST", f"/executions/{NAMESPACE}/{flow}?wait=true")
    print(f"{flow} {execution['id']}: {execution['state']['current']} in {seconds(execution['state'])}")
    for run in execution.get("taskRunList") or []:
        print(f"  {run['taskId']:32} {run['state']['current']:9} {seconds(run['state'])}")
    for log in api("GET", f"/logs/{execution['id']}"):
        if log.get("level") in ("ERROR", "WARN"):
            print(f"  {log['level']} {log.get('taskId')}: {log['message'][:500]}")
    return 1 if execution["state"]["current"] == "FAILED" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "email-summarize-send"))
