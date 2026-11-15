"""Triggers: the schedules each tool declares (spec `10min-runner`; Q-8 / D-019 one-minute ingest poll)."""

from __future__ import annotations

from harness.drivers import INGEST, TICK, Driver


def test_schedules_match_the_baseline(driver: Driver) -> None:
    assert driver.schedules() == {INGEST: ["* * * * *"], TICK: ["*/10 * * * *"]}
