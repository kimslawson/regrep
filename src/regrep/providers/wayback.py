"""Internet Archive Wayback Machine provider (CDX index + raw snapshot fetch)."""

import os
import threading

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .. import __version__
from .base import (
    DEFAULT_MAX_BYTES,
    DEFAULT_TIMEOUT,
    FetchResult,
    FetchStatus,
    ProviderError,
    Snapshot,
    SnapshotProvider,
    decode_bytes,
    looks_binary,
    mimetype_may_be_text,
)

USER_AGENT = f"regrep/{__version__} (+https://github.com/kimslawson/regrep)"

# Overridable so tests and self-hosted CDX-compatible archives (e.g. pywb)
# can stand in for archive.org.
DEFAULT_CDX_URL = "https://web.archive.org/cdx/search/cdx"
DEFAULT_WEB_URL = "https://web.archive.org/web"

_CDX_FIELDS = "timestamp,original,mimetype,statuscode,digest"


class WaybackProvider(SnapshotProvider):
    name = "wayback"

    def __init__(self, cdx_url: str | None = None, web_url: str | None = None):
        self.cdx_url = cdx_url or os.environ.get("REGREP_WAYBACK_CDX_URL", DEFAULT_CDX_URL)
        self.web_url = (
            web_url or os.environ.get("REGREP_WAYBACK_WEB_URL", DEFAULT_WEB_URL)
        ).rstrip("/")
        self._local = threading.local()

    def _session(self) -> requests.Session:
        # requests.Session is not guaranteed thread-safe; keep one per worker
        # thread, all sharing the same retry/pooling configuration.
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            retry = Retry(
                total=3,
                connect=3,
                read=2,
                backoff_factor=1.5,
                status_forcelist=(429, 502, 503, 504),
                allowed_methods=("GET", "HEAD"),
                respect_retry_after_header=True,
            )
            adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            session.headers["User-Agent"] = USER_AGENT
            self._local.session = session
        return session

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
        if not mimetype_may_be_text(snapshot.mimetype):
            return FetchResult(FetchStatus.NON_TEXT, detail=snapshot.mimetype)
        try:
            resp = self._session().get(snapshot.raw_url, timeout=(10, timeout), stream=True)
        except requests.RequestException as exc:
            return FetchResult(FetchStatus.ERROR, detail=str(exc))

        with resp:
            if resp.status_code != 200:
                return FetchResult(FetchStatus.ERROR, detail=f"HTTP {resp.status_code}")
            length = resp.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > max_bytes:
                return FetchResult(FetchStatus.TOO_LARGE, detail=f"{length} bytes")
            chunks: list[bytes] = []
            size = 0
            try:
                for chunk in resp.iter_content(chunk_size=65536):
                    size += len(chunk)
                    if size > max_bytes:
                        return FetchResult(FetchStatus.TOO_LARGE, detail=f">{max_bytes} bytes")
                    chunks.append(chunk)
            except requests.RequestException as exc:
                return FetchResult(FetchStatus.ERROR, detail=str(exc))
            content_type = resp.headers.get("Content-Type", "")

        raw = b"".join(chunks)
        if looks_binary(raw):
            return FetchResult(FetchStatus.BINARY)
        # Trust the charset only when one is actually declared; requests
        # otherwise "defaults" text/* to ISO-8859-1, garbling old UTF-8 pages.
        declared = resp.encoding if "charset=" in content_type.lower() else None
        return FetchResult(FetchStatus.OK, text=decode_bytes(raw, declared))
