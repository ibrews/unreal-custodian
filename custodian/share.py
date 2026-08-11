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


def report_anonymously(bytes_reclaimed: int, timeout: float = 5.0) -> bool:
    """POST an anonymous byte count to the public tally.

    Returns whether it succeeded. No identifying information is sent or
    logged -- just the number. Callers should treat a False return as
    "quietly skip it," never as an error worth surfacing to the user;
    reporting a household disk-cleanup stat is not worth interrupting
    anyone's day over a flaky network.
    """
    if bytes_reclaimed <= 0:
        return False
    try:
        payload = json.dumps({"bytes": bytes_reclaimed}).encode("utf-8")
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
