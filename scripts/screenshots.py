"""Capture the C9 UI walkthrough of one tool (Phase 6): run history, a run's graph, logs, outputs.

    make parity-kestra && make screenshots-kestra      # or the prefect pair

Runs inside the pinned Playwright image (scripts/screenshots.Dockerfile) on the stack's network.
A TCP forwarder inside the container makes localhost:<port> reach the tool, so the UI's own
configured URLs (http://localhost:8080, http://localhost:4200/api) work unchanged. Every request
that doesn't go to the stack is blocked (the Kestra UI loads analytics and the Kestra API site).

The runs shown are picked through the tool's API from the parity suite's history:
  F-1   the primary model failed and the fallback answered (a handled failure)
  Q-15  the alert was rejected, so the tick failed (an unhandled failure)
  happy a tick that summarized and sent an email
Writes docs/comparison/data/screenshots/<tool>-NN-<page>.png and <tool>-metrics.txt.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import socket
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

OUT = Path("/out")
VIEWPORT = {"width": 1600, "height": 1000}
JSON = dict[str, Any]


def forward(port: int, host: str) -> None:
    """Serve 127.0.0.1:<port> by piping each connection to <host>:<port>."""

    class Pipe(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            upstream = socket.create_connection((host, port))

            def copy(source: socket.socket, target: socket.socket) -> None:
                try:
                    while data := source.recv(65536):
                        target.sendall(data)
                except OSError:
                    pass
                finally:
                    with contextlib.suppress(OSError):
                        target.shutdown(socket.SHUT_WR)

            back = threading.Thread(target=copy, args=(upstream, self.request), daemon=True)
            back.start()
            copy(self.request, upstream)
            back.join()

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    server = socketserver.ThreadingTCPServer(("127.0.0.1", port), Pipe)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()


def http(url: str, user: str, password: str, body: JSON | None = None) -> Any:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def metrics(url: str, keep: Callable[[str], bool], limit: int = 60, auth: tuple[str, str] | None = None) -> str:
    """The first lines of a metrics endpoint that pass ``keep``, or what went wrong."""
    request = urllib.request.Request(url)
    if auth:
        request.add_header("Authorization", "Basic " + base64.b64encode(":".join(auth).encode()).decode())
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            lines = response.read().decode().splitlines()
    except urllib.error.HTTPError as error:
        return f"GET {url} -> HTTP {error.code}\n"
    except OSError as error:
        return f"GET {url} -> {error}\n"
    kept = [line for line in lines if keep(line)]
    return f"GET {url} -> {len(lines)} lines; {len(kept)} kept, first {limit} below\n" + "\n".join(kept[:limit]) + "\n"


# --- Kestra ------------------------------------------------------------------------------------

REVIEW_CSV = b"id,amount\n1,10.50\n2,0\n3,99.99\n"  # the probe's good batch (scripts/probes.py)


def kestra_login(page: Page, user: str, password: str) -> None:
    page.goto("http://localhost:8080/ui/login", wait_until="networkidle")
    page.locator("#input-username").press_sequentially(user)  # fill() doesn't trigger the form's input events
    page.locator("#input-password").press_sequentially(password)
    page.locator("button[type=submit]").click()
    page.wait_for_url(lambda url: "/login" not in url, timeout=15000)


def wait_until(check: Callable[[], bool], timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise TimeoutError("the run didn't finish after the review")
        time.sleep(2)


def kestra_review(page: Page, shoot: Callable[[str], None]) -> str:
    """Do a probe-gate run's human step in the UI: the resume form, a name, a decision, the CSV."""
    user, password = os.environ["KESTRA_ADMIN_USER"], os.environ["KESTRA_ADMIN_PASSWORD"]
    run_id = sys.argv[2]
    forward(8080, "kestra")
    kestra_login(page, user, password)
    run_page = f"http://localhost:8080/ui/main/executions/experiment.probes/probe-gate/{run_id}"
    page.goto(f"{run_page}/overview", wait_until="load")  # a live execution keeps a stream open: no networkidle
    page.wait_for_timeout(4000)
    page.get_by_role("button", name="Resume").first.click()
    page.wait_for_timeout(1500)
    dialog = page.locator("[role=dialog]")
    dialog.locator(".monaco-editor").first.click()  # STRING inputs are a code editor (they accept expressions)
    page.keyboard.type("ui reviewer")
    dialog.locator("input.kel-select__input").first.click(force=True)
    page.wait_for_timeout(600)
    page.get_by_role("option", name="APPROVE").click()
    page.set_input_files(
        "input[type=file]", files=[{"name": "batch.csv", "mimeType": "text/csv", "buffer": REVIEW_CSV}]
    )
    page.wait_for_timeout(500)
    shoot("form")
    dialog.get_by_role("button", name="Resume").click()
    api = f"http://localhost:8080/api/v1/main/executions/{run_id}"
    wait_until(lambda: http(api, user, password)["state"]["current"] == "SUCCESS")
    page.goto(f"{run_page}/outputs", wait_until="load")
    page.wait_for_timeout(4000)
    page.get_by_text("validate-file", exact=True).first.click()
    page.wait_for_timeout(1500)
    shoot("outputs")
    return ""


