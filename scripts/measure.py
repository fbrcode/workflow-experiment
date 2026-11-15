"""Measure one stack for the comparison (Phase 6): setup (C2), footprint (C3), developer loop (C5).

    make measure-kestra                          # or measure-prefect; about 15 minutes
    make measure-kestra MEASURE_ARGS="--cold"    # first remove the stack's images, then pull and build from scratch

Run one stack at a time with nothing else running in Docker (the script refuses otherwise). It
takes the stack down and up several times and leaves it running at the end. The results go to
docs/comparison/data/measure-<tool>-<date>.json, rewritten after every phase. The protocol and
its limits are D-021 in docs/decisions.md.

Phases, in order:
  tests     `make test-<tool>` with the stack down: the unit tests, without the orchestrator
  cold      (--cold) remove the stack's images; `compose pull`; `compose build --no-cache --pull` in a
            new, empty BuildKit builder (deleted afterwards), so no layer comes from a cache; then
            one start as below: the time from a clean clone to the first green run
  starts    N times: down; the two commands of `make <tool>-up` (with each service's time to
            healthy); seed; ingest; one tick. Green when the tick sent a Telegram message
  idle      down; up; 5 minutes with the schedules on and no mail; then docker stats for 60 s
  workload  docker stats while the parity suite's happy path runs (9 emails ingested, summarized, sent)
  loop      9 emails queued (untimed); then N times: change a source file, put the change into the
            running stack, run one tick, which summarizes and sends an email with the changed code
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "docs" / "comparison" / "data"
ENV_FILE = os.environ.get("ENV_FILE", ".env.example")  # the Makefile passes the file it uses
HAPPY_PATH = "test_each_tick_summarizes_and_sends_one_email"  # tests/parity/test_happy_path.py
BUILDKIT = "moby/buildkit:v0.34.1"  # the cold phase's throwaway builder
BUILDER = "wfx-measure-cold"
JSON = dict[str, Any]
T0 = time.monotonic()


class Tool(NamedTuple):
    name: str
    own: tuple[str, ...]  # compose services that are the tool itself; the others are the shared infra
    edit: str  # the source file the loop phase changes
    redeploy: str  # the make target that puts a code change into the running stack

    @property
    def project(self) -> str:
        return f"wfx-{self.name}"

    @property
    def compose(self) -> list[str]:
        """The Makefile's $(KESTRA) / $(PREFECT)."""
        return ["docker", "compose", "--env-file", ENV_FILE, "-f", f"{self.name}/compose.yaml", "-p", self.project]


TOOLS = {
    "kestra": Tool("kestra", ("kestra", "kestra-db"), "kestra/files/summarizer/ported.py", "kestra-deploy"),
    "prefect": Tool(
        "prefect",
        ("prefect-server", "prefect-worker", "prefect-db", "prefect-deploy"),
        "prefect/src/email_summarizer/summarize.py",
        "prefect-up",  # the worker runs the code installed in its image: rebuild, recreate, redeploy
    ),
}


def log(message: str) -> None:
    print(f"[{time.monotonic() - T0:7.1f}s] {message}", file=sys.stderr, flush=True)


def run(command: list[str], cwd: Path = REPO, **env: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ | env if env else None
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False, env=environment)
    if result.returncode:
        tail = (result.stdout + result.stderr)[-4000:]
        raise RuntimeError(f"{' '.join(command)} exited {result.returncode}:\n{tail}")
    return result


def timed(command: list[str], cwd: Path = REPO, **env: str) -> float:
    begin = time.monotonic()
    run(command, cwd, **env)
    return round(time.monotonic() - begin, 2)


def make(target: str, *variables: str) -> list[str]:
    return ["make", "--no-print-directory", target, *variables]


def output(*command: str) -> str:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


# --- Facts about the host, the stack and the tool -----------------------------------------------


