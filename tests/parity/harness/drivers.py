"""One driver per tool: the only tool-specific code in the parity suite.

A driver runs a flow through the tool's REST API and waits for it, reads and pauses the
schedules, and waits until no run is in flight. Everything the scenarios assert on (app-db,
the mocks' recorded calls, the mailbox) is shared and tool-agnostic.

Both drivers use plain HTTP, not a tool SDK, so the suite has no dependency on either tool.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import httpx

from .settings import env

FLOWS = ("email-ingest", "email-summarize-send")
INGEST, TICK = FLOWS
RUN_TIMEOUT = 900.0  # seconds; generous for a run that waits on a network timeout


@dataclass(frozen=True)
class Run:
    """A finished run. ``outcome`` is the tool-agnostic reading of the tool's own ``state``."""

    flow: str
    run_id: str
    state: str
    outcome: str  # "completed", "failed" or "cancelled"
    seconds: float  # from the trigger call to the terminal state, as seen by the suite


class Driver(ABC):
    name: str
    compose_project: str

    @abstractmethod
    def version(self) -> str: ...

    @abstractmethod
    def schedules(self) -> dict[str, list[str]]:
        """Cron expression(s) declared for each flow."""

    @abstractmethod
    def set_schedules_active(self, active: bool) -> None: ...

    @abstractmethod
    def active_runs(self) -> dict[str, str]:
        """Runs of either flow that haven't finished (or are due to start): id -> description."""

    @abstractmethod
    def cancel(self, run_id: str) -> None: ...

    @abstractmethod
    def run(self, flow: str) -> Run:
        """Start ``flow`` now and block until it reaches a terminal state."""

    def wait_idle(self, timeout: float = 300.0) -> None:
        deadline = time.monotonic() + timeout
        while active := self.active_runs():
            if time.monotonic() > deadline:
                raise TimeoutError(f"{self.name}: runs still active after {timeout:.0f}s: {list(active.values())}")
            time.sleep(1)

    def settle(self, timeout: float = 120.0) -> list[str]:
        """Let in-flight runs finish, then cancel what's left over and return it.

        Used once, at session start, with the schedules paused. A leftover is a run from before
        the session that can't finish, e.g. one whose worker process was replaced mid-run.
        """
        try:
            self.wait_idle(timeout)
            return []
        except TimeoutError:
            leftovers = self.active_runs()
        for run_id in leftovers:
            self.cancel(run_id)
        self.wait_idle(60.0)
        return list(leftovers.values())


class KestraDriver(Driver):
    """Kestra OSS REST API (`/api/v1/main/...`, basic auth)."""

    name = "kestra"
    compose_project = "wfx-kestra"
    namespace = "experiment.email"
    outcomes: ClassVar[dict[str, str]] = {
        "SUCCESS": "completed",
        "WARNING": "completed",
        "FAILED": "failed",
        "KILLED": "failed",
    }
    terminal: ClassVar[set[str]] = {"SUCCESS", "WARNING", "FAILED", "KILLED", "CANCELLED", "SKIPPED", "RETRIED"}

    def __init__(self) -> None:
        self.http = httpx.Client(
            base_url=f"http://localhost:{env('KESTRA_HOST_PORT')}/api/v1",
            auth=(env("KESTRA_ADMIN_USER"), env("KESTRA_ADMIN_PASSWORD")),
            timeout=httpx.Timeout(30.0, read=RUN_TIMEOUT),
        )

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.http.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def version(self) -> str:
        return self._call("GET", "/configs")["version"]

    def schedules(self) -> dict[str, list[str]]:
        out = {}
        for flow in FLOWS:
            body = self._call("GET", f"/main/flows/{self.namespace}/{flow}")
            triggers = body.get("triggers") or []
            out[flow] = sorted(t["cron"] for t in triggers if t["type"].endswith(".Schedule"))
        return out

    def set_schedules_active(self, active: bool) -> None:
        # Re-enabling without `recoverMissedSchedules` skips the ticks missed while disabled.
        for flow in FLOWS:
            for trigger in self._call("GET", f"/main/triggers/{self.namespace}/{flow}")["results"]:
                body = {"namespace": self.namespace, "flowId": flow, "triggerId": trigger["triggerId"]}
                self._call("PUT", "/main/triggers/set-disabled", json={**body, "disabled": not active})

    def active_runs(self) -> dict[str, str]:
        params = {"size": 100, "sort": "state.startDate:desc", "filters[namespace][EQUALS]": self.namespace}
        results = self._call("GET", "/main/executions/search", params=params)["results"]
        return {
            e["id"]: f"{e['flowId']} {e['id']} {e['state']['current']} since {e['state']['startDate']}"
            for e in results
            if e["state"]["current"] not in self.terminal
        }

    def cancel(self, run_id: str) -> None:
        self.http.delete(f"/main/executions/{run_id}/actions/kill").raise_for_status()

    def run(self, flow: str) -> Run:
        started = time.monotonic()
        execution = self._call("POST", f"/main/executions/{self.namespace}/{flow}", params={"wait": "true"})
        while execution["state"]["current"] not in self.terminal:  # `wait` normally returns at the end
            time.sleep(0.5)
            execution = self._call("GET", f"/main/executions/{execution['id']}")
        state = execution["state"]["current"]
        return Run(flow, execution["id"], state, self.outcomes.get(state, "cancelled"), time.monotonic() - started)