def kestra(page: Page, shoot: Callable[[str], None]) -> str:
    user, password = os.environ["KESTRA_ADMIN_USER"], os.environ["KESTRA_ADMIN_PASSWORD"]
    forward(8080, "kestra")
    base = "http://localhost:8080"
    api = f"{base}/api/v1/main"
    flow = "experiment.email/email-summarize-send"
    found = http(
        f"{api}/executions/search?filters%5Bnamespace%5D%5BEQUALS%5D=experiment.email"
        "&filters%5BflowId%5D%5BEQUALS%5D=email-summarize-send&size=200&sort=state.startDate:desc",
        user,
        password,
    )["results"]
    picked: dict[str, str] = {}
    for summary in found:
        execution = http(f"{api}/executions/{summary['id']}", user, password)
        tasks = {t["taskId"]: t["state"]["current"] for t in execution.get("taskRunList") or []}
        state = execution["state"]["current"]
        if tasks.get("gemini-flash") == "WARNING" and tasks.get("gemini-flash-fallback") == "SUCCESS":
            picked.setdefault("f1", execution["id"])
        if tasks.get("telegram-send-failed-exec") == "FAILED":
            picked.setdefault("q15", execution["id"])
        if state == "SUCCESS" and tasks.get("pg-update-summary-sent") == "SUCCESS":
            picked.setdefault("happy", execution["id"])
    print("kestra executions:", picked)

    kestra_login(page, user, password)

    def visit(path: str) -> None:
        page.goto(base + path, wait_until="networkidle")
        page.wait_for_timeout(1500)

    visit("/ui/main/executions")
    shoot("executions")
    f1 = f"/ui/main/executions/{flow}/{picked['f1']}"
    visit(f"{f1}/overview")
    # The topology fits the whole flow into the pane, too small to read: show the node details and
    # zoom in (mouse wheel, around the cursor) on the model calls and their If branch.
    page.get_by_text("Show more details").click()
    pane = page.locator(".vue-flow__pane").first.bounding_box()
    if pane is None:
        raise RuntimeError("the execution overview shows no topology pane")
    page.mouse.move(pane["x"] + pane["width"] * 0.40, pane["y"] + pane["height"] / 2)
    for _ in range(4):
        page.mouse.wheel(0, -200)
        page.wait_for_timeout(250)
    page.mouse.move(5, 5)  # no tooltip in the shot
    page.wait_for_timeout(800)
    shoot("f1-overview")
    visit(f"{f1}/gantt")
    shoot("f1-gantt")
    visit(f"{f1}/logs")
    shoot("f1-logs")
    visit(f"{f1}/outputs")
    page.get_by_text("gemini-flash-fallback", exact=True).first.click()
    page.wait_for_timeout(1000)
    shoot("f1-outputs")
    visit(f"/ui/main/executions/{flow}/{picked['q15']}/logs")
    page.get_by_text("telegram-send-failed-exec", exact=True).first.scroll_into_view_if_needed()
    page.mouse.wheel(0, 300)  # the alert task and its error, not just its header
    page.wait_for_timeout(800)
    shoot("q15-logs")
    visit(f"/ui/main/flows/edit/{flow}/edit")
    shoot("flow-editor")
    visit(f"/ui/main/flows/edit/{flow}/triggers")
    shoot("flow-triggers")
    families = ("kestra_executor_execution_duration", "kestra_executor_taskrun_ended_count")
    return metrics("http://kestra:8081/prometheus", lambda line: line.startswith(families))


# --- Prefect -----------------------------------------------------------------------------------


def prefect_login(page: Page, user: str, password: str) -> None:
    page.goto("http://localhost:4200", wait_until="networkidle")  # the default UI is the new one, under /v2
    page.locator("input").first.press_sequentially(f"{user}:{password}")  # the basic-auth string
    page.keyboard.press("Enter")
    page.wait_for_url(lambda url: "/login" not in url, timeout=15000)


