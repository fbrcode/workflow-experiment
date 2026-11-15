"""Fault scenarios F-1 to F-7 (docs/comparison/criteria.md), SND-5 without a response, and Q-15."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from harness.checks import bare, changes, pick, prompt_of, send_params, sent, system_of
from harness.expected import (
    SND5_CHANGES,
    SUM6_CHANGES,
    SUM7A_CHANGES,
    SYSTEM_MESSAGE,
    Email,
    alert,
    email,
)
from harness.world import World

SAO_PAULO = timedelta(hours=-3)  # the alert's $now zone (D-019); Brazil has no DST since 2019


def ingest(world: World, fixture: str = "01-morning-brew") -> Email:
    world.mail.deliver(fixture[:2])
    step = world.ingest()
    assert step.run.outcome == "completed", step.run
    return email(fixture)


def test_f1_primary_model_fails_and_the_fallback_answers(world: World) -> None:
    """F-1: SUM-5 falls back on any primary error; SUM-6 stores the primary's name anyway (Q-2)."""
    expected = ingest(world)
    world.gemini.fault(503, {"model": bare(expected.model)}, count=-1)

    tick = world.tick()

    assert tick.run.outcome == "completed", tick.run
    models = [c["model"] for c in tick.gemini]
    primary = models.count(bare(expected.model))
    assert primary >= 1 and models == [bare(expected.model)] * primary + [bare(expected.model_fallback)], models
    assert {c["status"] for c in tick.gemini[:-1]} == {503} and tick.gemini[-1]["status"] == 200
    answer = tick.gemini[-1]
    assert (prompt_of(answer), system_of(answer)) == (expected.prompt, SYSTEM_MESSAGE)
    row = world.db.row(1)
    assert pick(row, {"summary_status", "summary_content", "model_used", "is_summarized", "summary_retry"}) == {
        "summary_status": "OK",
        "summary_content": answer["response_text"],
        "model_used": expected.model,
        "is_summarized": True,
        "summary_retry": 0,
    }
    assert [(send_params(c)["chat_id"], c["status"]) for c in tick.telegram] == [(world.settings.chats["news"], 200)]
    assert (row["was_sent"], row["formatted_summary"]) == (True, tick.telegram[0]["response"]["result"]["text"])
    world.note("primary_attempts", primary)


def test_f2_both_models_fail_so_the_error_is_stored_and_alerted(world: World) -> None:
    """F-2: SUM-7a then SUM-7b; the run still succeeds (`onError: continueErrorOutput`)."""
    expected = ingest(world)
    world.gemini.fault(500, count=-1)

    tick = world.tick()

    assert tick.run.outcome == "completed", tick.run
    models = [c["model"] for c in tick.gemini]
    split = models.index(bare(expected.model_fallback))
    assert split >= 1 and set(models[:split]) == {bare(expected.model)}, models
    assert set(models[split:]) == {bare(expected.model_fallback)} and {c["status"] for c in tick.gemini} == {500}
    row = world.db.row(1)
    assert pick(row, {"summary_content", "model_used", "is_summarized", "summary_retry", "was_sent"}) == {
        "summary_content": None,
        "model_used": expected.model,  # D-008
        "is_summarized": False,
        "summary_retry": 0,
        "was_sent": False,
    }
    assert row["summary_status"] not in (None, "", "OK") and tick.covers(row["summarized_at"])
    assert changes(world.db.history())[1] == [("INSERT", set()), ("UPDATE", SUM7A_CHANGES)]

    assert len(tick.telegram) == 1, "the alert, and no summary (the row isn't summarized)"
    params = send_params(tick.telegram[0])
    assert {**params, "text": None} == sent(world.settings.alert_chat, None, "HTML")
    match = alert("fixture:01-morning-brew").pattern.fullmatch(params["text"])
    assert match, params["text"]
    assert match["error"] == row["summary_status"], "SUM-7a and SUM-7b both read get-error's message"
    stamp = datetime.fromisoformat(match["now"])
    assert stamp.utcoffset() == SAO_PAULO and tick.covers(stamp), match["now"]
    assert tick.telegram[0]["status"] == 200
    world.note("attempts_per_model", dict(Counter(models)))
    world.note("summary_status", row["summary_status"])


