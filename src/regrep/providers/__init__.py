"""Provider registry.

Future providers (archive.today, Memento TimeTravel, self-hosted pywb, ...)
register here; the CLI's --provider choices are generated from this mapping.
"""

from .base import (
    FetchResult,
    FetchStatus,
    ProviderError,
    Snapshot,
    SnapshotProvider,
)
from .wayback import WaybackProvider

PROVIDERS: dict[str, type[SnapshotProvider]] = {
    WaybackProvider.name: WaybackProvider,
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
    "FetchResult",
    "FetchStatus",
    "ProviderError",
    "Snapshot",
    "SnapshotProvider",
    "WaybackProvider",
    "get_provider",
]