class PrefectDriver(Driver):
    """Prefect OSS server REST API (`/api/...`, basic auth from PREFECT_SERVER_API_AUTH_STRING)."""

    name = "prefect"
    compose_project = "wfx-prefect"
    outcomes: ClassVar[dict[str, str]] = {
        "COMPLETED": "completed",
        "FAILED": "failed",
        "CRASHED": "failed",
        "CANCELLED": "cancelled",
    }
    unfinished: ClassVar[list[str]] = ["SCHEDULED", "PENDING", "RUNNING", "PAUSED", "CANCELLING"]

    def __init__(self) -> None:
        self.http = httpx.Client(
            base_url=f"http://localhost:{env('PREFECT_HOST_PORT')}/api",
            auth=(env("PREFECT_ADMIN_USER"), env("PREFECT_ADMIN_PASSWORD")),
            timeout=30.0,
        )
        self._deployments: dict[str, dict[str, Any]] = {}

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.http.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    def _deployment(self, flow: str, refresh: bool = False) -> dict[str, Any]:
        if refresh or flow not in self._deployments:  # each flow has one deployment of the same name
            self._deployments[flow] = self._call("GET", f"/deployments/name/{flow}/{flow}")
        return self._deployments[flow]

    def version(self) -> str:
        return self._call("GET", "/admin/version")

    def schedules(self) -> dict[str, list[str]]:
        return {
            flow: sorted(s["schedule"]["cron"] for s in self._deployment(flow, refresh=True)["schedules"])
            for flow in FLOWS
        }

    def set_schedules_active(self, active: bool) -> None:
        for flow in FLOWS:
            deployment = self._deployment(flow, refresh=True)
            for schedule in deployment["schedules"]:
                path = f"/deployments/{deployment['id']}/schedules/{schedule['id']}"
                self._call("PATCH", path, json={"active": active})

    def _unfinished_runs(self) -> list[dict[str, Any]]:
        body = {
            "deployments": {"id": {"any_": [self._deployment(flow)["id"] for flow in FLOWS]}},
            "flow_runs": {"state": {"type": {"any_": self.unfinished}}},
        }
        return self._call("POST", "/flow_runs/filter", json=body)

    def active_runs(self) -> dict[str, str]:
        horizon = datetime.now(UTC) + timedelta(minutes=2)
        active = {}
        for flow_run in self._unfinished_runs():
            state = flow_run["state"]
            due = datetime.fromisoformat(flow_run["expected_start_time"])
            if state["type"] != "SCHEDULED" or due < horizon:
                active[flow_run["id"]] = f"{flow_run['name']} {flow_run['id']} {state['name']} since {due.isoformat()}"
        return active

    def cancel(self, run_id: str) -> None:
        state = {"type": "CANCELLED", "name": "Cancelled", "message": "Left over from before a parity session"}
        self._call("POST", f"/flow_runs/{run_id}/set_state", json={"state": state, "force": True})

    def run(self, flow: str) -> Run:
        started = time.monotonic()
        flow_run = self._call("POST", f"/deployments/{self._deployment(flow)['id']}/create_flow_run", json={})
        deadline = started + RUN_TIMEOUT
        while (state := flow_run["state"]["type"]) not in self.outcomes:
            if time.monotonic() > deadline:
                raise TimeoutError(f"prefect: {flow} run {flow_run['id']} still {state} after {RUN_TIMEOUT:.0f}s")
            time.sleep(0.5)
            flow_run = self._call("GET", f"/flow_runs/{flow_run['id']}")
        name = flow_run["state"]["name"]
        return Run(flow, flow_run["id"], name, self.outcomes[state], time.monotonic() - started)


DRIVERS: dict[str, type[Driver]] = {"kestra": KestraDriver, "prefect": PrefectDriver}
