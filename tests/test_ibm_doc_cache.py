"""tools/ibm_doc_cache.py — cache layout and location (#1295).

Two defects fixed here: old-style `/docs/SSxxx_<ver>/...` URLs were cached with no
product/version segment (so versions of one page overwrote each other), and the cache
root was a hard-coded pre-bucket `build/refs/ibm-docs` that re-created a top-level
build/refs on every fetch. The tool now resolves the MAIN checkout's shared cache bucket
through mqlab.buildenv (the build-layout authority). No network, no real build/: the git
runner is faked and fetches write into tmp dirs.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from mqlab import buildenv

REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = REPO_ROOT / "tools" / "ibm_doc_cache.py"


def _load():
    spec = importlib.util.spec_from_file_location("ibm_doc_cache", _TOOL_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_git(common_dir: str):
    def run(args: list[str]) -> str:
        assert args == ["git", "rev-parse", "--git-common-dir"]
        return common_dir

    return run


# --- cache location: the main checkout's shared cache bucket ---
def test_importing_the_tool_touches_no_filesystem_or_git(monkeypatch):
    # The cache root is resolved lazily (main), not at import time.
    monkeypatch.delenv("IBM_DOC_CACHE", raising=False)
    assert not hasattr(_load(), "_CACHE")


def test_cache_root_is_the_main_checkouts_cache_bucket(monkeypatch, tmp_path):
    monkeypatch.delenv("IBM_DOC_CACHE", raising=False)
    tool = _load()
    main_git = tmp_path / "main" / ".git"
    main_git.mkdir(parents=True)
    root = tool._cache_root(run=_fake_git(str(main_git)))
    assert root == tmp_path / "main" / "build" / "cache" / "refs" / "ibm-docs"


def test_cache_root_agrees_with_the_build_layout_authority(monkeypatch, tmp_path):
    # The tool must not spell its own bucket path: it is exactly the authority's
    # cache bucket (what `mqlab build path cache` prints) plus refs/ibm-docs.
    monkeypatch.delenv("IBM_DOC_CACHE", raising=False)
    tool = _load()
    run = _fake_git(str(tmp_path / "main" / ".git"))
    expected = buildenv.bucket_path("cache", REPO_ROOT, run=run) / "refs" / "ibm-docs"
    assert tool._cache_root(run=run) == expected
    assert tool._buildenv() is buildenv  # the same module, from this checkout's src/


def test_cache_root_from_a_worktree_resolves_to_main(monkeypatch, tmp_path):
    # From a worktree, git's common dir is main's .git: the cache lands in main's build/.
    monkeypatch.delenv("IBM_DOC_CACHE", raising=False)
    tool = _load()
    main_git = tmp_path / "main" / ".git"
    main_git.mkdir(parents=True)
    root = tool._cache_root(run=_fake_git(str(main_git)))
    assert root.parent.parent == tmp_path / "main" / "build" / "cache"
    assert ".worktrees" not in root.parts


@pytest.mark.parametrize("module", ["buildenv", "paths"])
def test_the_authority_the_tool_imports_stays_stdlib_only(module):
    # The tool runs under plain `python3` (no project venv) and imports mqlab.buildenv
    # (which imports mqlab.paths) from src/. A third-party import there would break it.
    tree = ast.parse((REPO_ROOT / "src" / "mqlab" / f"{module}.py").read_text())
    imported = {
        name.split(".")[0]
        for node in ast.walk(tree)
        for name in (
            [a.name for a in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else []
        )
    }
    imported -= {"__future__", "mqlab"}
    assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)
    froms = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert {m for m in froms if m and m.startswith("mqlab")} <= {"mqlab.paths"}


def test_cache_root_honours_the_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("IBM_DOC_CACHE", str(tmp_path / "elsewhere"))
    assert _load()._cache_root() == tmp_path / "elsewhere"


def test_no_legacy_build_refs_path_is_spelled_in_the_tool():
    source = _TOOL_PATH.read_text()
    assert '"build" / "refs"' not in source
    assert '"build"' not in source


def test_main_reports_a_git_failure_cleanly(monkeypatch, capsys):
    monkeypatch.delenv("IBM_DOC_CACHE", raising=False)
    tool = _load()

    def boom(run=None):
        raise buildenv.BuildEnvError("git failed (exit 128): git rev-parse --git-common-dir")

    monkeypatch.setattr(tool, "_cache_root", boom)
    assert tool.main(["--list"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("ibm_doc_cache: cannot resolve the shared cache bucket:\n")
    assert "git failed (exit 128)" in err


# --- cache coordinates: product AND version for both URL shapes ---
@pytest.mark.parametrize(
    ("url", "coords"),
    [
        (
            "https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=availability-creating-drha-rdqms",
            ("ibm-mq", "9.4.x", "availability-creating-drha-rdqms"),
        ),
        (
            "https://www.ibm.com/docs/SSYHRD_10.0.0/container/ctr_support_dev.html",
            ("SSYHRD", "10.0.0", "container-ctr_support_dev"),
        ),
        (
            "https://www.ibm.com/docs/en/SSYHRD_10.0.0/container/ctr_support_dev.html",
            ("SSYHRD", "10.0.0", "container-ctr_support_dev"),
        ),
        (
            "https://www.ibm.com/docs/SSFKSJ_9.4.0/com.ibm.mq.con.doc/q123456_.html",
            ("SSFKSJ", "9.4.0", "com.ibm.mq.con.doc-q123456_"),
        ),
    ],
)
def test_coords(url, coords):
    assert _load()._coords(url) == coords


def test_old_style_versions_of_one_page_get_distinct_dirs():
    # The original #1295 report: fetching the 10.0 copy overwrote the 9.4 copy.
    tool = _load()
    v94 = tool._coords("https://www.ibm.com/docs/SSYHRD_9.4.0/container/ctr_support_dev.html")
    v10 = tool._coords("https://www.ibm.com/docs/SSYHRD_10.0.0/container/ctr_support_dev.html")
    assert v94 == ("SSYHRD", "9.4.0", "container-ctr_support_dev")
    assert v10 == ("SSYHRD", "10.0.0", "container-ctr_support_dev")


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=x", "not an https ibm.com/docs URL"),
        ("https://evil.example/docs/en/ibm-mq/9.4.x?topic=x", "not an https ibm.com/docs URL"),
        ("https://www.ibm.com/support/pages/node/123", "not an ibm.com/docs URL"),
        ("https://www.ibm.com/", "not an ibm.com/docs URL"),
        ("https://www.ibm.com/docs/SSYHRD_10.0.0", "names no page"),
        ("https://www.ibm.com/docs/en/ibm-mq/9.4.x", "has no ?topic=<slug>"),
        ("https://www.ibm.com/docs/en/ibm-mq", "unrecognised IBM Docs URL shape"),
        ("https://www.ibm.com/docs", "unrecognised IBM Docs URL shape"),
        ("https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=..", "unsafe cache path segment"),
    ],
)
def test_coords_refuses_rather_than_guessing(url, reason):
    with pytest.raises(ValueError, match=reason.replace("?", r"\?")):
        _load()._coords(url)


# --- fetch + list end to end, network faked, cache in a tmp dir ---
def test_fetch_caches_under_product_version_slug_and_lists(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("IBM_DOC_CACHE", str(tmp_path / "cache"))
    tool = _load()
    shell = '{"oldUrl":"SSYHRD_10.0.0/container/ctr_support_dev.html","title":"Support"}'
    body = "<html><body><p>Hello</p><script>x()</script></body></html>"
    calls: list[str] = []

    def fake_get(url):
        calls.append(url)
        return shell if len(calls) == 1 else body

    monkeypatch.setattr(tool, "_get", fake_get)
    url = "https://www.ibm.com/docs/SSYHRD_10.0.0/container/ctr_support_dev.html"
    assert tool.main([url]) == 0
    dest = tmp_path / "cache" / "SSYHRD" / "10.0.0" / "container-ctr_support_dev"
    assert (dest / "content.txt").read_text() == "Hello"
    meta = json.loads((dest / "meta.json").read_text())
    assert meta["source_url"] == url
    assert meta["content_url"] == (
        "https://www.ibm.com/docs/api/v1/content/SSYHRD_10.0.0/container/ctr_support_dev.html"
    )
    assert calls == [url, meta["content_url"]]
    capsys.readouterr()
    assert tool.main(["--list"]) == 0
    assert capsys.readouterr().out == f"SSYHRD/10.0.0/container-ctr_support_dev  <-  {url}\n"


def test_fetch_rejects_a_bad_url_before_any_network_call(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("IBM_DOC_CACHE", str(tmp_path / "cache"))
    tool = _load()
    monkeypatch.setattr(tool, "_get", lambda url: pytest.fail("network called"))
    assert tool.main(["https://www.ibm.com/docs/en/ibm-mq"]) == 1
    assert "unrecognised IBM Docs URL shape" in capsys.readouterr().err
    assert not (tmp_path / "cache").exists()