def test_f3_telegram_rejects_the_message_so_it_is_summarized_again(world: World) -> None:
    """F-3: SND-5 stores Telegram's description and resets is_summarized (Q-12); the next tick retries."""
    expected = ingest(world)
    description = "Bad Request: can't parse entities: forced by the parity suite"
    world.telegram.fault(400, {"method": "sendMessage"}, count=1, description=description)

    first = world.tick()

    assert first.run.outcome == "completed", first.run
    assert [send_params(c) for c in first.telegram] == [
        sent(world.settings.chats["news"], expected.message, "MarkdownV2")
    ]
    assert first.telegram[0]["status"] == 400
    row = world.db.row(1)
    assert pick(
        row, {"was_sent", "is_summarized", "sent_status", "sent_retry", "formatted_summary", "formatted_length"}
    ) == {
        "was_sent": False,
        "is_summarized": False,
        "sent_status": description,
        "sent_retry": 0,
        "formatted_summary": expected.message,  # the escaped text that was sent
        "formatted_length": expected.final_length,
    }
    assert (row["summary_retry"], row["summary_content"]) == (0, expected.summary) and first.covers(row["sent_at"])
    assert changes(world.db.history())[1] == [("INSERT", set()), ("UPDATE", SUM6_CHANGES), ("UPDATE", SND5_CHANGES)]

    second = world.tick()

    assert second.run.outcome == "completed", second.run
    assert [prompt_of(c) for c in second.gemini] == [expected.prompt], "summarized again (Q-12)"
    assert [c["status"] for c in second.telegram] == [200]
    row = world.db.row(1)
    assert pick(
        row, {"summary_retry", "is_summarized", "was_sent", "sent_status", "sent_retry", "formatted_summary"}
    ) == {
        "summary_retry": 1,  # Q-11: successes count too
        "is_summarized": True,
        "was_sent": True,
        "sent_status": "OK",
        "sent_retry": 1,
        "formatted_summary": expected.rendered,
    }


def test_snd5_telegram_unreachable_is_handled_like_a_rejection(world: World) -> None:
    """SND-5 without a response (network error): same writes, the client's message as sent_status."""
    expected = ingest(world)

    with world.docker.stopped("mock-telegram"):
        tick = world.tick()

    assert tick.run.outcome == "completed", tick.run
    assert [c["status"] for c in tick.gemini] == [200]
    row = world.db.row(1)
    assert pick(row, {"was_sent", "is_summarized", "sent_retry", "formatted_summary", "formatted_length"}) == {
        "was_sent": False,
        "is_summarized": False,
        "sent_retry": 0,
        "formatted_summary": expected.message,
        "formatted_length": expected.final_length,
    }
    assert row["sent_status"] not in (None, "", "OK") and tick.covers(row["sent_at"])
    world.note("sent_status", row["sent_status"])


def test_f4_database_down_fails_the_tick_and_the_next_tick_recovers(world: World) -> None:
    """F-4: an unhandled node error ends the run (D-019), visibly, with no partial writes."""
    ingest(world)
    rows, history = world.db.rows(), world.db.history()

    with world.docker.stopped("app-db"):
        down = world.tick()

    assert down.run.outcome == "failed", down.run
    assert down.gemini == [] and down.telegram == []
    assert (world.db.rows(), world.db.history()) == (rows, history)

    up = world.tick()

    assert up.run.outcome == "completed", up.run
    assert pick(world.db.row(1), {"is_summarized", "was_sent"}) == {"is_summarized": True, "was_sent": True}


def test_f5_a_duplicate_from_a_later_ingest_is_deleted(world: World) -> None:
    """F-5: DUP-1 keeps the earliest-consumed copy, so only one message goes out."""
    expected = ingest(world, "02-the-news")
    ingest(world, "03-the-news-duplicate")
    first, later = world.db.rows()
    assert first["email_consumed_at"] < later["email_consumed_at"]

    tick = world.tick()

    assert tick.run.outcome == "completed", tick.run
    assert [r["id"] for r in world.db.rows()] == [first["id"]], "DUP-1 deleted the later copy"
    assert [prompt_of(c) for c in tick.gemini] == [expected.prompt]
    assert [send_params(c) for c in tick.telegram] == [
        sent(world.settings.chats["news"], expected.message, "MarkdownV2")
    ]
    idle = world.tick()
    assert idle.gemini == [] and idle.telegram == [], "nothing left to summarize or send"


