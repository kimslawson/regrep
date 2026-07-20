"""archive.today provider (archive.ph / archive.is / archive.li / ...).

A thin Memento TimeMap provider pointed at archive.today. archive.today is
Memento-compliant, so all the real work lives in `MementoTimeMapProvider`;
this only pins the endpoint (overridable, since archive.today rotates through
mirror domains and can be flaky).

Caveats worth knowing: archive.today has no content digests, no raw-bytes
endpoint (fetched pages include its wrapper), and is aggressively hostile to
automated access (Cloudflare, occasional CAPTCHAs) — expect the odd blocked
request, which regrep reports rather than hides.
"""

import os

from .memento import MementoTimeMapProvider

DEFAULT_TIMEMAP_URL = "https://archive.today/timemap/"


class ArchiveTodayProvider(MementoTimeMapProvider):
    name = "archivetoday"

    def __init__(self, timemap_url: str | None = None):
        super().__init__(
            timemap_url
            or os.environ.get("REGREP_ARCHIVETODAY_TIMEMAP_URL", DEFAULT_TIMEMAP_URL)
        )