def host() -> JSON:
    docker = json.loads(output("docker", "info", "--format", "{{json .}}") or "{}")
    memsize = output("sysctl", "-n", "hw.memsize")
    return {
        "os": f"macOS {output('sw_vers', '-productVersion')}" if sys.platform == "darwin" else platform.platform(),
        "machine": platform.machine(),
        "cpu": output("sysctl", "-n", "machdep.cpu.brand_string"),
        "memory_gib": round(int(memsize) / 2**30, 1) if memsize.isdigit() else None,
        "docker": output("docker", "version", "--format", "{{.Server.Platform.Name}}, engine {{.Server.Version}}"),
        "compose": output("docker", "compose", "version", "--short"),
        "docker_vm": {
            "cpus": docker.get("NCPU"),
            "memory_gib": round(docker.get("MemTotal", 0) / 2**30, 2),
            "storage_driver": docker.get("Driver"),
        },
    }


def env(name: str) -> str:
    return os.environ[name]


def api(url: str, user: str, password: str) -> Any:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def tool_version(tool: Tool) -> str:
    if tool.name == "kestra":
        url = f"http://localhost:{env('KESTRA_HOST_PORT')}/api/v1/configs"
        return api(url, env("KESTRA_ADMIN_USER"), env("KESTRA_ADMIN_PASSWORD"))["version"]
    url = f"http://localhost:{env('PREFECT_HOST_PORT')}/api/admin/version"
    return api(url, env("PREFECT_ADMIN_USER"), env("PREFECT_ADMIN_PASSWORD"))


def telegram_sent() -> int:
    """sendMessage calls mock-telegram accepted since it started."""
    url = f"http://localhost:{env('MOCK_TELEGRAM_HOST_PORT')}/_mock/calls"
    with urllib.request.urlopen(url, timeout=10) as response:
        calls = json.load(response)
    return sum(call.get("method") == "sendMessage" and call.get("status") == 200 for call in calls)


def compose_config(tool: Tool, *profiles: str) -> JSON:
    flags = [arg for profile in profiles for arg in ("--profile", profile)]
    return json.loads(run([*tool.compose, *flags, "config", "--format", "json"]).stdout)


def setup_facts(tool: Tool) -> JSON:
    services = compose_config(tool, "*")["services"]
    rows = {
        name: {
            "part": "tool" if name in tool.own else "shared infra",
            "profiles": service.get("profiles", []),
            "image": service.get("image"),
            "built": "build" in service,
        }
        for name, service in sorted(services.items())
    }
    always_on = [name for name, row in rows.items() if not row["profiles"]]
    files = [f"{tool.name}/compose.yaml", "infra/compose.yaml"]
    return {
        "services": rows,
        "always_on": {
            "tool": sum(rows[n]["part"] == "tool" for n in always_on),
            "shared infra": sum(rows[n]["part"] == "shared infra" for n in always_on),
        },
        "compose_lines": {f: len((REPO / f).read_text().splitlines()) for f in files},
    }


def runtime_images(tool: Tool) -> list[str]:
    """Images the running stack uses: its compose services, plus Kestra's script-task image."""
    images = run([*tool.compose, "config", "--images"]).stdout.split()
    if tool.name == "kestra":  # script tasks start their own containers (containerImage in the flows)
        for flow in sorted((REPO / "kestra" / "flows").glob("*.yml")):
            images += re.findall(r"containerImage:\s*(\S+)", flow.read_text())
    return sorted(set(images))


def base_images(tool: Tool) -> list[str]:
    """The FROM images of the stack's own builds."""
    bases: set[str] = set()
    for service in compose_config(tool)["services"].values():
        if build := service.get("build"):
            dockerfile = Path(build["context"]) / build.get("dockerfile", "Dockerfile")
            stages: set[str] = set()
            for match in re.finditer(r"^FROM\s+(\S+)(?:\s+AS\s+(\S+))?", dockerfile.read_text(), re.M | re.I):
                if match[1] not in stages:
                    bases.add(match[1])
                if match[2]:
                    stages.add(match[2])
    return sorted(bases)


