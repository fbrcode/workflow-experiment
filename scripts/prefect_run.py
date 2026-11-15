"""Run a Prefect deployment now, wait for it, and print each task's state and the warnings/errors.

Usage (from the Makefile, which points the client at the stack's API):
    make prefect-run [FLOW=email-ingest]

Each flow has one deployment with the same name, so FLOW is also the deployment name.
Exits 1 if the flow run doesn't end Completed.
"""

from __future__ import annotations

import sys
import time

from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import FlowRunFilter, FlowRunFilterId, LogFilter, LogFilterFlowRunId, LogFilterLevel
from prefect.client.schemas.sorting import LogSort, TaskRunSort
from prefect.deployments import run_deployment

WARNING = 30


def main(flow: str) -> int:
    started = time.monotonic()
    flow_run = run_deployment(f"{flow}/{flow}", timeout=900)
    elapsed = time.monotonic() - started
    with get_client(sync_client=True) as client:
        task_runs = client.read_task_runs(
            flow_run_filter=FlowRunFilter(id=FlowRunFilterId(any_=[flow_run.id])),
            sort=TaskRunSort.EXPECTED_START_TIME_ASC,
        )
        logs = client.read_logs(
            log_filter=LogFilter(flow_run_id=LogFilterFlowRunId(any_=[flow_run.id]), level=LogFilterLevel(ge_=WARNING)),
            sort=LogSort.TIMESTAMP_ASC,
        )

    state = flow_run.state.name if flow_run.state else "?"
    print(f"{flow} run {flow_run.name} ({flow_run.id}): {state} in {elapsed:.1f}s")
    for task_run in task_runs:
        task_state = task_run.state.name if task_run.state else "?"
        print(f"  {task_run.name:<40} {task_state:<10} {task_run.total_run_time.total_seconds():6.2f}s")
    for log in logs:
        first_line = log.message.splitlines()[0] if log.message else ""
        print(f"  [{log.level}] {log.name}: {first_line[:300]}")
    return 0 if state == "Completed" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "email-summarize-send"))
