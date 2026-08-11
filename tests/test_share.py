"""Tests for social share link construction and the anonymous report call."""

from __future__ import annotations

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


def test_report_anonymously_never_raises_past_its_boundary(monkeypatch) -> None:
    """A flaky network reporting a household disk-cleanup stat must never
    interrupt or crash the caller's actual work."""
    def raise_it(*a, **k):
        raise OSError("dns failure")

    monkeypatch.setattr("urllib.request.urlopen", raise_it)
    result = share.report_anonymously(1024)  # must not raise
    assert result is False
