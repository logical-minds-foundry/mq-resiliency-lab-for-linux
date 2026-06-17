#!/usr/bin/env python3
"""Fetch canonical IBM Docs pages and cache them locally (#225).

IBM Docs (ibm.com/docs) return HTTP 403 to bot user-agents (the built-in WebFetch),
so research can't read the primary pages — only search snippets / third-party mirrors.
The block is a *user-agent heuristic*, not auth or a paywall: with a browser UA the
public page returns 200. The page is a JS SPA, but it embeds the legacy content path
as `"oldUrl":"SSFKSJ_<ver>/.../qNNNNNN_.html"`, and
`https://www.ibm.com/docs/api/v1/content/<oldUrl>` returns the **clean canonical topic
body** — the authoritative text to quote.

This utility automates that recipe and caches the result under (gitignored)
`build/refs/ibm-docs/<product>/<version>/<slug>/` as `content.html` (raw body),
`content.txt` (tag-stripped), and `meta.json` (source URL, content URL, retrieval
timestamp, sha256). The cache is gitignored (do not redistribute IBM content); this
tool is committed. Public docs, low volume — be polite.

Usage:
    tools/ibm_doc_cache.py <ibm-docs-url> [<url> ...]
    tools/ibm_doc_cache.py --list            # show what's cached
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
_CONTENT_API = "https://www.ibm.com/docs/api/v1/content/"


def _main_root() -> Path:
    """Repo main-worktree root, so the cache accumulates in one place regardless of
    which worktree the tool runs from (strips any `/.worktrees/<name>` segment)."""
    root = str(Path(__file__).resolve().parent.parent)  # tools/ -> repo root
    marker = "/.worktrees/"
    return Path(root.split(marker)[0]) if marker in root else Path(root)


def _cache_root() -> Path:
    """Where cached docs live. Defaults to the MAIN worktree's build/refs/ibm-docs/
    (host-durable, survives worktree removal). `build/` is scratch-by-convention, so
    set $IBM_DOC_CACHE to relocate the cache to a permanent home without code changes
    — see the permanent-home backlog issue (#226)."""
    override = os.environ.get("IBM_DOC_CACHE")
    if override:
        return Path(override).expanduser()
    return _main_root() / "build" / "refs" / "ibm-docs"


_CACHE = _cache_root()


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
    # This tool only ever fetches IBM Docs. Enforce that: reject non-https and any
    # host outside ibm.com, so a dynamic URL can't be pointed at an internal/SSRF
    # target. With that guard, the dynamic urlopen below is intended and safe.
    parts = urlparse(url)
    host = parts.hostname or ""
    if parts.scheme != "https" or not (host == "ibm.com" or host.endswith(".ibm.com")):
        raise RuntimeError(f"refusing to fetch non-https or non-ibm.com URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "text/html"})  # nosemgrep
    # host-pinned to ibm.com above; the dynamic fetch is this tool's whole purpose.
    with urllib.request.urlopen(req, timeout=30) as resp:  # nosemgrep
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        return resp.read().decode("utf-8", errors="replace")


def _coords(url: str) -> tuple[str, str, str]:
    """(product, version, slug) from an ibm.com/docs URL, for the cache path."""
    parts = urlparse(url)
    segs = [s for s in parts.path.split("/") if s]  # ["docs","en","ibm-mq","9.4.x"]
    product = segs[2] if len(segs) > 2 else "unknown"
    version = segs[3] if len(segs) > 3 else "unknown"
    slug = (parse_qs(parts.query).get("topic", [""])[0]) or segs[-1]
    return product, version, slug


def fetch(url: str) -> Path:
    """Fetch one IBM Docs page via the canonical content API; cache + return its dir."""
    shell = _get(url)
    m = re.search(r'"oldUrl":"([^"]+\.html)"', shell)
    if not m:
        raise RuntimeError(f"no oldUrl (content path) found in {url} — page layout changed?")
    old_url = m.group(1)
    title_m = re.search(r'"title":"([^"]+)"', shell)
    body = _get(_CONTENT_API + old_url)

    product, version, slug = _coords(url)
    dest = _CACHE / product / version / slug
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

    if args.list:
        for meta in sorted(_CACHE.rglob("meta.json")):
            info = json.loads(meta.read_text())
            print(f"{meta.parent.relative_to(_CACHE)}  <-  {info['source_url']}")
        return 0
    if not args.urls:
        ap.error("provide at least one URL, or --list")

    rc = 0
    for url in args.urls:
        try:
            dest = fetch(url)
            txt = (dest / "content.txt").read_text(encoding="utf-8")
            print(f"OK  {url}\n    cached -> {dest}  ({len(txt)} chars of body text)")
        except Exception as exc:  # noqa: BLE001 - report per-URL, keep going, fail loud
            print(f"FAIL {url}\n    {exc}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
