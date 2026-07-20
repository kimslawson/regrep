"""Provider abstraction: anything that can enumerate and serve archived snapshots.

A provider knows how to (1) list snapshots of a URL and (2) fetch the raw
archived content of one snapshot. The search engine in `regrep.core` is
provider-agnostic; adding support for a new archive (archive.today, a
national web archive, a self-hosted pywb, the Memento TimeTravel
aggregator, ...) means implementing this interface and registering it in
`regrep.providers.PROVIDERS`.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # refuse to buffer snapshots larger than this
DEFAULT_TIMEOUT = 30.0  # per-snapshot read timeout, seconds

# Content types that cannot meaningfully be grepped as text. Anything not
# matched here is fetched and NUL-byte-checked as a backstop.
_BINARY_PREFIXES = ("image/", "audio/", "video/", "font/", "model/")
_BINARY_TYPES = {
    "application/pdf",
    "application/zip",
    "application/gzip",
    "application/x-gzip",
    "application/octet-stream",
    "application/x-shockwave-flash",
    "application/java-archive",
    "application/x-msdownload",
}


class ProviderError(RuntimeError):
    """A provider-level failure (index query failed, malformed response, ...)."""


class FetchStatus(Enum):
    OK = "ok"
    ERROR = "error"
    BINARY = "binary"
    NON_TEXT = "non-text"
    TOO_LARGE = "too-large"


@dataclass(frozen=True)
class FetchResult:
    status: FetchStatus
    text: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class Snapshot:
    """One archived capture of one URL, in provider-neutral terms."""

    provider: str
    timestamp: str  # provider-native, lexically sortable (Wayback: YYYYMMDDhhmmss)
    original_url: str  # the URL as archived (may differ from what the user typed)
    raw_url: str  # unmodified original bytes, no archive chrome injected
    replay_url: str  # human-friendly view with the archive's navigation UI
    digest: str | None = None  # content hash, used to skip identical captures
    mimetype: str | None = None
    status: str | None = None

    @property
    def dt(self) -> datetime | None:
        try:
            return datetime.strptime(self.timestamp[:14], "%Y%m%d%H%M%S")
        except ValueError:
            return None

    @property
    def date_display(self) -> str:
        dt = self.dt
        return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else self.timestamp


def mimetype_may_be_text(mimetype: str | None) -> bool:
    """False only for types we are confident cannot be grepped as text."""
    if not mimetype or mimetype in ("-", "unk"):
        return True
    mt = mimetype.split(";")[0].strip().lower()
    return not (mt.startswith(_BINARY_PREFIXES) or mt in _BINARY_TYPES)


def looks_binary(raw: bytes) -> bool:
    return b"\x00" in raw[:8192]


def decode_bytes(raw: bytes, declared_encoding: str | None = None) -> str:
    """Decode archived bytes, preferring a declared charset, then detection.

    Old pages routinely omit or lie about their charset; `errors="replace"`
    keeps a bad byte from killing a whole snapshot.
    """
    if declared_encoding:
        try:
            return raw.decode(declared_encoding, errors="replace")
        except LookupError:
            pass
    try:
        from charset_normalizer import from_bytes  # ships with requests

        best = from_bytes(raw[:131072]).best()
        if best and best.encoding:
            return raw.decode(best.encoding, errors="replace")
    except Exception:  # detection is best-effort, never fatal
        pass
    return raw.decode("utf-8", errors="replace")


class SnapshotProvider(ABC):
    """Interface every archive backend implements."""

    name: str = "abstract"

    @abstractmethod
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
        """Enumerate snapshots of `url`, oldest first.

        `from_ts`/`to_ts` are digit-string timestamps (YYYY[MM[DD[hhmmss]]]).
        `prefix=True` widens the match to everything under the URL as a path
        prefix. `collapse` asks for at most one snapshot per period, expressed
        as the number of leading timestamp digits that must be unique (8 =
        daily, 6 = monthly, 4 = yearly); providers without an equivalent
        feature may ignore it.
        """

    @abstractmethod
    def fetch(
        self,
        snapshot: Snapshot,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> FetchResult:
        """Retrieve the raw archived content of one snapshot as text."""
