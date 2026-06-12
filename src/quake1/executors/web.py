"""web.* — default browser via `open`; fetch via httpx with crude tag stripping."""

from __future__ import annotations

import re
import urllib.parse

import httpx

from . import _util
from ._util import ToolError

_BLOCKS = re.compile(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>")
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
MAX_CHARS = 5000


def _http_url(args: dict) -> str:
    url = str(args.get("url") or "").strip()
    if not url:
        raise ToolError("a url is required")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", url):
        url = "https://" + url  # bare host like 'example.com' — assume https
    if not url.startswith(("http://", "https://")):
        raise ToolError(f"only http(s) URLs are allowed, got {url!r}")
    return url


def search(args: dict) -> dict:
    q = str(args.get("query") or "").strip()
    if not q:
        raise ToolError("a search query is required")
    url = "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(q)
    _util.run_cmd(["open", url])
    return {"opened": url}


def open_url(args: dict) -> dict:
    url = _http_url(args)
    _util.run_cmd(["open", url])
    return {"opened": url}


def fetch_url(args: dict) -> dict:
    url = _http_url(args)
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise ToolError(f"fetch failed: {e}") from e
    text = _WS.sub(" ", _TAGS.sub(" ", _BLOCKS.sub(" ", r.text))).strip()
    return {"url": str(r.url), "text": text[:MAX_CHARS]}


HANDLERS = {
    "web.search": search,
    "web.open_url": open_url,
    "web.fetch_url": fetch_url,
}
