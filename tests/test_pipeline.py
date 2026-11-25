"""Tests for the report window: each listing appears in exactly one Tue/Fri report."""

from datetime import date

from src.pipeline import report_cutoff


def test_tuesday_covers_since_friday():
    assert report_cutoff(date(2026, 12, 1)) == "2026-11-27"  # Tue -> after Fri


def test_friday_covers_since_tuesday():
    assert report_cutoff(date(2026, 12, 4)) == "2026-12-01"  # Fri -> after Tue


def test_manual_run_other_day_uses_three_days():
    assert report_cutoff(date(2026, 12, 3)) == "2026-11-30"


class TestLastReportState:
    def test_round_trip(self, tmp_path):
        from src.pipeline import read_last_report, write_last_report
        p = tmp_path / "last_report.txt"
        write_last_report(p, date(2026, 11, 25))
        assert read_last_report(p) == "2026-11-25"

    def test_missing_file(self, tmp_path):
        from src.pipeline import read_last_report
        assert read_last_report(tmp_path / "nope.txt") is None

    def test_empty_file(self, tmp_path):
        from src.pipeline import read_last_report
        p = tmp_path / "e.txt"
        p.write_text("")
        assert read_last_report(p) is None

    def test_garbage_file(self, tmp_path):
        from src.pipeline import read_last_report
        p = tmp_path / "g.txt"
        p.write_text("<html>oops</html>")
        assert read_last_report(p) is None


class TestReportSince:
    def test_uses_last_report_when_known(self):
        from src.pipeline import report_since
        assert report_since("2026-11-25", date(2026, 11, 27)) == "2026-11-25"

    def test_falls_back_to_window(self):
        from src.pipeline import report_since
        assert report_since(None, date(2026, 12, 4)) == "2026-12-01"

    def test_future_state_falls_back(self):
        from src.pipeline import report_since
        assert report_since("2027-01-01", date(2026, 12, 4)) == "2026-12-01"