def image_sizes(images: list[str]) -> JSON:
    """Size on disk per image, and the total without counting layers an image shares with another listed one."""
    found: dict[str, JSON] = {}
    for image in images:
        result = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            info = json.loads(result.stdout)[0]
            found[image] = {"bytes": info["Size"], "layers": info["RootFS"]["Layers"], "digests": info["RepoDigests"]}
    inside = {
        b
        for b in found
        for a in found
        if a != b and found[a]["layers"][: len(found[b]["layers"])] == found[b]["layers"]
    }
    total = sum(info["bytes"] for info in found.values())
    return {
        "images": {
            image: {
                "mb": round(info["bytes"] / 1e6),
                "digest": next(iter(info["digests"]), None),
                "base_of_another": image in inside,
            }
            if (info := found.get(image))
            else None
            for image in images
        },
        "total_mb": round(total / 1e6),
        "distinct_mb": round((total - sum(found[b]["bytes"] for b in inside)) / 1e6),
    }


# --- docker stats ------------------------------------------------------------------------------

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
MIB = {
    "B": 1 / 2**20,
    "KiB": 1 / 2**10,
    "MiB": 1.0,
    "GiB": 2**10,
    "kB": 1e3 / 2**20,
    "MB": 1e6 / 2**20,
    "GB": 1e9 / 2**20,
}


def mib(text: str) -> float:
    number, unit = re.fullmatch(r"([\d.]+)\s*([A-Za-z]+)", text.strip()).groups()  # type: ignore[union-attr]
    return float(number) * MIB[unit]


class Stats:
    """`docker stats` streamed in the background. A frame is one refresh: container -> (cpu %, MiB)."""

    def __init__(self) -> None:
        self.frames: list[tuple[float, dict[str, tuple[float, float]]]] = []

    def __enter__(self) -> Stats:
        command = ["docker", "stats", "--format", "{{json .}}"]
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        return self

    def _read(self) -> None:
        frame: dict[str, tuple[float, float]] = {}
        for raw in self.process.stdout or []:
            if "\x1b[H" in raw and frame:  # cursor home: a new refresh starts
                self.frames.append((time.monotonic(), frame))
                frame = {}
            line = ANSI.sub("", raw).strip()
            if line.startswith("{"):
                sample = json.loads(line)
                if sample.get("Name") in (None, "--"):
                    continue
                # A container that is starting or stopping reports "--": skip it in this frame.
                with contextlib.suppress(AttributeError, KeyError, TypeError, ValueError):
                    frame[sample["Name"]] = (
                        float(sample["CPUPerc"].rstrip("%")),
                        mib(sample["MemUsage"].split("/")[0]),
                    )
        if frame:
            self.frames.append((time.monotonic(), frame))

    def __exit__(self, *_: object) -> None:
        self.process.terminate()
        self.process.wait()
        self.reader.join(timeout=5)


def part(tool: Tool, container: str) -> str:
    """'tool' or 'shared infra'. Containers outside the compose project are the tool's own task containers."""
    prefix = f"{tool.project}-"
    if not container.startswith(prefix):
        return "tool"
    service = re.sub(r"-(\d+|run-[0-9a-f]+)$", "", container.removeprefix(prefix))
    return "tool" if service in tool.own else "shared infra"


def spread(values: list[float]) -> JSON:
    return {"mean": round(statistics.fmean(values), 1), "max": round(max(values), 1)} if values else {}


def summarize(tool: Tool, frames: list[tuple[float, dict[str, tuple[float, float]]]], series: bool) -> JSON:
    containers: dict[str, list[tuple[float, float]]] = defaultdict(list)
    groups: dict[str, dict[str, list[float]]] = {g: {"cpu": [], "mem": []} for g in ("tool", "shared infra", "all")}
    points = []
    start = frames[0][0] if frames else 0.0
    for stamp, frame in frames:
        sums = {g: [0.0, 0.0] for g in groups}
        for name, (cpu, mem) in frame.items():
            containers[name].append((cpu, mem))
            for group in (part(tool, name), "all"):
                sums[group][0] += cpu
                sums[group][1] += mem
        for group, (cpu, mem) in sums.items():
            groups[group]["cpu"].append(cpu)
            groups[group]["mem"].append(mem)
        points.append([round(stamp - start, 1), round(sums["tool"][1]), round(sums["all"][1]), round(sums["tool"][0])])
    record: JSON = {
        "frames": len(frames),
        "seconds": round(frames[-1][0] - start, 1) if frames else 0,
        "groups": {g: {"cpu_percent": spread(v["cpu"]), "memory_mib": spread(v["mem"])} for g, v in groups.items()},
        "containers": {
            name: {
                "part": part(tool, name),
                "frames": len(samples),
                "cpu_percent": spread([s[0] for s in samples]),
                "memory_mib": spread([s[1] for s in samples]),
            }
            for name, samples in sorted(containers.items())
        },
    }
    if series:
        record["series"] = {"columns": ["s", "tool_mib", "all_mib", "tool_cpu_percent"], "points": points}
    return record


