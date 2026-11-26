"""Tests for alert fetching: Gmail label, not the read flag, marks an alert as processed."""

import pytest

from src import gmail_client

RAW = (
    b"From: Reality <alert@reality.idnes.cz>\r\n"
    b"Subject: =?UTF-8?B?SGzDrWRhY8OtIHBlcw==?=\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n\r\n"
    b"<p>hi</p>"
)


class FakeImap:
    """Records IMAP calls; SEARCH returns the UIDs given per sender."""

    def __init__(self, hits):
        self.hits, self.calls = hits, []

    def login(self, *_):
        pass

    def select(self, *_):
        return "OK", [b"1"]

    def create(self, name):
        self.calls.append(("CREATE", name))
        return "NO", [b"exists"]

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if command == "SEARCH":
            sender = next(s for s in self.hits if s in args[-1])
            return "OK", [b" ".join(self.hits[sender])]
        if command == "FETCH":
            return "OK", [(b"1 (UID 7 BODY[] {10}", RAW), b")"]
        return "OK", [b""]

    def logout(self):
        pass


@pytest.fixture
def fake(monkeypatch):
    def install(hits):
        conn = FakeImap(hits)
        monkeypatch.setattr(gmail_client.imaplib, "IMAP4_SSL", lambda *_: conn)
        return conn
    return install


def test_search_skips_processed_label_not_unread(fake):
    conn = fake({"reality.idnes.cz": [b"7"]})
    gmail_client.fetch_new_alerts("a", "p", ["reality.idnes.cz"], since_days=7)
    search = next(c for c in conn.calls if c[0] == "SEARCH")
    assert search[1] == "X-GM-RAW"
    assert "-label:processed" in search[2] and "newer_than:7d" in search[2]
    assert "UNSEEN" not in str(conn.calls)


def test_fetch_peeks_so_read_flag_is_untouched(fake):
    conn = fake({"reality.idnes.cz": [b"7"]})
    gmail_client.fetch_new_alerts("a", "p", ["reality.idnes.cz"])
    fetch = next(c for c in conn.calls if c[0] == "FETCH")
    assert "BODY.PEEK[]" in fetch[2]


def test_one_record_decoded(fake):
    fake({"reality.idnes.cz": [b"7"]})
    [msg] = gmail_client.fetch_new_alerts("a", "p", ["reality.idnes.cz"])
    assert msg["uid"] == "7"
    assert msg["subject"] == "Hlídací pes"
    assert msg["html_body"] == "<p>hi</p>"


def test_empty_search(fake):
    fake({"reality.idnes.cz": []})
    assert gmail_client.fetch_new_alerts("a", "p", ["reality.idnes.cz"]) == []


def test_max_results_caps_overflow(fake):
    conn = fake({"reality.idnes.cz": [str(i).encode() for i in range(60)]})
    msgs = gmail_client.fetch_new_alerts("a", "p", ["reality.idnes.cz"], max_results=50)
    assert len(msgs) == 50
    assert sum(c[0] == "FETCH" for c in conn.calls) == 50


def test_label_processed_adds_gmail_label(fake):
    conn = fake({})
    gmail_client.label_processed("a", "p", ["7", "9"])
    stores = [c for c in conn.calls if c[0] == "STORE"]
    assert stores == [
        ("STORE", "7", "+X-GM-LABELS", "(processed)"),
        ("STORE", "9", "+X-GM-LABELS", "(processed)"),
    ]
    assert ("CREATE", "processed") in conn.calls


def test_label_processed_empty_is_noop(fake):
    conn = fake({})
    gmail_client.label_processed("a", "p", [])
    assert conn.calls == []


def test_max_results_keeps_newest(fake):
    conn = fake({"reality.idnes.cz": [str(i).encode() for i in range(60)]})
    gmail_client.fetch_new_alerts("a", "p", ["reality.idnes.cz"], max_results=50)
    fetched = [c[1] for c in conn.calls if c[0] == "FETCH"]
    assert fetched[0] == b"10" and fetched[-1] == b"59"


def test_label_failure_is_logged(fake, caplog):
    conn = fake({})
    conn.uid = lambda command, *args: ("NO", [b"denied"])
    gmail_client.label_processed("a", "p", ["7"])
    assert "Label failed for uid 7" in caplog.text
