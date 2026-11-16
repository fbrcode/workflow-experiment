"""Capability probes, not part of the n8n port (docs/probes.md).

Two flows for the Retool replacement questions, deployed by `prefect/probes.yaml` (only by the
probe runner, `make probe-prefect`, never by `make prefect-deploy`):

- ``probe-long-query``: one Postgres statement that runs longer than Retool Workflows' 600 s limit,
  through the same connector block the summarizer uses. No timeout is set anywhere.
- ``probe-gate``: a gate on a Postgres control table with a human step: wait until the batch's
  row in ``probe.batch`` is READY, pause for a person (a name, a decision, the batch's CSV),
  validate the CSV and publish the report as an artifact, then write the outcome back.
- ``probe-webhook``: an upstream system announces a batch. Webhooks are Prefect Cloud only, so the
  caller posts an event to the server's events API and a deployment trigger starts this flow.
"""

from __future__ import annotations

import csv
import io
import time
from typing import Literal

from prefect import flow, get_run_logger, task
from prefect.artifacts import create_table_artifact
from prefect.flow_runs import pause_flow_run
from prefect.input import RunInput
from pydantic import Field

from email_summarizer import config

READY_POLL_SECONDS = 5
READY_MAX_SECONDS = 2 * 3600
REVIEW_TIMEOUT_SECONDS = 2 * 3600  # pause_flow_run's own default is one hour


@task(name="long-statement")
def long_statement(seconds: int) -> None:
    with config.app_db() as db:
        db.execute("select pg_sleep(:seconds)", parameters={"seconds": seconds})


@flow(name="probe-long-query")
def probe_long_query(seconds: int = 660) -> None:
    """One statement longer than 600 s."""
    long_statement(seconds)


class Review(RunInput):
    """What the reviewer enters in the UI when the run pauses."""

    approver: str = Field(description="Who reviewed the batch")
    decision: Literal["APPROVE", "REJECT"]
    data_csv: str = Field(description="The batch's data file, pasted as CSV with the header id,amount")


@task(name="wait-until-ready")
def wait_until_ready(batch_id: int) -> None:
    deadline = time.monotonic() + READY_MAX_SECONDS
    while True:
        with config.app_db() as db:
            row = db.fetch_one("select status from probe.batch where id = :id", parameters={"id": batch_id})
        if row and row[0] == "READY":
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"batch {batch_id} not READY after {READY_MAX_SECONDS} s")
        time.sleep(READY_POLL_SECONDS)


@task(name="validate-file")
def validate_file(data_csv: str, decision: str) -> dict[str, object]:
    rows = list(csv.reader(io.StringIO(data_csv.strip())))
    header, body = (rows[0], rows[1:]) if rows else ([], [])

    def ok(row: list[str]) -> bool:
        try:
            return len(row) == 2 and int(row[0]) > 0 and float(row[1]) >= 0
        except ValueError:
            return False

    invalid = [i + 2 for i, row in enumerate(body) if not ok(row)]  # 1-based line numbers
    good = header == ["id", "amount"] and not invalid and bool(body)
    status = ("APPROVED" if decision == "APPROVE" else "REJECTED") if good else "INVALID"
    report = {
        "header_ok": header == ["id", "amount"],
        "rows": len(body),
        "valid": len(body) - len(invalid),
        "invalid_lines": invalid,
        "status": status,
    }
    create_table_artifact(
        key="probe-validation-report",
        table=[{"check": key, "value": str(value)} for key, value in report.items()],
        description="Validation of the CSV the reviewer provided",
    )
    return report


@task(name="record-outcome")
def record_outcome(batch_id: int, review: Review, report: dict[str, object]) -> None:
    with config.app_db() as db:
        db.execute(
            "update probe.batch set status = :status, approver = :approver, decision = :decision,"
            " rows_total = :rows, rows_valid = :valid, decided_at = now() where id = :id",
            parameters={
                "status": report["status"],
                "approver": review.approver,
                "decision": review.decision,
                "rows": report["rows"],
                "valid": report["valid"],
                "id": batch_id,
            },
        )


@task(name="register-batch")
def register_batch(batch_id: int) -> None:
    with config.app_db() as db:
        db.execute(
            "insert into probe.batch (id, status) values (:id, 'PENDING')"
            " on conflict (id) do update set status = 'PENDING'",
            parameters={"id": batch_id},
        )


@flow(name="probe-webhook")
def probe_webhook(batch_id: int) -> None:
    """Started by an event an upstream system posts (OSS has no webhooks): register the batch."""
    register_batch(batch_id)


@flow(name="probe-gate")
def probe_gate(batch_id: int = 1) -> str:
    """Control-table gate, human review with form fields, file validation, outcome written back."""
    wait_until_ready(batch_id)
    get_run_logger().info("batch %s is READY: waiting for a reviewer", batch_id)
    review = pause_flow_run(wait_for_input=Review, timeout=REVIEW_TIMEOUT_SECONDS)
    report = validate_file(review.data_csv, review.decision)
    record_outcome(batch_id, review, report)
    return str(report["status"])
