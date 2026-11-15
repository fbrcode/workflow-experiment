"""The shared world a scenario sets up and inspects: app-db, the mailbox, the two mocks, docker.

None of this knows which tool is under test. ``World.run`` starts a flow through the driver and
returns a ``Step``: the run, the database clock around it, and the mock calls it caused.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .drivers import INGEST, TICK, Driver, Run
from .settings import FIXTURES, REPO, Settings

sys.path.insert(0, str(REPO / "scripts"))
import seed_mail  # the repo's own IMAP seeding helpers (scripts/seed_mail.py)

JSON = dict[str, Any]
CLOCK_SLACK = timedelta(seconds=2)  # tools may stamp rows from their own clock, not the DB's
OUTAGE_SETTLE = 15.0  # seconds from the end of an outage to the next run (see Docker.stopped)


class Db:
    """app-db through its host port. A new connection per call, so a stopped DB (F-4) is harmless."""

    def __init__(self, settings: Settings) -> None:
        self.dsn = settings.db

    @contextmanager
    def _cursor(self) -> Iterator[psycopg.Cursor[JSON]]:
        with psycopg.connect(**self.dsn, autocommit=True, row_factory=dict_row, connect_timeout=5) as conn:
            yield conn.cursor()

    def reset(self) -> None:
        with self._cursor() as cur:
            cur.execute("set lock_timeout = '10s'")
            cur.execute("truncate public.news_email, public.news_email_history restart identity")

    def rows(self) -> list[JSON]:
        with self._cursor() as cur:
            return cur.execute("select * from public.news_email order by id").fetchall()

    def row(self, row_id: int) -> JSON:
        with self._cursor() as cur:
            found = cur.execute("select * from public.news_email where id = %s", (row_id,)).fetchone()
        assert found is not None, f"news_email row {row_id} is gone"
        return found

    def history(self) -> list[JSON]:
        """The audit trigger's log: one entry per INSERT and per UPDATE statement row."""
        with self._cursor() as cur:
            query = "select news_email_id, operation, changed_fields from public.news_email_history order by id"
            return cur.execute(query).fetchall()

    def now(self) -> datetime:
        with self._cursor() as cur:
            return cur.execute("select clock_timestamp() as now").fetchone()["now"]

    def insert(self, **columns: Any) -> int:
        names = list(columns)
        query = sql.SQL("insert into public.news_email ({}) values ({}) returning id").format(
            sql.SQL(", ").join(map(sql.Identifier, names)), sql.SQL(", ").join(sql.Placeholder() * len(names))
        )
        with self._cursor() as cur:
            return cur.execute(query, [columns[n] for n in names]).fetchone()["id"]


class Mail:
    """The stack's GreenMail mailbox, through the host IMAP port, with the repo's seeding helpers."""

    def __init__(self, settings: Settings) -> None:
        self.port = settings.imap_port
        self.mailbox = settings.mailbox

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        conn = seed_mail.connect("localhost", self.port)
        try:
            seed_mail.ensure_mailbox(conn, self.mailbox)
            yield conn
        finally:
            conn.logout()

    def purge(self) -> None:
        with self._conn() as conn:
            seed_mail.purge(conn, self.mailbox)

    def deliver(self, prefix: str = "") -> list[str]:
        """APPEND the fixtures whose file name starts with ``prefix`` (all of them by default), UNSEEN."""
        files = sorted(FIXTURES.glob(f"{prefix}*.eml"))
        assert files, f"no fixture matches {prefix!r}"
        with self._conn() as conn:
            for path in files:
                seed_mail.append(conn, self.mailbox, path)
        return [p.stem for p in files]

    def counts(self) -> tuple[int, int]:
        """(messages, unseen) in the mailbox."""
        with self._conn() as conn:
            return seed_mail.counts(conn, self.mailbox)


class Mock:
    """A mock's control API (docs/infra.md): recorded calls, queued faults, reset."""

    def __init__(self, url: str) -> None:
        self.http = httpx.Client(base_url=url, timeout=10.0)

    def reset(self) -> None:
        self.http.post("/_mock/reset").raise_for_status()

    def calls(self) -> list[JSON]:
        response = self.http.get("/_mock/calls")
        response.raise_for_status()
        return response.json()

    def fault(self, status: int, when: dict[str, str] | None = None, count: int = 1, **extra: Any) -> None:
        body = {"status": status, "when": when or {}, "count": count, **extra}
        self.http.post("/_mock/faults", json=body).raise_for_status()


