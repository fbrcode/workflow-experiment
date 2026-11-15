"""Workflow 1, ingest: ING-1 to ING-4 against the seeded mailbox."""

from __future__ import annotations

from harness.checks import pick
from harness.expected import FIXTURE_NAMES, INSERT_DEFAULTS, email
from harness.world import World


def test_ingest_inserts_every_fixture_as_n8n_would(world: World) -> None:
    """ING-1 to ING-4: one row per UNSEEN email, values equal to the ING-4 vector, DB defaults."""
    world.mail.deliver()
    step = world.ingest()

    assert step.run.outcome == "completed", step.run
    rows = world.db.rows()
    assert [r["id"] for r in rows] == list(range(1, len(FIXTURE_NAMES) + 1)), "one row per email, in mailbox order"
    for row, fixture in zip(rows, FIXTURE_NAMES, strict=True):
        expected = email(fixture).insert
        assert pick(row, expected) == expected, fixture
        assert pick(row, INSERT_DEFAULTS) == INSERT_DEFAULTS, fixture
        assert step.covers(row["email_consumed_at"]), fixture
    assert len({r["email_consumed_at"] for r in rows}) == 1, "one batch is one transaction (spec ING-1, Q-19)"
    assert [(h["operation"], h["changed_fields"]) for h in world.db.history()] == [("INSERT", None)] * len(rows)
    assert world.mail.counts() == (len(FIXTURE_NAMES), 0), "ING-1 marks every fetched email as read"
    assert step.gemini == [] and step.telegram == []


def test_ingest_without_new_mail_changes_nothing(world: World) -> None:
    """ING-1 reads UNSEEN only: an empty poll and a repeated poll insert nothing (Q-8 polling)."""
    empty = world.ingest()
    assert empty.run.outcome == "completed", empty.run
    assert world.db.rows() == []

    world.mail.deliver("01")
    first = world.ingest()
    again = world.ingest()

    assert first.run.outcome == again.run.outcome == "completed", (first.run, again.run)
    assert [r["email_inbox_label"] for r in world.db.rows()] == ["morning-brew"]
    assert len(world.db.history()) == 1
    assert world.mail.counts() == (1, 0)
