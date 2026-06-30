from datetime import date

from riji_agent.retrieval.models import Granularity
from riji_agent.retrieval.periods import enumerate_period_labels, period_label


def test_period_label_per_granularity() -> None:
    d = date(2026, 6, 24)
    assert period_label(d, Granularity.DAY) == "2026-06-24"
    assert period_label(d, Granularity.WEEK) == "2026-W26"
    assert period_label(d, Granularity.MONTH) == "2026-06"


def test_labels_are_zero_padded_for_lexical_ordering() -> None:
    assert period_label(date(2026, 1, 5), Granularity.MONTH) == "2026-01"
    assert period_label(date(2026, 1, 5), Granularity.WEEK) == "2026-W02"


def test_enumerate_month_labels_covers_range_without_duplicates() -> None:
    labels = enumerate_period_labels(date(2026, 1, 15), date(2026, 4, 2), Granularity.MONTH)
    assert labels == ["2026-01", "2026-02", "2026-03", "2026-04"]


def test_enumerate_day_labels_are_ordered() -> None:
    labels = enumerate_period_labels(date(2026, 6, 24), date(2026, 6, 26), Granularity.DAY)
    assert labels == ["2026-06-24", "2026-06-25", "2026-06-26"]
