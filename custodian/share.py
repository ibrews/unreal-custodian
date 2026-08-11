"""Turns a completed clean run into something worth telling someone about:
pre-filled social share links, and an anonymous opt-in report to a public
"total reclaimed across everyone" tally on alexcoulombepresents.com.

Pure stdlib -- no new dependency for one HTTP POST. Network calls here never
raise past this module's boundary: a report failing is "fine, didn't count
this time," never a reason to interrupt or alarm someone who just cleaned
their disk.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

REPORT_URL = "https://www.alexcoulombepresents.com/api/unreal-custodian/space-saved"
REPO_URL = "https://github.com/ibrews/unreal-custodian"
LATEST_RELEASE_API_URL = "https://api.github.com/repos/ibrews/unreal-custodian/releases/latest"


def share_text(human_amount: str) -> str:
    return f"Unreal Custodian just reclaimed {human_amount} of build cache I didn't need."


def twitter_share_url(human_amount: str) -> str:
    params = {"text": share_text(human_amount), "url": REPO_URL}
    return "https://twitter.com/intent/tweet?" + urllib.parse.urlencode(params)


def linkedin_share_url() -> str:
    # LinkedIn's share-offsite endpoint takes a URL, not custom text -- the
    # repo page's own title/description carry the message instead.
    return "https://www.linkedin.com/sharing/share-offsite/?" + urllib.parse.urlencode(
        {"url": REPO_URL}
    )


def fetch_public_totals(timeout: float = 5.0) -> dict | None:
    """GET the current public tally.

    Returns {"total_bytes": int, "total_reports": int}, or None on any
    failure (no internet, server down, unexpected response shape) -- the
    caller's job is to fall back to a locally cached value, not to treat
    this as an error. Called on every GUI launch, so it has to fail fast
    and fail quiet.
    """
    try:
        req = urllib.request.Request(REPORT_URL, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            if not (200 <= resp.status < 300):
                return None
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return None
    total_bytes = data.get("totalBytes")
    total_reports = data.get("totalReports")
    if not isinstance(total_bytes, (int, float)) or not isinstance(total_reports, (int, float)):
        return None
    return {"total_bytes": int(total_bytes), "total_reports": int(total_reports)}


def _parse_version(v: str) -> tuple[int, ...]:
    """"0.3.0" -> (0, 3, 0). Stops at the first non-numeric part rather than
    raising -- a release tag with a suffix ("0.3.0-beta") still compares
    sanely on its numeric prefix instead of blowing up the whole check."""
    parts: list[int] = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            break
    return tuple(parts) if parts else (0,)


def is_newer_version(candidate: str, current: str) -> bool:
    """Numeric tuple comparison, not string comparison -- "0.10.0" must
    sort after "0.9.0", which plain string comparison gets backwards."""
    return _parse_version(candidate) > _parse_version(current)


def fetch_latest_release(timeout: float = 5.0) -> dict | None:
    """GET the latest GitHub release for this repo.

    Returns {"version": "0.3.0", "url": "https://github.com/.../releases/tag/v0.3.0"},
    or None on any failure (no internet, GitHub rate limit, unexpected
    response shape) -- checked once per launch, so like the other network
    calls here it has to fail fast and quiet, never interrupt startup.
    """
    try:
        req = urllib.request.Request(
            LATEST_RELEASE_API_URL,
            # GitHub's REST API 403s anonymous requests with no User-Agent.
            headers={"Accept": "application/vnd.github+json", "User-Agent": "unreal-custodian"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            if not (200 <= resp.status < 300):
                return None
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return None
    tag = data.get("tag_name")
    url = data.get("html_url")
    if not isinstance(tag, str) or not isinstance(url, str):
        return None
    return {"version": tag.lstrip("v"), "url": url}


def report_anonymously(bytes_reclaimed: int, project_count: int = 1, timeout: float = 5.0) -> bool:
    """POST an anonymous byte count (and how many projects/engines it came
    from) to the public tally.

    Returns whether it succeeded. No identifying information is sent or
    logged -- just the numbers. Callers should treat a False return as
    "quietly skip it," never as an error worth surfacing to the user;
    reporting a household disk-cleanup stat is not worth interrupting
    anyone's day over a flaky network.

    project_count defaults to 1 for a caller that doesn't track it --
    still an honest report of one clean run, same as before this existed.
    """
    if bytes_reclaimed <= 0:
        return False
    try:
        payload = json.dumps(
            {"bytes": bytes_reclaimed, "projectCount": max(1, project_count)}
        ).encode("utf-8")
        req = urllib.request.Request(
            REPORT_URL,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False
