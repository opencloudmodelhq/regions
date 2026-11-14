# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Source registry, download and hash manifest."""

import csv
import hashlib
import os
import re
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from os import PathLike
from pathlib import Path
from typing import Final, TypeAlias, TypedDict, cast

from ocm_regions.fetch import robots

StrPath: TypeAlias = str | PathLike[str]

FROM_VARIABLE: Final = "OCM_REGIONS_FROM"
GZIP_MAGIC: Final = b"\x1f\x8b"
MAX_UNZIPPED: Final = 300 * 1024 * 1024
REQUIRE_PROXY_VARIABLE: Final = "OCM_REGIONS_REQUIRE_PROXY"
RETRY_CODES: Final = (429, 503)
USER_AGENT: Final = "ocm-regions-harness/0.1"
USER_AGENT_VARIABLE: Final = "OCM_REGIONS_USER_AGENT"
_FEED_TERMS = "Vendor IP feed under vendor terms; not redistributed, re-downloaded by the harness"
_VENDOR_TERMS = "Vendor page under vendor terms; not redistributed, re-downloaded by the harness"


@dataclass(frozen=True)
class Source:
    """An outside dataset: its name, URL, path under data/raw and licence note."""

    name: str
    url: str
    path: str
    license_note: str
    page_regex: str = ""


