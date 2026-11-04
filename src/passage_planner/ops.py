"""Read the vessel-ops-panel maintenance DB (read-only) and work out what
preventative maintenance comes due during a passage.

The ops-panel `tasks` table stores an interval as ``period`` + ``interval_type``
(``days``, ``hrs``, ``use``) and the last completion as a ``YYYY-MM-DD`` string:

* ``days``  — calendar interval; next due = last_completed + period days.
* ``use``   — per-outing (rinse the windlass, flush the heads …); every one of
              these is a "do before/after this passage" item.
* ``hrs``   — engine-hour interval; can only be dated if current engine hours
              are known. The ops-panel doesn't track them yet, so these are
              listed with a caveat, not dated.

Access is a bare ``sqlite3`` read (``mode=ro``) — this framework does not import
the ops-panel's own code.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from .errors import OpsPanelError


class DueStatus(StrEnum):
    OVERDUE = "overdue"
    DUE = "due"                # due within the passage window
    SOON = "soon"              # due within the buffer just after arrival
    PER_PASSAGE = "per_passage"  # a 'use' task — do before/after every outing
    HOURS = "hours"            # engine-hours interval, not dated


@dataclass(frozen=True, slots=True)
class MaintenanceItem:
    system: str
    task: str
    interval_type: str
    period: str
    last_completed: date | None
    next_due: date | None
    status: DueStatus


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    db_path: str
    generated: datetime
    window_start: date
    window_end: date
    items: tuple[MaintenanceItem, ...]

    def by_status(self, *statuses: DueStatus) -> tuple[MaintenanceItem, ...]:
        return tuple(i for i in self.items if i.status in statuses)

    @property
    def any_overdue(self) -> bool:
        return any(i.status is DueStatus.OVERDUE for i in self.items)


def _parse_date(text: object) -> date | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return datetime.strptime(text.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def read_maintenance_due(
    db_path: str | Path,
    window_start: date,
    window_end: date,
    *,
    soon_buffer_days: int = 10,
) -> MaintenanceReport:
    p = Path(db_path)
    if not p.is_file():
        raise OpsPanelError(f"ops-panel DB not found: {p}")

    soon_end = window_end + timedelta(days=soon_buffer_days)
    try:
        conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise OpsPanelError(f"cannot open {p}: {exc}") from exc
    try:
        cur = conn.execute(
            "SELECT system, task_name, period, interval_type, last_completed_date "
            "FROM tasks WHERE task_kind = 'recurring'"
        )
        rows = cur.fetchall()
    except sqlite3.Error as exc:
        raise OpsPanelError(f"{p}: query failed ({exc}) — schema may have changed") from exc
    finally:
        conn.close()

    items: list[MaintenanceItem] = []
    for system, task, period, itype, last_raw in rows:
        last = _parse_date(last_raw)
        itype = (itype or "").strip().lower()

        if itype == "use":
            items.append(MaintenanceItem(system, task, itype, str(period), last, None,
                                         DueStatus.PER_PASSAGE))
            continue
        if itype in ("hrs", "hours"):
            items.append(MaintenanceItem(system, task, itype, str(period), last, None,
                                         DueStatus.HOURS))
            continue
        if itype != "days":
            continue  # unknown interval type — skip quietly

        try:
            n = int(float(period))
        except (TypeError, ValueError):
            continue
        if last is None:
            # No completion record — treat as due (a fresh boat item, or lost history).
            items.append(MaintenanceItem(system, task, itype, str(period), None, None,
                                         DueStatus.OVERDUE))
            continue
        next_due = last + timedelta(days=n)
        if next_due < window_start:
            status = DueStatus.OVERDUE
        elif next_due <= window_end:
            status = DueStatus.DUE
        elif next_due <= soon_end:
            status = DueStatus.SOON
        else:
            continue  # not relevant to this passage
        items.append(MaintenanceItem(system, task, itype, str(period), last, next_due, status))

    items.sort(key=lambda i: (i.next_due or date.max, i.system, i.task))
    return MaintenanceReport(
        db_path=str(p),
        generated=datetime.now(UTC),
        window_start=window_start,
        window_end=window_end,
        items=tuple(items),
    )
