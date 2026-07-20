import responses

from regrep.providers import PROVIDERS, get_provider
from regrep.providers.archivetoday import ArchiveTodayProvider
from test_memento import URL, link_format


def test_archivetoday_is_registered():
    assert "archivetoday" in PROVIDERS
    assert get_provider("archivetoday").name == "archivetoday"


def test_default_endpoint_is_archive_today():
    assert ArchiveTodayProvider().timemap_url.startswith("https://archive.today/timemap")


def test_env_var_overrides_endpoint(monkeypatch):
    monkeypatch.setenv("REGREP_ARCHIVETODAY_TIMEMAP_URL", "https://archive.ph/timemap/")
    assert ArchiveTodayProvider().timemap_url == "https://archive.ph/timemap/"


@responses.activate
def test_end_to_end_list(monkeypatch):
    monkeypatch.setenv("REGREP_ARCHIVETODAY_TIMEMAP_URL", "https://at.test/timemap/")
    endpoint = "https://at.test/timemap/" + URL
    responses.get(
        endpoint,
        body=link_format(
            URL,
            [
                ("Mon, 01 Apr 2013 00:00:00 GMT", "https://archive.ph/aaaa/" + URL),
                ("Wed, 20 Jul 2022 10:00:00 GMT", "https://archive.ph/bbbb/" + URL),
            ],
        ),
    )
    snaps = ArchiveTodayProvider().list_snapshots(URL)
    assert [s.timestamp for s in snaps] == ["20130401000000", "20220720100000"]
    # The provider name propagates to snapshots (drives JSON output, headers).
    assert all(s.provider == "archivetoday" for s in snaps)
    assert snaps[0].raw_url == "https://archive.ph/aaaa/" + URL