SOURCES: Final[list[Source]] = [
    Source(
        "aws",
        "https://docs.aws.amazon.com/global-infrastructure/latest/regions/aws-regions.html",
        "cloud-regions/aws/regions.html",
        _VENDOR_TERMS,
    ),
    Source(
        "aws-partitions",
        "https://raw.githubusercontent.com/boto/botocore/develop/botocore/data/endpoints.json",
        "cloud-regions/aws/botocore-endpoints.json",
        "botocore, Apache-2.0",
    ),
    Source(
        "azure",
        "https://learn.microsoft.com/en-us/azure/reliability/regions-list",
        "cloud-regions/azure/regions-list.html",
        _VENDOR_TERMS,
    ),
    Source(
        "gcp",
        "https://docs.cloud.google.com/compute/docs/regions-zones",
        "cloud-regions/gcp/regions-zones.html",
        _VENDOR_TERMS,
    ),
    Source(
        "alibaba",
        "https://www.alibabacloud.com/help/en/cloud-migration-guide-for-beginners/latest/regions-and-zones",
        "cloud-regions/alibaba/regions-zones.html",
        _VENDOR_TERMS,
    ),
    Source(
        "oracle",
        "https://docs.oracle.com/en-us/iaas/Content/General/Concepts/regions.htm",
        "cloud-regions/oracle/regions.html",
        _VENDOR_TERMS,
    ),
    Source(
        "hetzner",
        "https://docs.hetzner.com/cloud/general/locations/",
        "cloud-regions/hetzner/locations.html",
        _VENDOR_TERMS,
    ),
    Source("vultr", "https://api.vultr.com/v2/regions", "cloud-regions/vultr/regions.json", _VENDOR_TERMS),
    Source(
        "digitalocean",
        "https://docs.digitalocean.com/platform/regional-availability/",
        "cloud-regions/digitalocean/regional-availability.html",
        _VENDOR_TERMS,
    ),
    Source(
        "tencent",
        "https://intl.cloud.tencent.com/document/product/213/6091",
        "cloud-regions/tencent/regions.html",
        _VENDOR_TERMS,
    ),
    Source(
        "ovhcloud",
        "https://www.ovhcloud.com/en/public-cloud/regions-availability/",
        "cloud-regions/ovhcloud/regions.html",
        _VENDOR_TERMS,
    ),
    Source(
        "ibm",
        "https://cloud.ibm.com/docs/overview?topic=overview-locations",
        "cloud-regions/ibm/locations.html",
        _VENDOR_TERMS,
    ),
    Source("akamai", "https://api.linode.com/v4/regions", "cloud-regions/akamai/regions.json", _VENDOR_TERMS),
    Source(
        "huawei",
        "https://support.huaweicloud.com/intl/en-us/api-cts/cts_api_0001.html",
        "cloud-regions/huawei/cts-endpoints.html",
        _VENDOR_TERMS,
    ),
    Source(
        "huawei-scp",
        "https://support.huaweicloud.com/intl/en-us/usermanual-organizations/org_03_0082.html",
        "cloud-regions/huawei/scp-regions.html",
        _VENDOR_TERMS,
    ),
    Source(
        "aws-feed",
        "https://ip-ranges.amazonaws.com/geo-ip-feed.csv",
        "feeds/aws/geo-ip-feed.csv",
        _FEED_TERMS,
    ),
    Source(
        "aws-ranges",
        "https://ip-ranges.amazonaws.com/ip-ranges.json",
        "feeds/aws/ip-ranges.json",
        _FEED_TERMS,
    ),
    Source(
        "gcp-feed",
        "https://www.gstatic.com/ipranges/cloud_geofeed",
        "feeds/gcp/cloud_geofeed.csv",
        _FEED_TERMS,
    ),
    Source("gcp-ranges", "https://www.gstatic.com/ipranges/cloud.json", "feeds/gcp/cloud.json", _FEED_TERMS),
    Source(
        "azure-feed",
        "https://download.microsoft.com/download/0b9dc6ca-68f9-43b2-b094-da4f6d509bc3/geoloc-Microsoft.csv",
        "feeds/azure/geoloc-Microsoft.csv",
        "Microsoft publishes this for geolocation providers; cited, not redistributed",
    ),
    Source(
        "azure-tags",
        "https://www.microsoft.com/en-us/download/details.aspx?id=56519",
        "feeds/azure/service-tags.json",
        _FEED_TERMS,
        r"https://download\.microsoft\.com/[^\"]*ServiceTags_Public[^\"]*\.json",
    ),
    Source(
        "akamai-feed", "https://ipgeo.akamai.com/linode-geofeed.csv", "feeds/akamai/geofeed.csv", _FEED_TERMS
    ),
    Source("vultr-feed", "https://geofeed.constant.com/", "feeds/vultr/geofeed.csv", _FEED_TERMS),
    Source(
        "digitalocean-feed",
        "https://digitalocean.com/geo/google.csv",
        "feeds/digitalocean/geofeed.csv",
        _FEED_TERMS,
    ),
    Source("hetzner-feed", "https://www.hetzner.com/geofeed.csv", "feeds/hetzner/geofeed.csv", _FEED_TERMS),
    Source(
        "ourairports",
        "https://davidmegginson.github.io/ourairports-data/airports.csv",
        "ourairports/airports.csv",
        "Public domain, per ourairports.com/data",
    ),
    Source(
        "un-locode",
        "https://raw.githubusercontent.com/datasets/un-locode/main/data/code-list.csv",
        "un-locode/code-list.csv",
        "CC BY 4.0, UNECE (UN/CEFACT standard)",
    ),
    Source(
        "geonames",
        "https://download.geonames.org/export/dump/cities500.zip",
        "geonames/cities500.zip",
        "CC BY 4.0, GeoNames",
    ),
    Source(
        "geonames-admin1",
        "https://download.geonames.org/export/dump/admin1CodesASCII.txt",
        "geonames/admin1CodesASCII.txt",
        "CC BY 4.0, GeoNames",
    ),
    Source(
        "geonames-countries",
        "https://download.geonames.org/export/dump/countryInfo.txt",
        "geonames/countryInfo.txt",
        "CC BY 4.0, GeoNames",
    ),
    Source(
        "opentraveldata",
        "https://raw.githubusercontent.com/opentraveldata/opentraveldata/master/opentraveldata/optd_por_public.csv",
        "opentraveldata/optd_por_public.csv",
        "CC BY, OpenTravelData; not an IATA publication",
    ),
    Source(
        "gsf-regions",
        "https://raw.githubusercontent.com/Green-Software-Foundation/real-time-cloud/main/Cloud_Region_Metadata.csv",
        "external/gsf/Cloud_Region_Metadata.csv",
        "Green Software Foundation materials, all rights reserved; cited for comparison, not redistributed",
    ),
    Source(
        "cloudflare-sites",
        "https://www.cloudflarestatus.com/api/v2/components.json",
        "external/cloudflare/components.json",
        "Cloudflare status page under vendor terms; cited for comparison, not redistributed",
    ),
    Source(
        "fastly-pops",
        "https://www.fastly.com/documentation/guides/concepts/pop/",
        "external/fastly/pop.html",
        "Fastly documentation under vendor terms; cited for comparison, not redistributed",
    ),
    Source(
        "zenlayer-compute",
        "https://docs.console.zenlayer.com/welcome/elastic-compute/location.md",
        "external/zenlayer/compute-location.md",
        "Zenlayer documentation under vendor terms; cited for comparison, not redistributed",
    ),
    Source(
        "zenlayer-bare-metal",
        "https://docs.console.zenlayer.com/welcome/bare-metal/overview/models-zones-and-operating-system.md",
        "external/zenlayer/bare-metal-zones.md",
        "Zenlayer documentation under vendor terms; cited for comparison, not redistributed",
    ),
    Source(
        "peeringdb-fac",
        "https://www.peeringdb.com/api/fac?depth=0",
        "peeringdb/fac.json",
        "PeeringDB API, subject to its acceptable use policy",
    ),
]


