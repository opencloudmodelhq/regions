# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Host name lookups through a DNS-over-HTTPS resolver the operator chooses."""

import http.client
import json
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import IO, Final, TypeAlias, cast, override

from ocm_regions.fetch.sources import client_headers

Ask: TypeAlias = Callable[[str], object]

ADDRESS_TYPES: Final = {"A": 1, "AAAA": 28}
DNS_VARIABLE: Final = "OCM_REGIONS_DNS"
MAX_ANSWER: Final = 1024 * 1024
PROXY_RESOLVES: Final = "proxy"
RESOLVERS: Final = {
    "cloudflare": "https://cloudflare-dns.com/dns-query",
    "google": "https://dns.google/resolve",
}


class NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect handler that follows nothing, so a resolver cannot pass the question on."""

    @override
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> None:
        """Refuse every redirect.

        Args:
            req: The original request.
            fp: Body of the redirect response.
            code: HTTP status code of the redirect.
            msg: Status message of the redirect.
            headers: Headers of the redirect response.
            newurl: URL the redirect points to.
        """


def endpoint(resolver: str) -> str:
    """The address of a resolver given by name or by its own https address.

    Args:
        resolver: ``cloudflare``, ``google``, or the https address of a resolver that answers in JSON.

    Returns:
        The address to send questions to.

    Raises:
        ValueError: If the resolver is neither a known name nor an https address.
    """
    known = RESOLVERS.get(resolver.strip().lower())
    if known:
        return known
    parts = urllib.parse.urlsplit(resolver)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError(f"not a known resolver or an https address: {resolver[:80]}")
    return resolver


def lookup(host: str, resolver: str, ask: Ask | None = None) -> list[str]:
    """Resolve a host name to its IPv4 and IPv6 addresses through a chosen resolver.

    Args:
        host: Host name to resolve.
        resolver: Resolver name or https address, as ``endpoint`` accepts.
        ask: Function that sends one question and returns the parsed answer; ``_ask`` when None.

    Returns:
        The addresses, as text.

    Raises:
        OSError: If the resolver gives no address for the name.
    """
    ask = ask or _ask
    base = endpoint(resolver)
    found = [
        address
        for kind, code in ADDRESS_TYPES.items()
        for address in _addresses(ask(f"{base}?{urllib.parse.urlencode({'name': host, 'type': kind})}"), code)
    ]
    if not found:
        raise OSError(f"no address for {host}")
    return found


def _addresses(reply: object, code: int) -> list[str]:
    """The addresses of one record type in a resolver's answer.

    Args:
        reply: Parsed answer of the resolver.
        code: Record type number to keep.

    Returns:
        The addresses, as text.
    """
    answers = _field(reply, "Answer")
    items = cast(list[object], answers) if isinstance(answers, list) else []
    found = [_field(item, "data") for item in items if _field(item, "type") == code]
    return [data for data in found if isinstance(data, str)]


def _ask(url: str) -> object:
    """Send one question to a resolver, through the proxy when one is set.

    Args:
        url: Address of the resolver with the question in its query.

    Returns:
        The parsed answer.
    """
    request = urllib.request.Request(url, headers=client_headers() | {"Accept": "application/dns-json"})
    with urllib.request.build_opener(NoRedirects).open(request, timeout=30) as response:
        return cast(object, json.loads(cast(bytes, response.read(MAX_ANSWER))))


def _field(record: object, name: str) -> object:
    """One field of a JSON object, or None when the value is not an object.

    Args:
        record: Parsed JSON value.
        name: Field to read.

    Returns:
        The field's value.
    """
    return cast(Mapping[str, object], record).get(name) if isinstance(record, dict) else None
