"""Tests for social share link construction and the anonymous report call."""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custodian import share  # noqa: E402


def test_twitter_share_url_includes_amount_and_repo() -> None:
    url = share.twitter_share_url("1.1G")
    assert url.startswith("https://twitter.com/intent/tweet?")
    assert "1.1G" in url
    assert "ibrews%2Funreal-custodian" in url or "github.com" in url


def test_linkedin_share_url_points_at_the_repo() -> None:
    url = share.linkedin_share_url()
    assert url.startswith("https://www.linkedin.com/sharing/share-offsite/?")
    assert "unreal-custodian" in url


def test_report_anonymously_is_a_noop_for_zero_or_negative(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: calls.append(1) or None
    )
    assert share.report_anonymously(0) is False
    assert share.report_anonymously(-100) is False
    assert calls == []  # never even attempted the network call


def test_report_anonymously_returns_true_on_2xx(monkeypatch) -> None:
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResponse())
    assert share.report_anonymously(1024) is True


def test_report_anonymously_fails_closed_on_network_error(monkeypatch) -> None:
    def raise_it(*a, **k):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr("urllib.request.urlopen", raise_it)
    assert share.report_anonymously(1024) is False


def test_report_anonymously_includes_project_count_in_the_payload(monkeypatch) -> None:
    """Real gap this closes: 'reported projects' on the public tally used to
    always mean +1 per API call, so a single batch clean of 12 projects
    showed up as 1 report, not 12."""
    captured = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, *a, **k):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    share.report_anonymously(36_507_222_016, project_count=12)
    assert captured["body"] == {"bytes": 36_507_222_016, "projectCount": 12}


def test_report_anonymously_defaults_project_count_to_one(monkeypatch) -> None:
    """A caller that doesn't track project count (or the default arg) still
    sends an honest, explicit projectCount rather than omitting it."""
    captured = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, *a, **k):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    share.report_anonymously(1024)
    assert captured["body"]["projectCount"] == 1


def test_report_anonymously_never_raises_past_its_boundary(monkeypatch) -> None:
    """A flaky network reporting a household disk-cleanup stat must never
    interrupt or crash the caller's actual work."""
    def raise_it(*a, **k):
        raise OSError("dns failure")

    monkeypatch.setattr("urllib.request.urlopen", raise_it)
    result = share.report_anonymously(1024)  # must not raise
    assert result is False


class _FakeGetResponse:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_public_totals_parses_a_good_response(monkeypatch) -> None:
    body = b'{"ok": true, "totalBytes": 555, "totalReports": 3}'
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeGetResponse(200, body))
    assert share.fetch_public_totals() == {"total_bytes": 555, "total_reports": 3}


def test_fetch_public_totals_returns_none_on_network_error(monkeypatch) -> None:
    def raise_it(*a, **k):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr("urllib.request.urlopen", raise_it)
    assert share.fetch_public_totals() is None  # must not raise, no internet is expected


def test_fetch_public_totals_returns_none_on_non_2xx(monkeypatch) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: _FakeGetResponse(500, b"{}")
    )
    assert share.fetch_public_totals() is None


def test_fetch_public_totals_returns_none_on_malformed_body(monkeypatch) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: _FakeGetResponse(200, b'{"ok": true, "totalBytes": "not a number"}'),
    )
    assert share.fetch_public_totals() is None


def test_fetch_public_totals_never_raises_past_its_boundary(monkeypatch) -> None:
    def raise_it(*a, **k):
        raise OSError("dns failure")

    monkeypatch.setattr("urllib.request.urlopen", raise_it)
    assert share.fetch_public_totals() is None  # must not raise


@pytest.mark.parametrize(
    "candidate,current,expected",
    [
        ("0.3.0", "0.2.0", True),
        ("0.2.0", "0.3.0", False),
        ("0.3.0", "0.3.0", False),
        ("0.10.0", "0.9.0", True),  # numeric, not lexicographic ("0.10" < "0.9" as strings)
        ("1.0.0", "0.99.99", True),
        ("0.3.0-beta", "0.2.0", True),  # non-numeric suffix ignored, "0.3.0" prefix still compares
    ],
)
def test_is_newer_version(candidate, current, expected) -> None:
    assert share.is_newer_version(candidate, current) is expected


def test_fetch_latest_release_parses_a_good_response(monkeypatch) -> None:
    body = json.dumps(
        {"tag_name": "v0.3.0", "html_url": "https://github.com/ibrews/unreal-custodian/releases/tag/v0.3.0"}
    ).encode("utf-8")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeGetResponse(200, body))
    assert share.fetch_latest_release() == {
        "version": "0.3.0",
        "url": "https://github.com/ibrews/unreal-custodian/releases/tag/v0.3.0",
    }


def test_fetch_latest_release_sends_a_user_agent(monkeypatch) -> None:
    """GitHub's REST API 403s anonymous requests with no User-Agent header."""
    captured = {}

    def fake_urlopen(req, *a, **k):
        captured["headers"] = req.headers
        body = json.dumps({"tag_name": "v0.3.0", "html_url": "https://x"}).encode("utf-8")
        return _FakeGetResponse(200, body)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    share.fetch_latest_release()
    assert "User-agent" in captured["headers"]  # urllib.request title-cases header keys


def test_fetch_latest_release_returns_none_on_network_error(monkeypatch) -> None:
    def raise_it(*a, **k):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr("urllib.request.urlopen", raise_it)
    assert share.fetch_latest_release() is None


def test_fetch_latest_release_returns_none_on_malformed_body(monkeypatch) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: _FakeGetResponse(200, b'{"tag_name": 12345}'),
    )
    assert share.fetch_latest_release() is None


def test_fetch_latest_release_never_raises_past_its_boundary(monkeypatch) -> None:
    def raise_it(*a, **k):
        raise OSError("dns failure")

    monkeypatch.setattr("urllib.request.urlopen", raise_it)
    assert share.fetch_latest_release() is None  # must not raise