def test_f6_an_empty_queue_calls_nothing_and_writes_nothing(world: World) -> None:
    """F-6: SUM-2 ends branch A quietly; SND-1a, branch C does nothing."""
    tick = world.tick()

    assert tick.run.outcome == "completed", tick.run
    assert tick.gemini == [] and tick.telegram == []
    assert world.db.rows() == [] and world.db.history() == []


def test_f7_a_row_is_left_alone_after_six_failed_attempts(world: World) -> None:
    """F-7: summary_retry goes -1 to 5, one alert per attempt, then `summary_retry < 5` skips it (Q-17)."""
    ingest(world)
    world.gemini.fault(500, count=-1)

    for attempt in range(6):
        tick = world.tick()
        assert tick.run.outcome == "completed", (attempt, tick.run)
        assert world.db.row(1)["summary_retry"] == attempt
        assert [send_params(c)["chat_id"] for c in tick.telegram] == [world.settings.alert_chat], attempt

    last = world.tick()

    assert last.run.outcome == "completed", last.run
    assert last.gemini == [] and last.telegram == [], "no seventh attempt and no alert"
    assert pick(world.db.row(1), {"summary_retry", "is_summarized"}) == {"summary_retry": 5, "is_summarized": False}


def test_q15_an_alert_telegram_rejects_fails_the_tick_and_skips_the_rest(world: World) -> None:
    """Q-15: the alert isn't HTML-escaped, Telegram rejects it, and the failed node ends the run (D-019).

    SUM-7a has already run (canvas order). DUP-1 and branch C don't run until the next tick.
    The rows are written straight to app-db, the state the two workflows share.
    """
    case = alert("edge:q15-html-in-subject")
    source = case.transform
    ready = email("02-the-news")
    duplicate = email("09-pragmatic-engineer").insert
    now = world.db.now()
    failing = world.db.insert(
        **{
            **email("01-morning-brew").insert,
            "email_from": source["from"],
            "email_subject": source["subject"],
            "email_date": source["date"],
            "to_translate": source["to_translate"],
        }
    )
    unsent = world.db.insert(
        **ready.insert, is_summarized=True, summary_content=ready.summary, model_used=ready.model, summary_retry=0
    )
    done = {"is_summarized": True, "was_sent": True, "summary_retry": 0, "sent_retry": 0}
    kept = world.db.insert(**duplicate, **done, email_consumed_at=now - timedelta(minutes=2))
    dropped = world.db.insert(**duplicate, **done, email_consumed_at=now - timedelta(minutes=1))
    world.gemini.fault(500, count=-1)

    tick = world.tick()

    assert tick.run.outcome == "failed", tick.run
    row = world.db.row(failing)
    assert pick(row, {"summary_retry", "is_summarized", "model_used"}) == {
        "summary_retry": 0,
        "is_summarized": False,
        "model_used": ready.model,
    }, "SUM-7a runs before the alert"
    assert len(tick.telegram) == 1, "the alert only: branch C didn't run"
    call = tick.telegram[0]
    params = send_params(call)
    assert {**params, "text": None} == sent(world.settings.alert_chat, None, "HTML")
    match = case.pattern.fullmatch(params["text"])
    assert match and match["error"] == row["summary_status"], params["text"]
    assert {"status": call["status"], "ok": False, "description": call["response"]["description"]} == case.verdict
    assert world.db.row(unsent)["was_sent"] is False
    assert [r["id"] for r in world.db.rows()] == [failing, unsent, kept, dropped], "DUP-1 didn't run"

    world.gemini.reset()
    recovery = world.tick()

    assert recovery.run.outcome == "completed", recovery.run
    assert [r["id"] for r in world.db.rows()] == [failing, unsent, kept], "DUP-1 ran on the next tick"
    assert [(send_params(c)["chat_id"], c["status"]) for c in recovery.telegram] == [
        (world.settings.chats["news"], 200)
    ]
