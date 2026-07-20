"""Memento TimeTravel aggregator provider (timetravel.mementoweb.org).

TimeTravel polls many web archives at once and returns a single aggregated
Memento TimeMap, so one regrep run fans out across the Wayback Machine,
archive.today, Archive-It, national and university libraries, and more. It is
"just" a `MementoTimeMapProvider` pointed at the aggregator's link-format
TimeMap — the payoff of having built the Memento base.

Caveats worth knowing:

* An aggregated TimeMap mixes mementos from every source, so fetched content
  is whatever each archive serves. Wayback entries come through as *replay*
  pages (with toolbar chrome), not `id_` raw bytes — use ``--provider
  wayback`` when you want Wayback's pristine original content.
* The aggregator polls archives live, so it is comparatively slow (hence the
  longer index timeout) and can be flaky under load; transient 5xx are
  retried.
* Very large TimeMaps are paginated by the aggregator; regrep reads the first
  page (bounded further by ``--limit``).
"""

import os

from .memento import MementoTimeMapProvider

# The "/link/" segment selects the link-format TimeMap (vs. /json/).
DEFAULT_TIMEMAP_URL = "https://timetravel.mementoweb.org/timemap/link/"


class TimeTravelProvider(MementoTimeMapProvider):
    name = "timetravel"
    # The aggregator queries many archives live; give the index request room.
    timemap_timeout = (10, 120)

    def __init__(self, timemap_url: str | None = None):
        super().__init__(
            timemap_url or os.environ.get("REGREP_TIMETRAVEL_TIMEMAP_URL", DEFAULT_TIMEMAP_URL)
        )
