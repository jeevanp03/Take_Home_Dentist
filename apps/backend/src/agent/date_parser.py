"""Natural-language date expression → ISO date range parser.

The LLM usually produces ISO dates directly (Gemini is good at this with
the current date in the system prompt).  This parser serves as a
**validation/fallback**: if the LLM passes a natural-language string
instead of ISO to ``get_available_slots``, the tool handler runs it
through ``parse_date_expression`` before querying the DB.

Usage::

    result = parse_date_expression("next week")
    # {"start": "2026-04-06", "end": "2026-04-10"}

    result = parse_date_expression("2026-04-15")
    # {"start": "2026-04-15", "end": "2026-04-15"}  (already ISO)
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _next_weekday(d: date, weekday: int) -> date:
    """Return the next date with the given weekday (0=Mon, 6=Sun)."""
    days_ahead = weekday - d.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return d + timedelta(days=days_ahead)


def _skip_sunday(d: date) -> date:
    """If d is Sunday, advance to Monday."""
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _last_day_of_month(d: date) -> date:
    """Return the last day of the month containing d."""
    _, last = calendar.monthrange(d.year, d.month)
    return d.replace(day=last)


def _next_month_first(d: date) -> date:
    """Return the 1st of the month after d."""
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


# ---------------------------------------------------------------------------
# Pattern matchers (order matters — most specific first)
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\btoday\b", re.I), "today"),
    (re.compile(r"\btomorrow\b", re.I), "tomorrow"),
    (re.compile(r"\basap\b", re.I), "asap"),
    (re.compile(r"\bearly\s+next\s+week\b", re.I), "early_next_week"),
    (re.compile(r"\blat(?:er?|e)\s+next\s+week\b", re.I), "later_next_week"),
    (re.compile(r"\bnext\s+week\b", re.I), "next_week"),
    (re.compile(r"\bthis\s+week\b", re.I), "this_week"),
    (re.compile(r"\bearly\s+next\s+month\b", re.I), "early_next_month"),
    (re.compile(r"\blat(?:er?|e)\s+next\s+month\b", re.I), "late_next_month"),
    (re.compile(r"\bnext\s+month\b", re.I), "next_month"),
    (re.compile(r"\bthis\s+month\b", re.I), "this_month"),
    # Named weekdays — "next Monday", "this Friday", etc.
    (re.compile(r"\bnext\s+monday\b", re.I), "next_monday"),
    (re.compile(r"\bnext\s+tuesday\b", re.I), "next_tuesday"),
    (re.compile(r"\bnext\s+wednesday\b", re.I), "next_wednesday"),
    (re.compile(r"\bnext\s+thursday\b", re.I), "next_thursday"),
    (re.compile(r"\bnext\s+friday\b", re.I), "next_friday"),
    (re.compile(r"\bnext\s+saturday\b", re.I), "next_saturday"),
]

# Map weekday names to weekday numbers for named-day resolution
_WEEKDAY_MAP = {
    "next_monday": 0, "next_tuesday": 1, "next_wednesday": 2,
    "next_thursday": 3, "next_friday": 4, "next_saturday": 5,
}


def _resolve(tag: str, ref: date) -> tuple[date, date]:
    """Map a matched tag to a (start, end) date range."""

    if tag == "today":
        d = _skip_sunday(ref)
        return (d, d)

    if tag == "tomorrow":
        d = _skip_sunday(ref + timedelta(days=1))
        return (d, d)

    if tag == "asap":
        # Today or next business day
        d = _skip_sunday(ref)
        end = d + timedelta(days=2)  # give a 3-day window
        return (d, _skip_sunday(end))

    if tag == "this_week":
        # Remaining business days this week (Mon-Sat), skip Sunday
        start = _skip_sunday(ref)
        # Saturday of this week
        days_to_sat = 5 - ref.weekday()
        if days_to_sat <= 0:
            # Already Saturday or Sunday — return next Mon-Sat
            start = _next_weekday(ref, 0)  # next Monday
            end = start + timedelta(days=5)
        else:
            end = ref + timedelta(days=days_to_sat)
        return (start, end)

    if tag == "next_week":
        monday = _next_weekday(ref, 0)  # next Monday
        friday = monday + timedelta(days=4)
        return (monday, friday)

    if tag == "early_next_week":
        monday = _next_weekday(ref, 0)
        tuesday = monday + timedelta(days=1)
        return (monday, tuesday)

    if tag == "later_next_week":
        monday = _next_weekday(ref, 0)
        thursday = monday + timedelta(days=3)
        friday = monday + timedelta(days=4)
        return (thursday, friday)

    if tag == "this_month":
        start = _skip_sunday(ref)
        end = _last_day_of_month(ref)
        return (start, end)

    if tag == "next_month":
        first = _next_month_first(ref)
        last = _last_day_of_month(first)
        return (first, last)

    if tag == "early_next_month":
        first = _next_month_first(ref)
        tenth = first.replace(day=10)
        return (first, tenth)

    if tag == "late_next_month":
        first = _next_month_first(ref)
        last = _last_day_of_month(first)
        twentieth = first.replace(day=20)
        return (twentieth, last)

    # Named weekdays — "next Monday" through "next Saturday"
    if tag in _WEEKDAY_MAP:
        d = _next_weekday(ref, _WEEKDAY_MAP[tag])
        return (d, d)

    # Fallback — shouldn't reach here
    return (ref, ref)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_date_expression(
    text: str,
    reference_date: date | None = None,
) -> dict[str, str] | None:
    """Parse a natural-language date expression into an ISO date range.

    Parameters
    ----------
    text:
        The date string to parse.  Can be an ISO date (``"2026-04-15"``),
        a natural-language expression (``"next week"``), or free text
        containing a date expression.
    reference_date:
        The reference point for relative expressions.  Defaults to today.

    Returns
    -------
    dict:
        ``{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}`` on success.
    None:
        If the text cannot be parsed as any known date expression.
    """
    if reference_date is None:
        reference_date = date.today()

    stripped = text.strip()

    # --- Try ISO parse first (most common path from Gemini) ---------------
    try:
        parsed = date.fromisoformat(stripped)
        return {"start": parsed.isoformat(), "end": parsed.isoformat()}
    except (ValueError, TypeError):
        pass

    # --- Try natural-language patterns ------------------------------------
    lower = stripped.lower()
    for pattern, tag in _PATTERNS:
        if pattern.search(lower):
            start, end = _resolve(tag, reference_date)
            return {"start": start.isoformat(), "end": end.isoformat()}

    # --- No match ---------------------------------------------------------
    return None
