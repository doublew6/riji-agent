"""Pure helpers for bucketing dates into timeline periods.

Labels are zero-padded so lexical ordering equals chronological ordering.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import timedelta
from typing import List

from riji_agent.retrieval.models import Granularity


def period_label(value: Date, granularity: Granularity) -> str:
    if granularity is Granularity.DAY:
        return value.isoformat()
    if granularity is Granularity.WEEK:
        iso_year, iso_week, _ = value.isocalendar()
        return f"{iso_year:04d}-W{iso_week:02d}"
    return f"{value.year:04d}-{value.month:02d}"


def enumerate_period_labels(
    date_from: Date, date_to: Date, granularity: Granularity
) -> List[str]:
    """Ordered, de-duplicated period labels covering the inclusive range."""
    labels: List[str] = []
    seen = set()
    cursor = date_from
    while cursor <= date_to:
        label = period_label(cursor, granularity)
        if label not in seen:
            seen.add(label)
            labels.append(label)
        cursor += timedelta(days=1)
    return labels
