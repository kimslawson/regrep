"""Provider registry.

Future providers (Memento TimeTravel aggregator, self-hosted pywb, ...)
register here; the CLI's --provider choices are generated from this mapping.
"""

from .archivetoday import ArchiveTodayProvider
from .base import (
    FetchResult,
    FetchStatus,
    ProviderError,
    Snapshot,
    SnapshotProvider,
)
from .memento import MementoTimeMapProvider
from .wayback import WaybackProvider

PROVIDERS: dict[str, type[SnapshotProvider]] = {
    WaybackProvider.name: WaybackProvider,
    ArchiveTodayProvider.name: ArchiveTodayProvider,
}

DEFAULT_PROVIDER = WaybackProvider.name


def get_provider(name: str) -> SnapshotProvider:
    try:
        return PROVIDERS[name]()
    except KeyError:
        raise ProviderError(
            f"unknown provider {name!r} (available: {', '.join(sorted(PROVIDERS))})"
        ) from None


__all__ = [
    "DEFAULT_PROVIDER",
    "PROVIDERS",
    "ArchiveTodayProvider",
    "FetchResult",
    "FetchStatus",
    "MementoTimeMapProvider",
    "ProviderError",
    "Snapshot",
    "SnapshotProvider",
    "WaybackProvider",
    "get_provider",
]