# --- Phases ------------------------------------------------------------------------------------


def compose_ps(tool: Tool) -> list[JSON]:
    result = subprocess.run([*tool.compose, "ps", "-a", "--format", "json"], cwd=REPO, capture_output=True, text=True)
    text = result.stdout.strip()
    if not text:
        return []
    return json.loads(text) if text.startswith("[") else [json.loads(line) for line in text.splitlines()]


def start(tool: Tool, first_run: bool) -> JSON:
    """Down, then `make <tool>-up` as its two commands; optionally seed and run until the first green tick."""
    record: JSON = {"down_s": timed(make(f"{tool.name}-down"))}
    healthy: dict[str, float] = {}
    done = threading.Event()
    begin = time.monotonic()

    def check() -> None:
        for container in compose_ps(tool):
            if container.get("Health") == "healthy":
                healthy.setdefault(container["Service"], round(time.monotonic() - begin, 1))

    def poll() -> None:
        while not done.is_set():
            check()
            done.wait(0.5)

    poller = threading.Thread(target=poll, daemon=True)
    poller.start()
    try:
        record["up_wait_s"] = timed([*tool.compose, "up", "-d", "--build", "--wait"])
    finally:
        done.set()
        poller.join()
    check()  # the last service often turns healthy just as `up --wait` returns
    record["healthy_after_s"] = dict(sorted(healthy.items(), key=lambda item: item[1]))
    record["deploy_s"] = timed(make(f"{tool.name}-deploy"))
    record["make_up_s"] = round(time.monotonic() - begin, 2)
    if first_run:
        record["seed_s"] = timed(make(f"{tool.name}-seed"))
        # The ingest schedule may hold the concurrency slot, which cancels this run (exit 1 on
        # Prefect): the scheduled run then ingests the mail. So the exit codes are recorded, and
        # "green" means the tick ended well and Telegram accepted a message.
        for step, command in (
            ("ingest", make(f"{tool.name}-run", "FLOW=email-ingest")),
            ("tick", make(f"{tool.name}-run")),
        ):
            began = time.monotonic()
            result = subprocess.run(command, cwd=REPO, capture_output=True, text=True, check=False)
            record[f"{step}_s"] = round(time.monotonic() - began, 2)
            record[f"{step}_exit"] = result.returncode
            record[f"{step}_output"] = result.stdout.strip().splitlines()[:1]
        record["first_green_s"] = round(time.monotonic() - begin, 2)
        record["telegram_messages"] = telegram_sent()
        record["green"] = record["tick_exit"] == 0 and record["telegram_messages"] >= 1
    return record


def unit_tests(tool: Tool) -> JSON:
    run(make(f"{tool.name}-down"))  # ING-1's GreenMail test skips itself when the stack is down
    begin = time.monotonic()
    result = run(make(f"test-{tool.name}"))
    summary = [line for line in result.stdout.splitlines() if " passed" in line]
    return {"command": f"make test-{tool.name}", "wall_s": round(time.monotonic() - begin, 2), "pytest": summary[-1:]}


