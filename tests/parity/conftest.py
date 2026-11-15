"""Parity suite (Phase 5): the same scenarios, run against whichever stack is up.

    make parity-kestra      # or parity-prefect; starts the stack if needed, then runs this suite

The suite pauses the tool's schedules for the session (and resumes them at the end), resets
app-db, the mailbox and the mocks before every test, and drives each flow through the tool's
API. Results, with each run's tool state and duration, go to ``--results`` as JSON.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from harness.drivers import DRIVERS, Driver
from harness.expected import vector_models
from harness.settings import Settings
from harness.world import World

RESULTS: dict[str, Any] = {"tests": {}}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--tool", choices=sorted(DRIVERS), required=True, help="stack under test")
    parser.addoption("--results", default=None, help="results JSON (default: results/<tool>.json)")


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings.from_env()


@pytest.fixture(scope="session")
def driver(request: pytest.FixtureRequest, settings: Settings) -> Iterator[Driver]:
    problems = Settings.not_in_mock_mode()
    if (settings.model, settings.model_fallback) != vector_models():
        problems.append(f"GEMINI_MODEL/GEMINI_MODEL_FALLBACK must be the golden vectors' {vector_models()}")
    if problems:
        pytest.exit("parity needs the mock configuration of .env.example: " + "; ".join(problems), returncode=2)

    driver = DRIVERS[request.config.getoption("--tool")]()
    RESULTS["tool"] = {"name": driver.name, "version": driver.version()}
    driver.set_schedules_active(False)  # nothing but the suite starts a run from here on
    try:
        RESULTS["leftover_runs_cancelled"] = driver.settle()
        yield driver
    finally:
        driver.set_schedules_active(True)


@pytest.fixture
def world(request: pytest.FixtureRequest, settings: Settings, driver: Driver) -> World:
    record: dict[str, Any] = {"runs": [], "notes": {}}
    RESULTS["tests"][request.node.nodeid] = record
    world = World(settings, driver, record)
    world.reset()
    return world


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
    report = yield
    entry = RESULTS["tests"].setdefault(item.nodeid, {"runs": []})
    if report.when == "call" or report.outcome != "passed":
        entry["outcome"] = report.outcome
        if report.outcome == "failed":
            entry["error"] = str(report.longrepr).splitlines()[-1][:500] if report.longrepr else ""
    return report


def _docker_memory() -> str:
    try:
        command = ["docker", "info", "--format", "{{.MemTotal}}"]
        out = subprocess.run(command, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{int(out.stdout.strip()) / 2**30:.2f} GiB"


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    tool = session.config.getoption("--tool")
    if not RESULTS["tests"]:
        return
    path = Path(session.config.getoption("--results") or Path(__file__).parent / "results" / f"{tool}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    tests = RESULTS["tests"].values()
    document = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "host": {"platform": platform.platform(), "machine": platform.machine(), "docker_memory": _docker_memory()},
        "tool": RESULTS.get("tool", {"name": tool}),
        "leftover_runs_cancelled": RESULTS.get("leftover_runs_cancelled", []),
        "command": (os.environ.get("PARITY_COMMAND") or " ".join(["pytest", *sys.argv[1:]])).strip(),
        "summary": {o: sum(t.get("outcome") == o for t in tests) for o in ("passed", "failed", "skipped")},
        "tests": RESULTS["tests"],
    }
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
