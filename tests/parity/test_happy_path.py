"""Workflow 2, happy path: every fixture summarized and sent, one per 10-minute tick."""

from __future__ import annotations

from harness.checks import bare, changes, pick, prompt_of, send_params, sent, system_of
from harness.expected import FIXTURE_NAMES, SND4_CHANGES, SUM6_CHANGES, SYSTEM_MESSAGE, email
from harness.world import World


def test_each_tick_summarizes_and_sends_one_email(world: World) -> None:
    """SUM-1 to SUM-6, DUP-1, SND-1 to SND-4 for all 9 fixtures, then an idle tick (F-6, SND-1a).

    All 9 arrive in one ingest batch, so 02 and its duplicate 03 share `email_consumed_at`, tie
    in DUP-1's rank() and are both kept and sent (Q-19).
    """
    world.mail.deliver()
    assert world.ingest().run.outcome == "completed"
    total = len(FIXTURE_NAMES)

    for row_id, fixture in enumerate(FIXTURE_NAMES, start=1):
        expected = email(fixture)
        tick = world.tick()
        assert tick.run.outcome == "completed", (fixture, tick.run)

        # SUM-5: one call to the primary model, the SUM-4 prompt and n8n's default system message.
        assert [(c["model"], c["status"]) for c in tick.gemini] == [(bare(expected.model), 200)], fixture
        assert prompt_of(tick.gemini[0]) == expected.prompt, fixture
        assert system_of(tick.gemini[0]) == SYSTEM_MESSAGE, fixture

        # SND-3: the SND-2 message to the category's chat; mock-telegram accepted it.
        assert [send_params(c) for c in tick.telegram] == [
            sent(world.settings.chats[expected.category], expected.message, "MarkdownV2")
        ], fixture
        assert tick.telegram[0]["status"] == 200, (fixture, tick.telegram[0]["response"])

        rows = world.db.rows()
        assert len(rows) == total, "Q-19: same-batch duplicates both survive DUP-1"
        row = rows[row_id - 1]
        assert pick(row, expected.insert) == expected.insert, fixture
        assert pick(
            row,
            {"summary_status", "summary_content", "model_used", "is_summarized", "summary_retry"},
        ) == {  # SUM-6
            "summary_status": "OK",
            "summary_content": expected.summary,
            "model_used": expected.model,
            "is_summarized": True,
            "summary_retry": 0,
        }, fixture
        assert pick(
            row,
            {"was_sent", "sent_status", "sent_retry", "formatted_summary", "formatted_length"},
        ) == {  # SND-4
            "was_sent": True,
            "sent_status": "OK",
            "sent_retry": 0,
            "formatted_summary": expected.rendered,
            "formatted_length": expected.final_length,
        }, fixture
        assert tick.covers(row["summarized_at"]) and tick.covers(row["sent_at"]), fixture
        assert changes(world.db.history())[row_id] == [
            ("INSERT", set()),
            ("UPDATE", SUM6_CHANGES),
            ("UPDATE", SND4_CHANGES),
        ], f"{fixture}: one UPDATE per step, touching exactly the baseline's columns"
        for later in rows[row_id:]:
            assert (later["is_summarized"], later["summary_retry"]) == (False, -1), "one row per branch per tick"

    history = len(world.db.history())
    idle = world.tick()
    assert idle.run.outcome == "completed", idle.run
    assert idle.gemini == [] and idle.telegram == [], "SUM-2 and SND-1a: an empty queue calls nothing"
    assert len(world.db.history()) == history, "F-6: and writes nothing"