def cold(tool: Tool) -> JSON:
    """Remove the stack's images, then pull and build them as on a machine that has never built them.

    `docker rmi` leaves base-image layers in BuildKit's cache, so the build runs in a new, empty
    builder (docker-container driver, pinned BuildKit), which is deleted afterwards. The builder's
    own start isn't timed.
    """
    run(make(f"{tool.name}-down"))
    images = sorted(set(runtime_images(tool)) | set(base_images(tool)))
    present = [
        i for i in images if subprocess.run(["docker", "image", "inspect", i], capture_output=True).returncode == 0
    ]
    if present:
        run(["docker", "rmi", *present])
    record: JSON = {"removed_images": present, "builder": BUILDKIT}
    record["pull_s"] = timed([*tool.compose, "pull", "--ignore-buildable", "--quiet"])
    run(
        [
            "docker",
            "buildx",
            "create",
            "--name",
            BUILDER,
            "--driver",
            "docker-container",
            "--driver-opt",
            f"image={BUILDKIT}",
        ]
    )
    try:
        run(["docker", "buildx", "inspect", "--bootstrap", BUILDER])
        record["build_s"] = timed([*tool.compose, "build", "--no-cache", "--pull"], BUILDX_BUILDER=BUILDER)
    finally:
        run(["docker", "buildx", "rm", BUILDER])  # and its cache
    # The first start can still pull: Kestra pulls its script-task image at the first task run.
    record["first_start"] = start(tool, first_run=True)
    record["total_s"] = round(record["pull_s"] + record["build_s"] + record["first_start"]["first_green_s"], 2)
    return record


def idle(tool: Tool, wait: int, sample: int) -> JSON:
    record: JSON = {"start": start(tool, first_run=False), "waited_s": wait}
    log(f"idle: {wait}s with the schedules on and no mail, then {sample}s of docker stats")
    time.sleep(wait)
    with Stats() as stats:
        time.sleep(sample)
    return record | summarize(tool, stats.frames, series=False)


def workload(tool: Tool) -> JSON:
    results = Path(tempfile.mkdtemp()) / "parity.json"
    command = ["uv", "run", "--quiet", "pytest", "--tool", tool.name, "-k", HAPPY_PATH]
    with Stats() as stats:
        seconds = timed([*command, "--results", str(results)], cwd=REPO / "tests" / "parity")
    parity = json.loads(results.read_text())
    return {
        "command": "cd tests/parity && " + " ".join(command),
        "seconds": seconds,
        "parity": parity["summary"],
        "runs": next(iter(parity["tests"].values()))["runs"],
    } | summarize(tool, stats.frames, series=True)


def app_db(tool: Tool, statement: str) -> str:
    query = f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "{statement}"'
    return run([*tool.compose, "exec", "-T", "app-db", "sh", "-c", query]).stdout.strip()


def news_rows(tool: Tool) -> int:
    return int(app_db(tool, "select count(*) from public.news_email"))


def sent_rows(tool: Tool) -> int:
    return int(app_db(tool, "select count(*) from public.news_email where was_sent"))


def loop(tool: Tool, repeats: int) -> JSON:
    """Edit, redeploy, run one tick that summarizes and sends an email with the edited module."""
    # Untimed setup: an empty table (rows already sent would make the new ones duplicates, which
    # SUM-1 skips), then the 9 fixtures in the queue. A manual ingest can be cancelled by the
    # schedule's run, which then ingests them itself, so wait for the rows rather than the run.
    app_db(tool, "truncate public.news_email, public.news_email_history restart identity")
    run(make(f"{tool.name}-seed"))
    subprocess.run(make(f"{tool.name}-run", "FLOW=email-ingest"), cwd=REPO, capture_output=True, check=False)
    deadline = time.monotonic() + 120
    while news_rows(tool) < 9:
        if time.monotonic() > deadline:
            raise TimeoutError("the 9 fixtures were not ingested within 2 minutes")
        time.sleep(2)
    path = REPO / tool.edit
    original = path.read_bytes()
    cycles = []
    try:
        for i in range(repeats):
            path.write_bytes(original.rstrip(b"\n") + f"\n# measure.py loop edit {i + 1}\n".encode())
            sent = sent_rows(tool)  # not the mock's call log: `make prefect-up` recreates the mocks
            begin = time.monotonic()
            redeploy = timed(make(tool.redeploy))
            tick = timed(make(f"{tool.name}-run"))
            cycles.append(
                {
                    "redeploy_s": redeploy,
                    "tick_s": tick,
                    "total_s": round(time.monotonic() - begin, 2),
                    "emails_sent": sent_rows(tool) - sent,
                }
            )
    finally:
        path.write_bytes(original)
    run(make(tool.redeploy))  # put the unchanged code back into the stack
    return {"edited": tool.edit, "redeploy": f"make {tool.redeploy}", "run": f"make {tool.name}-run", "cycles": cycles}


