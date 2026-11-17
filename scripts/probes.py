"""Capability probes for the Retool replacement questions (docs/probes.md).

    make probe-kestra       # or probe-prefect; starts the stack if needed, about 32 minutes

Runs against the stack that is up, through the tool's REST API, and writes
docs/comparison/data/probes-<tool>-<date>.json. Three probes, run at the same time:

  long-query  one `select pg_sleep(1800)` through the tool's own Postgres task: does a 30-minute
              statement, three times Retool Workflows' 600 s limit, finish? (--seconds)
  webhook     an upstream system announces a batch with an HTTP call: Kestra's webhook trigger
              (HMAC-signed, answered synchronously); Prefect OSS has no webhooks, so the caller
              posts an event that a deployment trigger turns into a run
  gate        a control-table gate with a human step, twice (a good CSV, then a bad one):
              the run waits until its row in probe.batch is READY (this script flips it after
              a while, as an upstream process would), pauses for a reviewer, gets a name, a
              decision and the CSV through the tool's resume API (what the UI form sends),
              validates the CSV and writes the outcome back to the control table.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
ENV_FILE = os.environ.get("ENV_FILE", ".env.example")
JSON = dict[str, Any]
LONG_SECONDS = 1800  # the owner's requirement: Postgres statements of up to 30 minutes (D-024)
READY_AFTER = 20  # seconds the gate waits on the control table before the row turns READY
GOOD_CSV = "id,amount\n1,10.50\n2,0\n3,99.99\n"
BAD_CSV = "id,amount\n1,10.50\nx,3\n3,-1\n"
T0 = time.monotonic()
PROBES = ("long-query", "gate", "webhook")
WEBHOOK_BATCH = 7

TABLE = """
create schema if not exists probe;
create table if not exists probe.batch (
  id bigint primary key,
  status text not null,          -- PENDING, then READY, then APPROVED, REJECTED or INVALID
  approver text,
  decision text,
  rows_total int,
  rows_valid int,
  decided_at timestamptz
);
"""
CONTROL_TABLE = "drop table if exists probe.batch;" + TABLE  # a fresh table for each probe session


def log(message: str) -> None:
    print(f"[{time.monotonic() - T0:6.0f}s] {message}", file=sys.stderr, flush=True)


def env(name: str) -> str:
    return os.environ[name]


def compose(tool: str) -> list[str]:
    return ["docker", "compose", "--env-file", ENV_FILE, "-f", f"{tool}/compose.yaml", "-p", f"wfx-{tool}"]


def app_db(tool: str, statement: str, **variables: int | str) -> str:
    """Run SQL in app-db with psql. Values travel as psql variables (`:'name'` in the SQL), not as text."""
    flags = " ".join(f"-v {name}={value}" for name, value in variables.items())
    for value in variables.values():
        if not str(value).replace("_", "").isalnum():
            raise ValueError(f"unexpected psql variable value {value!r}")
    query = f'psql -v ON_ERROR_STOP=1 {flags} -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA'
    result = subprocess.run(
        [*compose(tool), "exec", "-T", "app-db", "sh", "-c", query],
        input=statement,
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def http(method: str, url: str, auth: tuple[str, str], body: bytes | None = None, ctype: str = "") -> Any:
    token = base64.b64encode(":".join(auth).encode()).decode()
    headers = {"Authorization": f"Basic {token}"} | ({"Content-Type": ctype} if ctype else {})
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


def call(url: str, body: bytes, headers: dict[str, str]) -> JSON:
    """POST and record the status, the time and the start of the body, errors included."""
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **headers})
    began = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status, text = response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        status, text = error.code, error.read().decode()
    return {"status": status, "seconds": round(time.monotonic() - began, 2), "body": text[:300]}


def wait_rows(tool: str, batch_id: int, timeout: float = 120) -> float:
    """Seconds until the batch's row exists."""
    began = time.monotonic()
    while app_db(tool, "select count(*) from probe.batch where id = :'id';", id=batch_id) != "1":
        if time.monotonic() - began > timeout:
            raise TimeoutError(f"batch {batch_id} not registered after {timeout:.0f}s")
        time.sleep(0.5)
    return round(time.monotonic() - began, 2)


def multipart(fields: dict[str, str], files: dict[str, tuple[str, str]]) -> tuple[bytes, str]:
    """A multipart/form-data body: plain fields, and files as name -> (filename, content)."""
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        for name, value in fields.items()
    ]
    parts += [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        f"Content-Type: text/csv\r\n\r\n{content}\r\n"
        for name, (filename, content) in files.items()
    ]
    return ("".join(parts) + f"--{boundary}--\r\n").encode(), f"multipart/form-data; boundary={boundary}"