class ManifestRow(TypedDict):
    """One dataset in the manifest, with its size, hash and retrieval date."""

    name: str
    url: str
    path: str
    bytes: int | str
    sha256: str
    retrieved: str
    license_note: str


class Drift(TypedDict):
    """Which datasets changed, appeared or disappeared against their pins."""

    changed: list[str]
    added: list[str]
    removed: list[str]


class FetchReport(Drift):
    """Dataset drift, plus the datasets that failed to download."""

    failed: list[str]


def client_headers() -> dict[str, str]:
    """Headers that identify this client on every outgoing request.

    The name and contact come from the environment, so an operator's own identity never sits in the repository.

    Returns:
        The ``User-Agent`` header, and ``From`` when a contact address is set.

    Raises:
        RuntimeError: If a proxy is required and none is configured for both HTTP and HTTPS.
    """
    proxies = urllib.request.getproxies()
    if os.environ.get(REQUIRE_PROXY_VARIABLE) and not {"http", "https"} <= set(proxies):
        raise RuntimeError(f"{REQUIRE_PROXY_VARIABLE} is set but no proxy is configured for HTTP and HTTPS")
    headers = {"User-Agent": os.environ.get(USER_AGENT_VARIABLE) or USER_AGENT}
    contact = os.environ.get(FROM_VARIABLE)
    return headers | {"From": contact} if contact else headers


def download(source: Source, raw_dir: StrPath, force: bool = False) -> Path:
    """Download a dataset unless a copy exists, resolving a release link from its page where needed.

    Args:
        source: Dataset to download.
        raw_dir: The data/raw folder.
        force: Download again even when a saved copy exists.

    Returns:
        The path of the saved file.
    """
    target = Path(raw_dir) / source.path
    if target.exists() and not force:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    url = (
        resolve_url(source.page_regex, _get(source.url).decode("utf-8", "replace"))
        if source.page_regex
        else source.url
    )
    _ = target.write_bytes(_get(url))
    return target


def drift(old: Mapping[str, str], new: Mapping[str, str]) -> Drift:
    """Compare two {name: hash} maps.

    Args:
        old: Previous hash per source.
        new: New hash per source.

    Returns:
        The names added, removed and changed.
    """
    return {
        "changed": sorted(k for k in old.keys() & new.keys() if old[k] != new[k]),
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
    }


