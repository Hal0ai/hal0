"""Five-field UTC cron expressions: validation and the next and previous fire.

hal0 writes cron expressions to the memory engine (a knowledge page's
``trigger.refresh_cron``) and runs one schedule itself (scheduled
consolidation, :mod:`hal0.memory.refresh_runner`). The engine evaluates its
own with ``croniter``; hal0 has no cron dependency, so this module reads the
subset both agree on:

* five fields: minute (0-59), hour (0-23), day of month (1-31), month (1-12)
  and day of week (0-7, where 0 and 7 are Sunday);
* in each field ``*``, a number, a range ``a-b``, a step ``*/n`` or ``a-b/n``,
  and comma-separated lists of those.

Names (``MON``, ``JAN``), ``?``, ``L``, ``W``, ``#`` and the ``@daily``
shorthands are refused: the dashboard writes none of them, and a smaller
grammar is one every reader parses the same way. As in every cron, when both
the day of month and the day of week are restricted a day matches either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

#: (name, low, high) for each of the five fields.
_FIELDS: tuple[tuple[str, int, int], ...] = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day of month", 1, 31),
    ("month", 1, 12),
    ("day of week", 0, 7),
)

#: How far :func:`next_fire` and :func:`previous_fire` look before giving up.
#: Four years and a day covers every expression that can fire at all (29
#: February on a given weekday comes round within 28 years, but such an
#: expression names both fields and fires on either).
_SEARCH_DAYS = 4 * 366 + 1


class CronError(ValueError):
    """The expression is not one this module reads."""


def _parse_field(text: str, name: str, low: int, high: int) -> frozenset[int]:
    if not text:
        raise CronError(f"the {name} field is empty")
    values: set[int] = set()
    for part in text.split(","):
        if not part:
            raise CronError(f"the {name} field has an empty list entry")
        step = 1
        body = part
        if "/" in part:
            body, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) < 1:
                raise CronError(f"the {name} field has a bad step {part!r}")
            step = int(step_text)
        if body == "*":
            start, end = low, high
        elif "-" in body:
            a, _, b = body.partition("-")
            if not (a.isdigit() and b.isdigit()):
                raise CronError(f"the {name} field has a bad range {part!r}")
            start, end = int(a), int(b)
            if start > end:
                raise CronError(f"the {name} field has a range that runs backwards {part!r}")
        elif body.isdigit():
            start = int(body)
            # ``5/15`` is "from 5, every 15" as croniter reads it.
            end = high if "/" in part else start
        else:
            raise CronError(f"the {name} field has {part!r}, which is not a number")
        if start < low or end > high:
            raise CronError(f"the {name} field is {low}-{high}, and {part!r} is outside it")
        values.update(range(start, end + 1, step))
    return frozenset(values)


@dataclass(frozen=True)
class CronSchedule:
    """A parsed expression. :meth:`matches` says whether a minute fires."""

    expression: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    days_restricted: bool
    weekdays_restricted: bool

    def _day_matches(self, moment: datetime) -> bool:
        dom = moment.day in self.days
        # Python: Monday is 0; cron: Sunday is 0 (and 7).
        dow = (moment.weekday() + 1) % 7 in self.weekdays
        if self.days_restricted and self.weekdays_restricted:
            return dom or dow
        if self.days_restricted:
            return dom
        if self.weekdays_restricted:
            return dow
        return True

    def matches(self, moment: datetime) -> bool:
        """Whether the minute ``moment`` falls in (UTC) is one this schedule fires on."""
        moment = _utc(moment)
        return (
            moment.minute in self.minutes
            and moment.hour in self.hours
            and moment.month in self.months
            and self._day_matches(moment)
        )

    def _day_candidates(self, start: datetime, step: int) -> datetime | None:
        """The first (``step`` 1) or last (``step`` -1) firing minute from ``start``,
        that minute included, walking a day at a time and then within the day."""
        hours = sorted(self.hours, reverse=step < 0)
        minutes = sorted(self.minutes, reverse=step < 0)
        day = start.replace(hour=0, minute=0, second=0, microsecond=0)
        for offset in range(_SEARCH_DAYS):
            current = day + timedelta(days=offset * step)
            if current.month not in self.months or not self._day_matches(current):
                continue
            for hour in hours:
                for minute in minutes:
                    candidate = current.replace(hour=hour, minute=minute)
                    if (step > 0 and candidate >= start) or (step < 0 and candidate <= start):
                        return candidate
        return None


def parse_cron(expression: str) -> CronSchedule:
    """Parse ``expression``; :class:`CronError` says what is wrong with it."""
    if not isinstance(expression, str):
        raise CronError("a cron expression is a string")
    fields = expression.split()
    if len(fields) != 5:
        raise CronError(
            f"a cron expression has five fields (minute hour day month weekday), "
            f"{expression!r} has {len(fields)}"
        )
    parsed = [
        _parse_field(text, name, low, high)
        for text, (name, low, high) in zip(fields, _FIELDS, strict=True)
    ]
    weekdays = frozenset(0 if day == 7 else day for day in parsed[4])
    schedule = CronSchedule(
        expression=" ".join(fields),
        minutes=parsed[0],
        hours=parsed[1],
        days=parsed[2],
        months=parsed[3],
        weekdays=weekdays,
        days_restricted=fields[2] != "*",
        weekdays_restricted=fields[4] != "*",
    )
    if schedule._day_candidates(datetime(2000, 1, 1, tzinfo=UTC), 1) is None:
        raise CronError(f"{expression!r} names no day that exists")
    return schedule


def validate_cron(expression: str) -> str:
    """``expression`` with its fields joined by single spaces, or :class:`CronError`."""
    return parse_cron(expression).expression


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def next_fire(expression: str | CronSchedule, after: datetime) -> datetime:
    """The first firing minute strictly after ``after`` (UTC)."""
    schedule = expression if isinstance(expression, CronSchedule) else parse_cron(expression)
    start = _utc(after).replace(second=0, microsecond=0) + timedelta(minutes=1)
    found = schedule._day_candidates(start, 1)
    if found is None:  # pragma: no cover - parse_cron refuses such expressions
        raise CronError(f"{schedule.expression!r} never fires")
    return found


def previous_fire(expression: str | CronSchedule, at: datetime) -> datetime:
    """The last firing minute at or before ``at`` (UTC)."""
    schedule = expression if isinstance(expression, CronSchedule) else parse_cron(expression)
    start = _utc(at).replace(second=0, microsecond=0)
    found = schedule._day_candidates(start, -1)
    if found is None:  # pragma: no cover - parse_cron refuses such expressions
        raise CronError(f"{schedule.expression!r} never fires")
    return found


__all__ = [
    "CronError",
    "CronSchedule",
    "next_fire",
    "parse_cron",
    "previous_fire",
    "validate_cron",
]
