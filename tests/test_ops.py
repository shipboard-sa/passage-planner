"""Tests for passage_planner.ops (maintenance-due from the ops-panel DB)."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from passage_planner.errors import OpsPanelError
from passage_planner.ops import DueStatus, read_maintenance_due

_DDL = """
CREATE TABLE tasks (
  task_id INTEGER PRIMARY KEY,
  system TEXT NOT NULL,
  task_name TEXT NOT NULL,
  period TEXT NOT NULL,
  interval_type TEXT NOT NULL,
  last_completed_date TEXT,
  task_kind TEXT NOT NULL DEFAULT 'recurring'
);
"""


def _db(tmp_path: Path, rows: list[tuple]) -> Path:
    p = tmp_path / "vessel_local.db"
    conn = sqlite3.connect(p)
    conn.executescript(_DDL)
    conn.executemany(
        "INSERT INTO tasks (system, task_name, period, interval_type, "
        "last_completed_date, task_kind) VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return p


def test_classifies_each_interval_type(tmp_path: Path) -> None:
    start = date(2026, 10, 15)
    end = date(2026, 10, 18)
    rows = [
        # overdue: 30-day interval last done 60 days before the window
        ("Racor", "drain", "30", "days", (start - timedelta(days=60)).isoformat(), "recurring"),
        # due in window: 90-day, last done 89 days before start
        ("Rig", "tune", "90", "days", (start - timedelta(days=89)).isoformat(), "recurring"),
        # soon (within buffer after end): 30-day, next due 5 days after end
        ("Bilge", "pump test", "30", "days", (end - timedelta(days=25)).isoformat(), "recurring"),
        # not relevant: 365-day, done recently
        ("Hull", "anodes", "365", "days", (start - timedelta(days=10)).isoformat(), "recurring"),
        # per-passage
        ("Heads", "flush", "1", "use", (start - timedelta(days=200)).isoformat(), "recurring"),
        # engine hours
        ("Yanmar", "oil change", "250", "hrs", None, "recurring"),
        # no last_completed -> overdue
        ("Windlass", "gearbox oil", "180", "days", None, "recurring"),
        # one_off tasks are excluded
        ("Project", "send liferaft", "1", "use", None, "one_off"),
    ]
    r = read_maintenance_due(_db(tmp_path, rows), start, end, soon_buffer_days=10)

    by = {i.task: i.status for i in r.items}
    assert by["drain"] is DueStatus.OVERDUE
    assert by["tune"] is DueStatus.DUE
    assert by["pump test"] is DueStatus.SOON
    assert by["flush"] is DueStatus.PER_PASSAGE
    assert by["oil change"] is DueStatus.HOURS
    assert by["gearbox oil"] is DueStatus.OVERDUE
    assert "anodes" not in by            # not relevant to the window
    assert "send liferaft" not in by     # one_off excluded
    assert r.any_overdue is True


def test_missing_db_raises() -> None:
    with pytest.raises(OpsPanelError, match="not found"):
        read_maintenance_due("nope/vessel_local.db", date(2026, 1, 1), date(2026, 1, 2))


def test_bad_schema_raises(tmp_path: Path) -> None:
    p = tmp_path / "x.db"
    sqlite3.connect(p).execute("CREATE TABLE other (a INTEGER)")
    with pytest.raises(OpsPanelError, match="schema"):
        read_maintenance_due(p, date(2026, 1, 1), date(2026, 1, 2))
