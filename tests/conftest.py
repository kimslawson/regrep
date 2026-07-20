import json

import pytest

from regrep.providers.base import Snapshot

CDX_URL = "https://web.archive.org/cdx/search/cdx"
WEB_URL = "https://web.archive.org/web"
CDX_HEADER = ["timestamp", "original", "mimetype", "statuscode", "digest"]


def cdx_payload(rows: list[list[str]]) -> str:
    return json.dumps([CDX_HEADER, *rows])


def raw_url(timestamp: str, original: str) -> str:
    return f"{WEB_URL}/{timestamp}id_/{original}"


def make_snapshot(
    timestamp: str,
    original: str = "http://example.com/page",
    digest: str | None = None,
    mimetype: str | None = "text/html",
    provider: str = "fake",
) -> Snapshot:
    return Snapshot(
        provider=provider,
        timestamp=timestamp,
        original_url=original,
        raw_url=raw_url(timestamp, original),
        replay_url=f"{WEB_URL}/{timestamp}/{original}",
        digest=digest,
        mimetype=mimetype,
        status="200",
    )


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    """Keep host environment from bleeding into URL and color decisions."""
    for name in ("REGREP_WAYBACK_CDX_URL", "REGREP_WAYBACK_WEB_URL", "NO_COLOR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