def preflight(tool: Tool) -> None:
    lines = run(["docker", "ps", "--format", '{{.Names}}\t{{.Label "com.docker.compose.project"}}']).stdout.splitlines()
    others = [line.split("\t")[0] for line in lines if line and line.split("\t")[1:] != [tool.project]]
    if others:
        sys.exit(f"one stack at a time: stop these containers first: {', '.join(others)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tool", choices=sorted(TOOLS))
    parser.add_argument("--cold", action="store_true", help="remove the stack's images, then pull and build them")
    parser.add_argument("--phases", default="tests,starts,idle,workload,loop", help="comma-separated subset")
    parser.add_argument("--starts", type=int, default=3, help="clean starts to time (default 3)")
    parser.add_argument("--idle-wait", type=int, default=300, help="seconds before the idle sample (default 300)")
    parser.add_argument("--idle-sample", type=int, default=60, help="seconds of idle docker stats (default 60)")
    parser.add_argument("--loops", type=int, default=3, help="edit-redeploy-run cycles (default 3)")
    parser.add_argument("--update", action="store_true", help="replace only these phases in an existing results file")
    parser.add_argument(
        "--out", type=Path, help="results file (default: docs/comparison/data/measure-<tool>-<date>.json)"
    )
    args = parser.parse_args()
    tool = TOOLS[args.tool]
    phases = (["cold"] if args.cold else []) + args.phases.split(",")
    preflight(tool)

    now = datetime.now().astimezone()
    out = args.out or DATA / f"measure-{tool.name}-{now:%Y-%m-%d}.json"
    commit = output("git", "-C", str(REPO), "rev-parse", "--short", "HEAD")
    dirty = output("git", "-C", str(REPO), "status", "--porcelain")
    document: JSON = {
        "date": now.isoformat(timespec="seconds"),
        "host": host(),
        "tool": {"name": tool.name},
        "commit": commit + (" (with uncommitted changes)" if dirty and dirty != "unknown" else ""),
        "env_file": ENV_FILE,
        "command": os.environ.get("MEASURE_COMMAND") or " ".join(["python", "scripts/measure.py", *sys.argv[1:]]),
        "protocol": "D-021 in docs/decisions.md",
        "setup": setup_facts(tool),
        "phases": {},
    }

    if args.update and out.exists():
        previous = json.loads(out.read_text())
        updates = previous.pop("updates", [])
        updates.append({k: document[k] for k in ("date", "commit", "command")} | {"phases": phases})
        document = previous | {"updates": updates}

    def save() -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")

    order = ["tests", "cold", "starts", "idle", "workload", "loop"]
    for phase in sorted(phases, key=order.index):
        log(f"phase {phase}")
        began = datetime.now(UTC)
        if phase == "tests":
            result: Any = unit_tests(tool)
        elif phase == "cold":
            result = cold(tool)
        elif phase == "starts":
            result = []
            for i in range(args.starts):
                result.append(start(tool, first_run=True))
                log(f"start {i + 1}: up {result[-1]['make_up_s']}s, first green {result[-1]['first_green_s']}s")
        elif phase == "idle":
            result = idle(tool, args.idle_wait, args.idle_sample)
        elif phase == "workload":
            result = workload(tool)
        else:
            result = loop(tool, args.loops)
        document["phases"][phase] = {"started": began.isoformat(timespec="seconds"), "result": result}
        save()
    if phases != ["tests"]:  # the stack is up: read the version, and size the images it runs
        document["tool"]["version"] = tool_version(tool)
        document["setup"]["images"] = image_sizes(runtime_images(tool))
        save()
    log(f"done: {out.relative_to(REPO)} (the stack is still running; make {tool.name}-down stops it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