def prefect_review(page: Page, shoot: Callable[[str], None]) -> str:
    """Do a probe-gate run's human step in the UI: the resume form, a name, a decision, the CSV as text."""
    user, password = os.environ["PREFECT_ADMIN_USER"], os.environ["PREFECT_ADMIN_PASSWORD"]
    run_id = sys.argv[2]
    forward(4200, "prefect-server")
    prefect_login(page, user, password)
    page.goto(f"http://localhost:4200/v2/runs/flow-run/{run_id}", wait_until="load")
    page.wait_for_timeout(4000)
    page.get_by_role("button", name="Open menu").click()  # Resume is in the run page's menu
    page.wait_for_timeout(800)
    page.get_by_role("menuitem", name="Resume").click()
    page.wait_for_timeout(2500)
    dialog = page.locator("[role=dialog]")
    fields = dialog.locator("textarea")  # the form draws every str field as a text box: no file upload
    fields.nth(0).fill("ui reviewer")
    dialog.get_by_text("Select", exact=True).click()
    page.get_by_role("option", name="APPROVE").click()
    fields.nth(1).fill(REVIEW_CSV.decode())
    page.wait_for_timeout(500)
    shoot("form")
    dialog.get_by_role("button", name="Resume").click()
    dialog.wait_for(state="hidden", timeout=15000)  # the form closes once the server accepted the input
    api = f"http://localhost:4200/api/flow_runs/{run_id}"
    wait_until(lambda: http(api, user, password)["state"]["type"] == "COMPLETED")
    page.goto(f"http://localhost:4200/v2/runs/flow-run/{run_id}", wait_until="load")
    page.wait_for_timeout(4000)
    page.get_by_role("tab", name="Artifacts").or_(page.get_by_role("button", name="Artifacts")).first.click()
    page.wait_for_timeout(2000)
    shoot("artifacts")
    page.get_by_text("probe-validation-report", exact=True).first.click()
    page.wait_for_timeout(3000)
    shoot("artifact-table")
    return ""


def prefect(page: Page, shoot: Callable[[str], None]) -> str:
    user, password = os.environ["PREFECT_ADMIN_USER"], os.environ["PREFECT_ADMIN_PASSWORD"]
    forward(4200, "prefect-server")
    base = "http://localhost:4200"
    api = f"{base}/api"

    def task_runs(name: str, state: str) -> list[JSON]:
        body = {
            "task_runs": {"name": {"like_": name}, "state": {"type": {"any_": [state]}}},
            "sort": "EXPECTED_START_TIME_DESC",
            "limit": 200,
        }
        runs = http(f"{api}/task_runs/filter", user, password, body)
        # Task runs are named "<task name>-<3 hex>"; both model calls are runs of one task.
        return [r for r in runs if re.fullmatch(rf"{re.escape(name)}-[0-9a-f]{{3}}", r["name"])]

    answered = {r["flow_run_id"] for r in task_runs("gemini-flash-fallback", "COMPLETED")}
    f1 = next(r for r in task_runs("gemini-flash", "FAILED") if r["flow_run_id"] in answered)
    q15 = task_runs("telegram-send-failed-exec", "FAILED")[0]
    happy = task_runs("pg-update-summary-sent", "COMPLETED")[0]
    picked = {
        "f1": f1["flow_run_id"],
        "f1_task": f1["id"],
        "q15": q15["flow_run_id"],
        "q15_task": q15["id"],
        "happy": happy["flow_run_id"],
    }
    print("prefect runs:", picked)

    prefect_login(page, user, password)

    def visit(path: str) -> None:
        page.goto(base + path, wait_until="networkidle")
        page.wait_for_timeout(2000)

    visit("/v2/runs")
    shoot("runs")
    visit(f"/v2/runs/flow-run/{picked['f1']}")
    shoot("f1-run")
    visit(f"/v2/runs/task-run/{picked['f1_task']}")
    shoot("f1-failed-task")
    page.get_by_text("Task Inputs", exact=True).first.click()
    page.wait_for_timeout(1500)
    shoot("f1-task-inputs")
    visit(f"/v2/runs/flow-run/{picked['q15']}")
    shoot("q15-run")
    visit(f"/v2/runs/task-run/{picked['q15_task']}")
    shoot("q15-failed-task")
    visit("/v2/deployments")
    shoot("deployments")
    visit("/v2/blocks")
    shoot("blocks")
    visit("/v2/variables")
    shoot("variables")
    return metrics(f"{api}/metrics", lambda line: not line.startswith("#"), auth=(user, password))


def main() -> int:
    tool = sys.argv[1]  # kestra | prefect | kestra-review RUN_ID | prefect-review RUN_ID
    capture = {"kestra": kestra, "prefect": prefect, "kestra-review": kestra_review, "prefect-review": prefect_review}[
        tool
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    blocked: set[str] = set()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport=VIEWPORT, color_scheme="light")

        def route(request_route: Any) -> None:
            if request_route.request.url.startswith("http://localhost:"):
                request_route.continue_()
            else:
                blocked.add(request_route.request.url.split("/")[2])
                request_route.abort()

        context.route("**/*", route)
        page = context.new_page()
        count = 0

        def shoot(name: str) -> None:
            nonlocal count
            count += 1
            path = OUT / f"{tool}-{count:02d}-{name}.png"
            page.screenshot(path=path)
            print("wrote", path.name)

        text = capture(page, shoot)
        browser.close()
    if text:
        (OUT / f"{tool}-metrics.txt").write_text(text)
    print("blocked hosts:", sorted(blocked) or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