# --- Tool adapters: start a run, read its state, resume it with the reviewer's input -------------


class Kestra:
    name = "kestra"
    namespace = "experiment.probes"
    terminal = frozenset({"SUCCESS", "WARNING", "FAILED", "KILLED", "CANCELLED"})

    def __init__(self) -> None:
        self.api = f"http://localhost:{env('KESTRA_HOST_PORT')}/api/v1/main"
        self.auth = (env("KESTRA_ADMIN_USER"), env("KESTRA_ADMIN_PASSWORD"))

    def version(self) -> str:
        return http("GET", self.api.replace("/main", "") + "/configs", self.auth)["version"]

    def deploy(self) -> list[str]:
        """The probe flows, in their own namespace (make kestra-deploy never touches it)."""
        bundle = "\n---\n".join(p.read_text() for p in sorted((REPO / "kestra" / "probes").glob("*.yml")))
        url = f"{self.api}/flows/{self.namespace}?delete=true"
        flows = http("POST", url, self.auth, bundle.encode(), "application/x-yaml")
        return [f"{f['id']} (rev {f['revision']})" for f in flows]

    def start(self, flow: str, inputs: dict[str, int]) -> str:
        body, ctype = multipart({k: str(v) for k, v in inputs.items()}, {})
        return http("POST", f"{self.api}/executions/{self.namespace}/{flow}", self.auth, body, ctype)["id"]

    def state(self, run_id: str) -> str:
        return http("GET", f"{self.api}/executions/{run_id}", self.auth)["state"]["current"]

    def awaiting_review(self, run_id: str) -> bool:
        """The Pause task itself is paused. The execution alone isn't enough: LoopUntil puts the
        whole execution in PAUSED between its checks, so a resume then only triggers an early check."""
        execution = http("GET", f"{self.api}/executions/{run_id}", self.auth)
        tasks = {t["taskId"]: t["state"]["current"] for t in execution.get("taskRunList") or []}
        return tasks.get("human-review") == "PAUSED"

    def resume(self, run_id: str, approver: str, decision: str, csv: str) -> None:
        body, ctype = multipart({"approver": approver, "decision": decision}, {"data_file": ("batch.csv", csv)})
        http("POST", f"{self.api}/executions/{run_id}/actions/resume", self.auth, body, ctype)

    def webhook(self, batch_id: int) -> JSON:
        """The Webhook trigger of probe-webhook: unsigned, wrongly signed, then signed. No login."""
        url = f"{self.api}/executions/webhook/{self.namespace}/probe-webhook/probe-webhook"
        body = json.dumps({"batch_id": batch_id}).encode()
        signature = hmac.new(env("WEBHOOK_SIGNING_SECRET").encode(), body, hashlib.sha256).hexdigest()
        unsigned = call(url, body, {})
        wrong = call(url, body, {"X-Signature": "0" * 64})
        signed = call(url, body, {"X-Signature": signature})
        return {
            "endpoint": "POST /api/v1/main/executions/webhook/experiment.probes/probe-webhook/probe-webhook",
            "authentication": "HMAC-SHA256 of the body in X-Signature; the server's login isn't needed",
            "unsigned": unsigned,
            "wrong_signature": wrong,
            "signed": signed,
            "row_after_seconds": wait_rows(self.name, batch_id),
        }

    def details(self, run_id: str) -> JSON:
        execution = http("GET", f"{self.api}/executions/{run_id}", self.auth)
        return {"tasks": {t["taskId"]: t["state"]["current"] for t in execution.get("taskRunList") or []}}


