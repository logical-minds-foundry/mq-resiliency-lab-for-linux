#!/usr/bin/env python3
"""Fetch canonical IBM Docs pages and cache them locally (#225).

IBM Docs (ibm.com/docs) return HTTP 403 to bot user-agents (the built-in WebFetch),
so research can't read the primary pages — only search snippets / third-party mirrors.
The block is a *user-agent heuristic*, not auth or a paywall: with a browser UA the
public page returns 200. The page is a JS SPA, but it embeds the legacy content path
as `"oldUrl":"SSFKSJ_<ver>/.../qNNNNNN_.html"`, and
`https://www.ibm.com/docs/api/v1/content/<oldUrl>` returns the **clean canonical topic
body** — the authoritative text to quote.

This utility automates that recipe and caches the result in the MAIN checkout's shared
cache bucket (gitignored), at `<cache bucket>/refs/ibm-docs/<product>/<version>/<slug>/`
— i.e. `build/cache/refs/ibm-docs/...`, resolved through the build-layout authority
(`mqlab.buildenv`, the same code behind `mqlab build path cache`) — as `content.html`
(raw body), `content.txt` (tag-stripped), and `meta.json` (source URL, content URL,
retrieval timestamp, sha256). Both URL shapes get a product/version segment, so versions
of one page never overwrite each other (#1295):

    /docs/en/<product>/<version>?topic=<slug>     -> <product>/<version>/<slug>/
    /docs/[en/]<SScode>_<version>/<dir>/<page>.html -> <SScode>/<version>/<dir>-<page>/

The cache is gitignored (do not redistribute IBM content); this tool is committed.
Public docs, low volume — be polite.

Usage:
    tools/ibm_doc_cache.py <ibm-docs-url> [<url> ...]
    tools/ibm_doc_cache.py --list            # show what's cached
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import sys
import urllib.request
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
# IBM's edge tightened its bot heuristic: a lone User-Agent now gets HTTP 403
# (even on pages that used to return 200). It waves a request through only when
# the browser-shaped triad is all present — a real `Accept`, an `Accept-Language`,
# AND an `Accept-Encoding`. We ask for `identity` (no compression) on purpose so
# urllib hands back a plain-text body with nothing to decompress. See #1070.
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "identity",
}
_CONTENT_API = "https://www.ibm.com/docs/api/v1/content/"


_REPO = Path(__file__).resolve().parent.parent  # tools/ -> this checkout's root
# The IBM-docs tree inside the shared `cache` bucket. The bucket itself is never spelled
# here: it comes from the build-layout authority (mqlab.buildenv.bucket_path).
_BUCKET = "cache"
_SUBPATH = ("refs", "ibm-docs")


def _buildenv() -> ModuleType:
    """mqlab.buildenv from THIS checkout's src/ — the build-layout authority.

    It is stdlib-only (so plain `python3 tools/ibm_doc_cache.py` can import it without
    the project venv) and resolves the MAIN checkout's build/ via git's common dir, so
    the cache accumulates in one place from any worktree and survives worktree removal.
    """
    src = str(_REPO / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    return importlib.import_module("mqlab.buildenv")


def _cache_root(run: Callable[[list[str]], str] | None = None) -> Path:
    """Where cached docs live: the MAIN checkout's shared cache bucket + refs/ibm-docs
    (build/cache/refs/ibm-docs/, host-durable). Set $IBM_DOC_CACHE to relocate the
    cache to a permanent home without code changes (#226). `run` injects the git
    runner (tests); a git failure raises the authority's BuildEnvError."""
    override = os.environ.get("IBM_DOC_CACHE")
    if override:
        return Path(override).expanduser()
    buildenv = _buildenv()
    bucket: Path = (
        buildenv.bucket_path(_BUCKET, _REPO)
        if run is None
        else buildenv.bucket_path(_BUCKET, _REPO, run=run)
    )
    return bucket.joinpath(*_SUBPATH)


class _TextExtractor(HTMLParser):
    """Minimal HTML->text: drop script/style, turn blocks into line breaks."""

    _BLOCKS = {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "pre", "table"}

    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self._out: list[str] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._BLOCKS:
            self._out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self._out.append(data)

    def text(self) -> str:
        joined = "".join(self._out)
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", joined)).strip()


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=_HEADERS)  # noqa: S310
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        return resp.read().decode("utf-8", errors="replace")


