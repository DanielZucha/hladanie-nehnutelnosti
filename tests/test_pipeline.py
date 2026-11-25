"""Tests for the report window: each listing appears in exactly one Tue/Fri report."""

from datetime import date

from src.pipeline import report_cutoff


def test_tuesday_covers_since_friday():
    assert report_cutoff(date(2026, 12, 1)) == "2026-11-27"  # Tue -> after Fri


def test_friday_covers_since_tuesday():
    assert report_cutoff(date(2026, 12, 4)) == "2026-12-01"  # Fri -> after Tue


def test_manual_run_other_day_uses_three_days():
    assert report_cutoff(date(2026, 12, 3)) == "2026-11-30"