class Prefect:
    name = "prefect"
    terminal = frozenset({"COMPLETED", "FAILED", "CRASHED", "CANCELLED"})

    def __init__(self) -> None:
        self.api = f"http://localhost:{env('PREFECT_HOST_PORT')}/api"
        self.auth = (env("PREFECT_ADMIN_USER"), env("PREFECT_ADMIN_PASSWORD"))

    def version(self) -> str:
        return http("GET", f"{self.api}/admin/version", self.auth)

    def deploy(self) -> list[str]:
        """The probe deployments from prefect/probes.yaml (make prefect-deploy never applies it)."""
        command = ["run", "--rm", "prefect-deploy", "prefect", "--no-prompt", "deploy", "--all"]
        deploy = [*compose("prefect"), *command, "--prefect-file", "probes.yaml"]
        subprocess.run(deploy, cwd=REPO, check=True, stdout=sys.stderr)  # stdout carries --hold's run id
        return ["probe-long-query", "probe-gate", "probe-webhook"]

    def start(self, flow: str, inputs: dict[str, int]) -> str:
        deployment = http("GET", f"{self.api}/deployments/name/{flow}/{flow}", self.auth)
        body = json.dumps({"parameters": inputs}).encode()
        url = f"{self.api}/deployments/{deployment['id']}/create_flow_run"
        return http("POST", url, self.auth, body, "application/json")["id"]

    def state(self, run_id: str) -> str:
        return http("GET", f"{self.api}/flow_runs/{run_id}", self.auth)["state"]["type"]

    def awaiting_review(self, run_id: str) -> bool:
        """Only pause_flow_run pauses the run: the control-table wait is a task that keeps it Running."""
        return self.state(run_id) == "PAUSED"

    def resume(self, run_id: str, approver: str, decision: str, csv: str) -> None:
        run_input = {"approver": approver, "decision": decision, "data_csv": csv}
        body = json.dumps({"run_input": run_input}).encode()
        http("POST", f"{self.api}/flow_runs/{run_id}/resume", self.auth, body, "application/json")

    def webhook(self, batch_id: int) -> JSON:
        """OSS has no webhooks: post a probe.batch.announced event; probes.yaml's trigger starts a run."""
        event = {
            "id": str(uuid.uuid4()),
            "occurred": datetime.now(UTC).isoformat(),
            "event": "probe.batch.announced",
            "resource": {"prefect.resource.id": f"probe.batch.{batch_id}"},
            "payload": {"batch_id": batch_id},
        }
        body = json.dumps([event]).encode()
        token = base64.b64encode(":".join(self.auth).encode()).decode()
        no_login = call(f"{self.api}/events", body, {})
        began = time.monotonic()
        with_login = call(f"{self.api}/events", body, {"Authorization": f"Basic {token}"})
        row_after = wait_rows(self.name, batch_id)
        runs = http(
            "POST",
            f"{self.api}/flow_runs/filter",
            self.auth,
            json.dumps(
                {"deployments": {"name": {"any_": ["probe-webhook"]}}, "limit": 1, "sort": "START_TIME_DESC"}
            ).encode(),
            "application/json",
        )
        final = wait_for(self, runs[0]["id"], self.terminal, 120) if runs else "no run found"
        return {
            "endpoint": "POST /api/events (a list of events), with a deployment trigger in prefect/probes.yaml",
            "authentication": "the server's basic-auth string",
            "without_login": no_login,
            "with_login": with_login,
            "row_after_seconds": round(row_after + with_login["seconds"], 2) if row_after else None,
            "run_state": final,
            "total_seconds": round(time.monotonic() - began, 2),
        }

    def details(self, run_id: str) -> JSON:
        body = json.dumps({"task_runs": {"flow_run_id": {"any_": [run_id]}}}).encode()
        task_runs = http("POST", f"{self.api}/task_runs/filter", self.auth, body, "application/json")
        body = json.dumps({"artifacts": {"flow_run_id": {"any_": [run_id]}}}).encode()
        artifacts = http("POST", f"{self.api}/artifacts/filter", self.auth, body, "application/json")
        return {
            "tasks": {t["name"]: t["state"]["type"] for t in task_runs},
            "artifacts": [{"key": a["key"], "type": a["type"]} for a in artifacts],
        }


# --- Probes ------------------------------------------------------------------------------------