class Docker:
    """Stop and start one service container of the stack under test (F-4, SND-5 network error)."""

    def __init__(self, project: str) -> None:
        self.project = project

    def _container(self, service: str) -> str:
        labels = [f"label=com.docker.compose.project={self.project}", f"label=com.docker.compose.service={service}"]
        out = subprocess.run(
            ["docker", "ps", "-aq", *(arg for label in labels for arg in ("--filter", label))],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
        assert len(out) == 1, f"expected one {service} container in {self.project}, found {out}"
        return out[0]

    def _health(self, container: str) -> str:
        return subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Health.Status}}", container],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    @contextmanager
    def stopped(self, service: str, timeout: float = 120.0) -> Iterator[None]:
        """Stop ``service`` for the body, then start it, wait until healthy, and let the outage settle.

        The settle time stands in for the 10 minutes until the next real tick: long enough for a
        client to forget the outage. Kestra's JVM keeps a failed DNS lookup for 10 s
        (`networkaddress.cache.negative.ttl`), so a run right after the restart can still fail.
        """
        container = self._container(service)
        subprocess.run(["docker", "stop", container], check=True, capture_output=True)
        try:
            yield
        finally:
            outage_end = time.monotonic()
            subprocess.run(["docker", "start", container], check=True, capture_output=True)
            deadline = outage_end + timeout
            while self._health(container) != "healthy":
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{service} not healthy {timeout:.0f}s after restart")
                time.sleep(1)
            time.sleep(max(0.0, outage_end + OUTAGE_SETTLE - time.monotonic()))


@dataclass
class Step:
    """One run plus what it did to the outside world."""

    run: Run
    started: datetime | None  # DB clock just before the trigger (None if the DB was down)
    finished: datetime | None
    gemini: list[JSON] = field(default_factory=list)  # generateContent calls during the run
    telegram: list[JSON] = field(default_factory=list)  # sendMessage calls during the run

    def covers(self, stamp: datetime | None) -> bool:
        """Whether a timestamp written by the run falls inside it (with clock slack)."""
        assert self.started and self.finished, "the DB clock wasn't readable around this run"
        return stamp is not None and self.started - CLOCK_SLACK <= stamp <= self.finished + CLOCK_SLACK


@dataclass
class World:
    settings: Settings
    driver: Driver
    record: JSON  # this test's entry in the results file: {"runs": [...], "notes": {...}}
    db: Db = field(init=False)
    mail: Mail = field(init=False)
    telegram: Mock = field(init=False)
    gemini: Mock = field(init=False)
    docker: Docker = field(init=False)

    def __post_init__(self) -> None:
        self.db = Db(self.settings)
        self.mail = Mail(self.settings)
        self.telegram = Mock(self.settings.telegram_url)
        self.gemini = Mock(self.settings.gemini_url)
        self.docker = Docker(self.driver.compose_project)

    def reset(self) -> None:
        """Empty table, audit log, mailbox and mock state, with ids restarting at 1."""
        self.driver.wait_idle()
        self.telegram.reset()
        self.gemini.reset()
        self.db.reset()
        self.mail.purge()

    def _clock(self) -> datetime | None:
        try:
            return self.db.now()
        except psycopg.OperationalError:
            return None

    def _calls(self, mock: Mock) -> list[JSON] | None:
        try:
            return mock.calls()
        except httpx.TransportError:
            return None  # the mock is stopped (SND-5 network scenario)

    def run(self, flow: str) -> Step:
        before = {name: self._calls(mock) for name, mock in (("gemini", self.gemini), ("telegram", self.telegram))}
        started = self._clock()
        run = self.driver.run(flow)
        finished = self._clock()
        entry = {"flow": run.flow, "state": run.state, "outcome": run.outcome, "seconds": round(run.seconds, 2)}
        self.record["runs"].append(entry)
        step = Step(run, started, finished)
        for name, mock in (("gemini", self.gemini), ("telegram", self.telegram)):
            seen = {c["id"] for c in before[name] or []}
            new = [c for c in self._calls(mock) or [] if c["id"] not in seen]
            if name == "gemini":
                step.gemini = [c for c in new if c.get("action") in ("generateContent", "streamGenerateContent")]
            else:
                step.telegram = [c for c in new if c.get("method") == "sendMessage"]
        return step

    def note(self, key: str, value: Any) -> None:
        """Keep an observation (retry counts, client error texts) in the results file."""
        self.record["notes"][key] = value

    def ingest(self) -> Step:
        return self.run(INGEST)

    def tick(self) -> Step:
        return self.run(TICK)