_HOSTS = frozenset({"www.ibm.com", "ibm.com"})
_LANG = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$")  # "en", "en-us"
# Old-style product prefix: an IBM product code + version, e.g. SSFKSJ_9.4.0, SSYHRD_10.0.0.
_OLD_PREFIX = re.compile(r"^(SS[A-Z0-9]+)_(\d[0-9A-Za-z.]*)$")
# One safe path segment: no separators, no dot-only names (no escaping the cache dir).
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _coords(url: str) -> tuple[str, str, str]:
    """(product, version, slug) from an ibm.com/docs URL, for the cache path.

    Two URL shapes, both keyed by product AND version so versions never collide:
    - new style  /docs/<lang>/<product>/<version>?topic=<slug>
    - old style  /docs/[<lang>/]<SScode>_<version>/<dir>/.../<page>.html, keyed
      <SScode>/<version>/<dir>-...-<page> (IBM redirects the lang-less form to /docs/en/).
    Anything else raises ValueError rather than guessing a location."""
    parts = urlparse(url)
    if parts.scheme != "https" or parts.hostname not in _HOSTS:
        raise ValueError(f"not an https ibm.com/docs URL: {url}")
    segs = [s for s in parts.path.split("/") if s]
    if not segs or segs[0] != "docs":
        raise ValueError(f"not an ibm.com/docs URL: {url}")
    rest = segs[1:]
    if len(rest) >= 2 and _LANG.match(rest[0]) and _OLD_PREFIX.match(rest[1]):
        rest = rest[1:]  # drop the language of an old-style /docs/en/SSxxx_<ver>/... URL
    old = _OLD_PREFIX.match(rest[0]) if rest else None
    if old:
        product, version = old.group(1), old.group(2)
        page = rest[1:]
        if not page:
            raise ValueError(f"old-style IBM Docs URL names no page: {url}")
        slug = "-".join(page).removesuffix(".html")
    elif len(rest) == 3 and _LANG.match(rest[0]):
        product, version = rest[1], rest[2]
        slug = parse_qs(parts.query).get("topic", [""])[0]
        if not slug:
            raise ValueError(f"IBM Docs URL has no ?topic=<slug>: {url}")
    else:
        raise ValueError(
            f"unrecognised IBM Docs URL shape (expected /docs/<lang>/<product>/<version>"
            f"?topic=<slug> or /docs/<SScode>_<version>/<page>.html): {url}"
        )
    for segment in (product, version, slug):
        if not _SAFE_SEGMENT.match(segment):
            raise ValueError(f"unsafe cache path segment {segment!r} from {url}")
    return product, version, slug


def fetch(url: str, cache: Path) -> Path:
    """Fetch one IBM Docs page via the canonical content API; cache + return its dir."""
    product, version, slug = _coords(url)  # reject a bad URL before any network call
    shell = _get(url)
    m = re.search(r'"oldUrl":"([^"]+\.html)"', shell)
    if not m:
        raise RuntimeError(f"no oldUrl (content path) found in {url} — page layout changed?")
    old_url = m.group(1)
    title_m = re.search(r'"title":"([^"]+)"', shell)
    body = _get(_CONTENT_API + old_url)

    dest = cache / product / version / slug
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "content.html").write_text(body, encoding="utf-8")
    extractor = _TextExtractor()
    extractor.feed(body)
    (dest / "content.txt").write_text(extractor.text(), encoding="utf-8")
    (dest / "meta.json").write_text(
        json.dumps(
            {
                "source_url": url,
                "content_url": _CONTENT_API + old_url,
                "old_url": old_url,
                "title": title_m.group(1) if title_m else "",
                "retrieved_at": datetime.now(tz=UTC).isoformat(),
                "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("urls", nargs="*", help="IBM Docs URLs to fetch + cache")
    ap.add_argument("--list", action="store_true", help="list cached docs and exit")
    args = ap.parse_args(argv)
    if not args.list and not args.urls:
        ap.error("provide at least one URL, or --list")

    try:
        cache = _cache_root()
    except RuntimeError as exc:  # the authority's BuildEnvError (a git failure): say why
        print(f"ibm_doc_cache: cannot resolve the shared cache bucket:\n{exc}", file=sys.stderr)
        return 2

    if args.list:
        for meta in sorted(cache.rglob("meta.json")):
            info = json.loads(meta.read_text())
            print(f"{meta.parent.relative_to(cache)}  <-  {info['source_url']}")
        return 0

    rc = 0
    for url in args.urls:
        try:
            dest = fetch(url, cache)
            txt = (dest / "content.txt").read_text(encoding="utf-8")
            print(f"OK  {url}\n    cached -> {dest}  ({len(txt)} chars of body text)")
        except Exception as exc:  # noqa: BLE001 - report per-URL, keep going, fail loud
            print(f"FAIL {url}\n    {exc}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
