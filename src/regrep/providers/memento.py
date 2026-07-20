"""Memento TimeMap provider (RFC 7089) — the base for archive.today and, later,
the TimeTravel aggregator and other Memento-compliant archives.

A Memento TimeMap is poorer than Wayback's CDX: per capture it gives only a
URL and a datetime — no content digest, no mimetype, and no "original bytes"
endpoint. So compared to the Wayback provider:

* Snapshots carry no digest, so client-side dedupe is a no-op (we cannot know
  two captures are identical without fetching both).
* Fetched content is whatever the archive serves, including any chrome it
  injects — there is no `id_`-style raw endpoint to strip it.
* The TimeMap lists every capture at once, so snapshot selection
  (from/to/limit/cadence) is applied client-side here.
* Prefix search has no TimeMap equivalent and is refused rather than faked.
"""

import re
from datetime import timezone
from email.utils import parsedate_to_datetime

import requests

from .base import (
    DEFAULT_MAX_BYTES,
    DEFAULT_TIMEOUT,
    FetchResult,
    HttpProvider,
    ProviderError,
    Snapshot,
    stream_text,
)

# A TimeMap is a comma-separated list of links, but RFC 1123 datetimes also
# contain commas ("Mon, 01 Apr 2013 ..."). Split only on commas that begin a
# new link — i.e. followed by optional whitespace and the opening "<".
_LINK_SPLIT = re.compile(r",\s*(?=<)")
_URI = re.compile(r"<([^>]*)>")
_PARAM = re.compile(r';\s*([A-Za-z][\w-]*)\s*=\s*"([^"]*)"')


def parse_timemap(text: str) -> tuple[list[tuple[str, str]], str | None]:
    """Parse link-format TimeMap text.

    Returns ``(mementos, original_url)`` where ``mementos`` is a list of
    ``(timestamp, memento_url)`` with 14-digit UTC timestamps, and
    ``original_url`` is the ``rel="original"`` target if present.
    """
    mementos: list[tuple[str, str]] = []
    original: str | None = None
    for chunk in _LINK_SPLIT.split(text.strip()):
        uri_match = _URI.search(chunk)
        if not uri_match:
            continue
        uri = uri_match.group(1)
        params = {key.lower(): value for key, value in _PARAM.findall(chunk)}
        rel = params.get("rel", "").split()
        if "original" in rel:
            original = uri
        if "memento" not in rel:
            continue
        raw_dt = params.get("datetime")
        if not raw_dt:
            continue
        try:
            dt = parsedate_to_datetime(raw_dt)
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        mementos.append((dt.strftime("%Y%m%d%H%M%S"), uri))
    return mementos, original


class MementoTimeMapProvider(HttpProvider):
    """Enumerate captures from a Memento TimeMap endpoint and fetch them."""

    name = "memento"
    timemap_url = ""  # e.g. "https://archive.today/timemap/"; set by subclasses

    def __init__(self, timemap_url: str | None = None):
        super().__init__()
        if timemap_url:
            self.timemap_url = timemap_url
        if not self.timemap_url:
            raise ProviderError(f"{self.name}: no TimeMap endpoint configured")

    def list_snapshots(
        self,
        url: str,
        *,
        from_ts: str | None = None,
        to_ts: str | None = None,
        limit: int | None = None,
        prefix: bool = False,
        collapse: int | None = None,
    ) -> list[Snapshot]:
        if prefix:
            raise ProviderError(
                f"{self.name} does not support --prefix (a TimeMap is per exact URL)"
            )
        endpoint = self.timemap_url.rstrip("/") + "/" + url
        try:
            resp = self._session().get(endpoint, timeout=(10, 60))
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ProviderError(f"{self.name} TimeMap query failed: {exc}") from exc

        mementos, original = parse_timemap(resp.text)
        if not mementos:
            # A real "no captures" answer still looks like link-format; a
            # bot-block/challenge page does not.
            if "rel=" not in resp.text:
                raise ProviderError(
                    f"{self.name} returned no TimeMap for {url} — it may be blocking "
                    "automated access, or have no snapshots of this URL"
                )
            return []

        original = original or url
        mementos.sort(key=lambda m: m[0])
        mementos = self._apply_range(mementos, from_ts, to_ts)
        if collapse:
            mementos = self._collapse(mementos, collapse)
        if limit:
            mementos = mementos[:limit]

        return [
            Snapshot(
                provider=self.name,
                timestamp=ts,
                original_url=original,
                # No raw-bytes endpoint: the memento URL is both what we fetch
                # and what a human opens.
                raw_url=memento_url,
                replay_url=memento_url,
                digest=None,
                mimetype=None,
                status="200",
            )
            for ts, memento_url in mementos
        ]

    def fetch(
        self,
        snapshot: Snapshot,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> FetchResult:
        # No mimetype from the TimeMap, so we can't pre-skip binaries; fetch
        # and let stream_text's NUL-byte check catch them.
        return stream_text(self._session(), snapshot.raw_url, timeout=timeout, max_bytes=max_bytes)

    @staticmethod
    def _apply_range(
        mementos: list[tuple[str, str]], from_ts: str | None, to_ts: str | None
    ) -> list[tuple[str, str]]:
        # Pad partial bounds so a lexical compare means the whole period:
        # from="2023" -> 20230000000000, to="2023" -> 20239999999999.
        low = from_ts.ljust(14, "0") if from_ts else None
        high = to_ts.ljust(14, "9") if to_ts else None
        return [
            (ts, u)
            for ts, u in mementos
            if (low is None or ts >= low) and (high is None or ts <= high)
        ]

    @staticmethod
    def _collapse(mementos: list[tuple[str, str]], digits: int) -> list[tuple[str, str]]:
        seen: set[str] = set()
        kept: list[tuple[str, str]] = []
        for ts, u in mementos:
            key = ts[:digits]
            if key in seen:
                continue
            seen.add(key)
            kept.append((ts, u))
        return kept
