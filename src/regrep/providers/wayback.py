"""Internet Archive Wayback Machine provider (CDX index + raw snapshot fetch)."""

import os

import requests

from .base import (
    DEFAULT_MAX_BYTES,
    DEFAULT_TIMEOUT,
    FetchResult,
    FetchStatus,
    HttpProvider,
    ProviderError,
    Snapshot,
    mimetype_may_be_text,
    stream_text,
)

# Overridable so tests and self-hosted CDX-compatible archives (e.g. pywb)
# can stand in for archive.org.
DEFAULT_CDX_URL = "https://web.archive.org/cdx/search/cdx"
DEFAULT_WEB_URL = "https://web.archive.org/web"

_CDX_FIELDS = "timestamp,original,mimetype,statuscode,digest"


class WaybackProvider(HttpProvider):
    name = "wayback"

    def __init__(self, cdx_url: str | None = None, web_url: str | None = None):
        super().__init__()
        self.cdx_url = cdx_url or os.environ.get("REGREP_WAYBACK_CDX_URL", DEFAULT_CDX_URL)
        self.web_url = (
            web_url or os.environ.get("REGREP_WAYBACK_WEB_URL", DEFAULT_WEB_URL)
        ).rstrip("/")

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
        params: dict = {
            "url": url,
            "output": "json",
            "fl": _CDX_FIELDS,
            # Only successful captures; this also drops "revisit" records,
            # whose content lives in an earlier capture anyway.
            "filter": "statuscode:200",
            # Adjacent captures with identical content are pure duplicate
            # work; regrep also dedupes non-adjacent digests client-side.
            "collapse": ["digest"],
        }
        if collapse:
            params["collapse"].append(f"timestamp:{collapse}")
        if from_ts:
            params["from"] = from_ts
        if to_ts:
            params["to"] = to_ts
        if limit:
            params["limit"] = str(limit)
        if prefix:
            params["matchType"] = "prefix"

        try:
            resp = self._session().get(self.cdx_url, params=params, timeout=(10, 60))
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ProviderError(f"Wayback CDX query failed: {exc}") from exc

        if not resp.text.strip():
            return []
        try:
            rows = resp.json()
        except ValueError as exc:
            raise ProviderError(
                f"Wayback CDX returned an unexpected payload: {resp.text[:200]!r}"
            ) from exc
        if not isinstance(rows, list) or len(rows) < 2:
            return []

        header = rows[0]
        try:
            idx = {name: header.index(name) for name in ("timestamp", "original")}
        except (ValueError, AttributeError) as exc:
            raise ProviderError(f"Wayback CDX header missing expected fields: {header!r}") from exc
        opt = {name: header.index(name) for name in ("mimetype", "statuscode", "digest")
               if name in header}

        snapshots = []
        for row in rows[1:]:
            if len(row) <= max(idx.values()):
                continue
            ts, original = row[idx["timestamp"]], row[idx["original"]]

            def field(name: str, row=row) -> str | None:
                i = opt.get(name)
                return row[i] if i is not None and i < len(row) else None

            snapshots.append(
                Snapshot(
                    provider=self.name,
                    timestamp=ts,
                    original_url=original,
                    # `id_` asks Wayback for the original bytes, without the
                    # injected toolbar/rewriting that would pollute matches.
                    raw_url=f"{self.web_url}/{ts}id_/{original}",
                    replay_url=f"{self.web_url}/{ts}/{original}",
                    digest=field("digest"),
                    mimetype=field("mimetype"),
                    status=field("statuscode"),
                )
            )
        return snapshots

    def fetch(
        self,
        snapshot: Snapshot,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> FetchResult:
        # CDX gives us the mimetype up front, so skip known-binary captures
        # without spending a request on them.
        if not mimetype_may_be_text(snapshot.mimetype):
            return FetchResult(FetchStatus.NON_TEXT, detail=snapshot.mimetype)
        return stream_text(self._session(), snapshot.raw_url, timeout=timeout, max_bytes=max_bytes)