def wait_for(tool: Kestra | Prefect, run_id: str, states: frozenset[str] | set[str], timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while (state := tool.state(run_id)) not in states:
        if time.monotonic() > deadline:
            raise TimeoutError(f"{run_id} still {state} after {timeout:.0f}s, expected {sorted(states)}")
        time.sleep(2)
    return state


def gate(tool: Kestra | Prefect, batch_id: int, csv: str, decision: str) -> JSON:
    app_db(tool.name, "insert into probe.batch (id, status) values (:'id', 'PENDING');", id=batch_id)
    started = time.monotonic()
    run_id = tool.start("probe-gate", {"batch_id": batch_id})
    time.sleep(READY_AFTER)
    state_while_pending = tool.state(run_id)  # the gate holds: the row isn't READY yet
    app_db(tool.name, "update probe.batch set status = 'READY' where id = :'id';", id=batch_id)
    ready_at = time.monotonic()
    deadline = time.monotonic() + 300
    while not tool.awaiting_review(run_id):
        if tool.state(run_id) in tool.terminal or time.monotonic() > deadline:
            raise RuntimeError(f"{run_id} ended or didn't reach the review step: {tool.state(run_id)}")
        time.sleep(2)
    paused_after_ready = time.monotonic() - ready_at
    tool.resume(run_id, approver="probe reviewer", decision=decision, csv=csv)
    final = wait_for(tool, run_id, tool.terminal, 600)
    columns = ["status", "approver", "decision", "rows_total", "rows_valid"]
    query = "select status, approver, decision, rows_total, rows_valid from probe.batch where id = :'id';"
    row = app_db(tool.name, query, id=batch_id).split("|")
    return {
        "run_id": run_id,
        "decision": decision,
        "csv_lines": csv.strip().count("\n") + 1,
        "state_while_row_pending": state_while_pending,
        "paused_seconds_after_ready": round(paused_after_ready, 1),
        "final_state": final,
        "seconds": round(time.monotonic() - started, 1),
        "control_row": dict(zip(columns, row, strict=False)),
    } | tool.details(run_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tool", choices=["kestra", "prefect"])
    parser.add_argument("--seconds", type=int, default=LONG_SECONDS, help="length of the long statement")
    parser.add_argument(
        "--hold",
        type=int,
        metavar="BATCH",
        help="only start a gate run for BATCH (row READY), wait until it asks for a review, print its id",
    )
    parser.add_argument(
        "--probes",
        default=",".join(PROBES),
        help="comma-separated subset of long-query,gate,webhook; a subset updates the day's results file",
    )
    args = parser.parse_args()
    selected = [name for name in args.probes.split(",") if name]
    if unknown := set(selected) - set(PROBES):
        parser.error(f"unknown probes: {sorted(unknown)}")
    tool: Kestra | Prefect = Kestra() if args.tool == "kestra" else Prefect()
    if args.hold is not None:  # for make screenshots-review-<tool>: a person finishes the run in the UI
        app_db(tool.name, TABLE)
        upsert = (
            "insert into probe.batch (id, status) values (:'id', 'READY')"
            " on conflict (id) do update set status = 'READY';"
        )
        app_db(tool.name, upsert, id=args.hold)
        tool.deploy()
        run_id = tool.start("probe-gate", {"batch_id": args.hold})
        while not tool.awaiting_review(run_id):
            time.sleep(2)
        print(run_id)
        return 0

    now = datetime.now().astimezone()
    out = REPO / "docs" / "comparison" / "data" / f"probes-{tool.name}-{now:%Y-%m-%d}.json"
    command = os.environ.get("PROBE_COMMAND") or " ".join(["python", "scripts/probes.py", *sys.argv[1:]])
    partial = set(selected) != set(PROBES) and out.exists()
    document: JSON = json.loads(out.read_text()) if partial else {}
    if "command" in document:  # a file from before --probes: keep its run in the history
        document["commands"] = [
            {"date": document["date"], "command": document.pop("command"), "probes": list(PROBES)[:2]}
        ]
    document |= {"date": now.isoformat(timespec="seconds"), "tool": {"name": tool.name, "version": tool.version()}}
    document["commands"] = [
        *document.get("commands", []),
        {"date": document["date"], "command": command, "probes": selected},
    ]
    document["docs"] = "docs/probes.md"
    app_db(tool.name, CONTROL_TABLE if not partial else TABLE)
    document["deployed"] = tool.deploy()
    log(f"deployed {document['deployed']}")

    if "long-query" in selected:
        long_id = tool.start("probe-long-query", {"seconds": args.seconds})
        long_started = time.monotonic()
        log(f"long-query started: select pg_sleep({args.seconds})")

    if "webhook" in selected:
        app_db(tool.name, "delete from probe.batch where id = :'id';", id=WEBHOOK_BATCH)
        document["webhook"] = tool.webhook(WEBHOOK_BATCH)
        log(f"webhook: {document['webhook']}")

    if "gate" in selected:
        document["gate"] = []
        for batch_id, csv, decision in ((1, GOOD_CSV, "APPROVE"), (2, BAD_CSV, "APPROVE")):
            document["gate"].append(gate(tool, batch_id, csv, decision))
            result = document["gate"][-1]
            log(f"gate batch {batch_id}: {result['final_state']}, row {result['control_row']}")

    if "long-query" in selected:
        final = wait_for(tool, long_id, tool.terminal, args.seconds + 600)
        document["long_query"] = {
            "run_id": long_id,
            "statement": f"select pg_sleep({args.seconds})",
            "final_state": final,
            "seconds": round(time.monotonic() - long_started, 1),
        } | tool.details(long_id)
        log(f"long-query: {final} after {document['long_query']['seconds']}s")
    out.write_text(json.dumps(document, indent=2) + "\n")
    log(f"wrote {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
