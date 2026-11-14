# SPDX-FileCopyrightText: 2026 Mosox Digital Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Obey each site's robots.txt for this client's name."""

import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Final, TypeAlias
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

Read: TypeAlias = Callable[[str, Mapping[str, str]], str | None]

BUSY: Final = 429
DENY_ALL: Final = ("User-agent: *", "Disallow: /")
MAX_BYTES: Final = 512 * 1024
MAX_CRAWL_DELAY: Final = 60
MISSING: Final = range(400, 500)
_RULES: Final[dict[tuple[str, str], RobotFileParser]] = {}


class RobotsDisallowed(PermissionError):
    """A site's robots.txt does not let this client read a URL."""


class FiveRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect handler that follows a robots.txt file to any site, five redirects at most (RFC 9309)."""

    max_redirections = 5


def allowed(url: str, headers: Mapping[str, str], read: Read | None = None) -> bool:
    """Whether the site's robots.txt lets this client read a URL.

    Args:
        url: URL of the document.
        headers: Headers the request will carry; the client name is taken from ``User-Agent``.
        read: Function that returns a robots.txt file's text; ``read_robots`` when None.

    Returns:
        True when the URL may be read.
    """
    name = product(headers["User-Agent"])
    rules = _rules(url, headers, read or read_robots)
    return rules.can_fetch(name, url) and float(rules.crawl_delay(name) or 0) <= MAX_CRAWL_DELAY


def crawl_delay(url: str, headers: Mapping[str, str]) -> float:
    """Seconds the site asks this client to wait between requests, once the site has been checked.

    Args:
        url: URL of the document.
        headers: Headers the request carries; the client name is taken from ``User-Agent``.

    Returns:
        The delay in seconds; zero when the site names none or has not been checked yet.
    """
    name = product(headers["User-Agent"])
    rules = _RULES.get((_origin(url), name))
    delay = rules.crawl_delay(name) if rules else None
    return min(float(delay), MAX_CRAWL_DELAY) if delay else 0.0


def forget() -> None:
    """Drop every robots.txt read so far, so the next check reads it again."""
    _RULES.clear()


def product(user_agent: str) -> str:
    """The name a robots.txt file addresses this client by.

    Args:
        user_agent: The full ``User-Agent`` value.

    Returns:
        The part before the version and any comment.
    """
    return user_agent.split("/")[0].split()[0]


def read_robots(
    url: str, headers: Mapping[str, str], opener: urllib.request.OpenerDirector | None = None
) -> str | None:
    """Fetch a robots.txt file, following at most five redirects unless an opener says otherwise.

    Args:
        url: URL of the robots.txt file.
        headers: Headers to send.
        opener: Opener to send the request with; one that follows five redirects when None.

    Returns:
        The file's text; empty when the site has none to give; None when the site is busy or failed, which allows nothing.
    """
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with (opener or urllib.request.build_opener(FiveRedirects)).open(request, timeout=30) as response:
            body: bytes = response.read(MAX_BYTES)
    except urllib.error.HTTPError as error:
        return "" if error.code in MISSING and error.code != BUSY else None
    except (OSError, ValueError):
        return None
    return body.decode("utf-8", errors="replace")


def require(url: str, headers: Mapping[str, str], read: Read | None = None) -> None:
    """Stop a request the site's robots.txt does not allow.

    Args:
        url: URL of the document.
        headers: Headers the request will carry; the client name is taken from ``User-Agent``.
        read: Function that returns a robots.txt file's text; ``read_robots`` when None.

    Raises:
        RobotsDisallowed: If the URL may not be read.
    """
    if not allowed(url, headers, read):
        raise RobotsDisallowed(f"robots.txt at {_origin(url)} disallows {url}")


def _origin(url: str) -> str:
    """The scheme and host a robots.txt file covers.

    Args:
        url: URL of the document.

    Returns:
        The origin, without a trailing slash.
    """
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _parsed(text: str | None) -> RobotFileParser:
    """Rules from a robots.txt file's text.

    Args:
        text: The file's text, or None for a site that allows nothing.

    Returns:
        The parsed rules.
    """
    rules = RobotFileParser()
    rules.parse(DENY_ALL if text is None else text.splitlines())
    return rules


def _rules(url: str, headers: Mapping[str, str], read: Read) -> RobotFileParser:
    """The rules for a URL's site, read once for each site and client name.

    Args:
        url: URL of the document.
        headers: Headers the request will carry.
        read: Function that returns a robots.txt file's text.

    Returns:
        The site's rules.
    """
    key = (_origin(url), product(headers["User-Agent"]))
    if key not in _RULES:
        _RULES[key] = _parsed(read(f"{key[0]}/robots.txt", headers))
    return _RULES[key]