def fetch_all(raw_dir: StrPath, manifest_path: StrPath, force: bool = False) -> FetchReport:
    """Download sources and return drift against the pins; only a forced refresh, or a first run, moves the pins.

    Args:
        raw_dir: The data/raw folder.
        manifest_path: Path of the dataset list.
        force: Download again even when a saved copy exists.

    Returns:
        The drift, and whether the pins were rewritten.
    """
    previous = read_manifest(manifest_path)
    kept = {r["name"]: r for r in _manifest_rows(manifest_path)}
    rows: list[ManifestRow] = []
    failed: list[str] = []
    for source in SOURCES:
        try:
            path = download(source, raw_dir, force)
        except (OSError, ValueError) as exc:
            print(f"{source.name}: download failed, {str(exc)[:100]}; see {source.url}")
            failed.append(source.name)
            rows += [kept[source.name]] if source.name in kept else []
            continue
        rows.append(
            {
                "name": source.name,
                "url": source.url,
                "path": source.path,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "retrieved": date.today().isoformat(),
                "license_note": source.license_note,
            }
        )
    if force or not previous:
        with Path(manifest_path).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    found = drift(previous, {r["name"]: r["sha256"] for r in rows})
    return {
        "changed": found["changed"],
        "added": found["added"],
        "removed": found["removed"],
        "failed": failed,
    }


def read_manifest(path: StrPath) -> dict[str, str]:
    """Read the pinned hash of each dataset, keyed by name.

    Args:
        path: Path of the dataset list.

    Returns:
        Hashes keyed by dataset name.
    """
    if not Path(path).exists():
        return {}
    return {r["name"]: r["sha256"] for r in csv.DictReader(Path(path).open(encoding="utf-8"))}


def resolve_url(pattern: str, page: str) -> str:
    """Find the current download link on a page whose link changes with each release.

    Args:
        pattern: Regular expression that finds the link.
        page: Normalised text of the page.

    Returns:
        The absolute link.

    Raises:
        ValueError: If no link on the page matches the pattern.
    """
    match = re.search(pattern, page)
    if not match:
        raise ValueError(f"no download link matches {pattern}")
    return match[0]


def sha256_file(path: StrPath) -> str:
    """SHA-256 of a file as hex, read in chunks.

    Args:
        path: File to hash.

    Returns:
        The hex digest.
    """
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ungzipped(body: bytes) -> bytes:
    """Some servers send gzip unasked; saved bytes stay as they came, so reading undoes it.

    Args:
        body: Raw bytes of the response.

    Returns:
        The decompressed bytes, or the input when it is not gzip.

    Raises:
        ValueError: If the body expands beyond the size limit.
    """
    if body[:2] != GZIP_MAGIC:
        return body
    plain = zlib.decompressobj(wbits=31).decompress(body, MAX_UNZIPPED + 1)
    if len(plain) > MAX_UNZIPPED:
        raise ValueError(f"gzip body expands beyond {MAX_UNZIPPED} bytes")
    return plain


def _get_with_retry(
    url: str,
    get: Callable[[str], bytes] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 4,
    wait: float = 60.0,
) -> bytes:
    """Wait longer after each refusal, since some sources limit how often a script may ask.

    Args:
        url: URL of the document.
        get: Function that downloads a URL; ``_plain_get`` when None.
        sleep: Function that waits a number of seconds.
        attempts: Number of tries before giving up.
        wait: Seconds to wait before the first retry.

    Returns:
        The response body.
    """
    get = get or _plain_get
    for attempt in range(attempts - 1):
        try:
            return get(url)
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRY_CODES:
                raise
            sleep(wait * 2**attempt)
    return get(url)


def _manifest_rows(manifest_path: StrPath) -> list[ManifestRow]:
    """Read every row of the dataset manifest, or none when it does not exist.

    Args:
        manifest_path: Path of the dataset list.

    Returns:
        The manifest rows.
    """
    path = Path(manifest_path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return cast(list[ManifestRow], list(csv.DictReader(handle)))


def _plain_get(url: str) -> bytes:
    """Fetch a URL with the project's client name and return the body.

    Args:
        url: URL of the document.

    Returns:
        The response body.
    """
    headers = client_headers()
    robots.require(url, headers)
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=120) as response:
        return cast(bytes, response.read())


_get = _get_with_retry
