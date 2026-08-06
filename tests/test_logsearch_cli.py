"""Unit tests for `mqlab logsearch` (epic .github#149, plan Task 11).

Scope: the CLI's PURE helpers (health interpretation, snapshot selection, the
Dashboards URL, read-only/full detection, disk summary, snapshot naming, the
Ansible transport argv) plus command WIRING exercised with the network I/O
monkeypatched. A live snapshot/restore round-trip needs the `logsearch` node
(topology #830) and is asserted at the epic's cold-rebuild validation (#835),
never fabricated here.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from mqlab import cli, logsearch
from mqlab.orchestrator import CommandStep, StepFailedError
from mqlab.runner import Command

runner = CliRunner()


# --- interpret_health: single-node replicas:0 → green AND yellow are healthy ---


@pytest.mark.parametrize(
    ("status", "expected"),
    [("green", "healthy"), ("yellow", "healthy"), ("red", "unhealthy")],
)
def test_interpret_health(status: str, expected: str) -> None:
    assert logsearch.interpret_health({"status": status}) == expected


def test_single_node_yellow_is_not_failure() -> None:
    # green and yellow are both healthy on a single-node replicas:0 cluster;
    # only red / unreachable is a failure (plan Task 11 Step 1).
    assert logsearch.interpret_health({"status": "green"}) == "healthy"
    assert logsearch.interpret_health({"status": "yellow"}) == "healthy"
    assert logsearch.interpret_health({"status": "red"}) == "unhealthy"


def test_interpret_health_unknown_or_missing_is_unhealthy() -> None:
    assert logsearch.interpret_health({}) == "unhealthy"
    assert logsearch.interpret_health({"status": "chartreuse"}) == "unhealthy"


# --- latest_snapshot ---


def test_latest_snapshot_selection() -> None:
    assert logsearch.latest_snapshot(["snap-1", "snap-3", "snap-2"]) == "snap-3"


def test_latest_snapshot_empty_is_none() -> None:
    assert logsearch.latest_snapshot([]) is None


def test_latest_snapshot_timestamped_names_sort_lexically() -> None:
    names = [
        "snap-20260101T000000Z",
        "snap-20260806T120000Z",
        "snap-20260501T000000Z",
    ]
    assert logsearch.latest_snapshot(names) == "snap-20260806T120000Z"


# --- dashboards_url ---


def test_dashboards_url() -> None:
    assert logsearch.dashboards_url("10.50.0.4", 5601) == "http://10.50.0.4:5601"


# --- read_only_indices: the flood-stage / full signal ---


def test_read_only_indices_flat_settings() -> None:
    settings = {
        "logs-a": {"settings": {"index.blocks.read_only_allow_delete": "true"}},
        "logs-b": {"settings": {"index.blocks.read_only_allow_delete": "false"}},
        "logs-c": {"settings": {}},
    }
    assert logsearch.read_only_indices(settings) == ["logs-a"]


def test_read_only_indices_none_when_writable() -> None:
    assert logsearch.read_only_indices({"logs-a": {"settings": {}}}) == []


def test_read_only_indices_nested_settings() -> None:
    settings = {
        "logs-a": {"settings": {"index": {"blocks": {"read_only_allow_delete": "true"}}}},
    }
    assert logsearch.read_only_indices(settings) == ["logs-a"]


# --- disk_summary ---


def test_disk_summary_formats_per_node_and_skips_unassigned() -> None:
    alloc = [
        {"node": "logsearch", "disk.percent": "12", "disk.used": "2gb", "disk.total": "20gb"},
        {"node": None, "shards": "0"},  # UNASSIGNED row — no node, skipped
    ]
    summary = logsearch.disk_summary(alloc)
    assert "logsearch" in summary
    assert "12%" in summary


def test_disk_summary_empty_allocation() -> None:
    assert logsearch.disk_summary([]) == "unknown"


# --- snapshot_name ---


def test_snapshot_name_sorts_as_latest() -> None:
    name = logsearch.snapshot_name(datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC))
    assert name == "snap-20260806T120000Z"
    assert logsearch.latest_snapshot([name, "snap-20260101T000000Z"]) == name


# --- Ansible transport argv builders (native _snapshot fs repo; spike Task 2) ---


_REPO = "/var/lib/opensearch/snapshots"
_TAR = "/staging/x.tar.gz"  # arbitrary; the argv builders don't care about the path


def test_archive_argv_tars_repo_dir() -> None:
    argv = logsearch.archive_argv("logsearch", _REPO, _TAR)
    assert argv[:2] == ["ansible", "logsearch"]
    joined = " ".join(argv)
    assert _REPO in joined
    assert _TAR in joined


def test_fetch_argv_pulls_to_host_flat() -> None:
    argv = logsearch.fetch_argv("logsearch", _TAR, "/dest")
    assert argv[:2] == ["ansible", "logsearch"]
    assert "ansible.builtin.fetch" in argv
    joined = " ".join(argv)
    assert f"src={_TAR}" in joined
    assert "flat=true" in joined


def test_copy_argv_stages_host_tar_to_guest() -> None:
    argv = logsearch.copy_argv("logsearch", "/dest/x.tar.gz", _TAR)
    assert "ansible.builtin.copy" in argv
    joined = " ".join(argv)
    assert "src=/dest/x.tar.gz" in joined
    assert f"dest={_TAR}" in joined


def test_untar_argv_expands_into_repo_dir() -> None:
    argv = logsearch.untar_argv("logsearch", _REPO, _TAR)
    joined = " ".join(argv)
    assert _REPO in joined
    assert _TAR in joined


# --- open: pure command (prints dashboards_url) ---


def test_open_prints_dashboards_url() -> None:
    result = runner.invoke(cli.app, ["logsearch", "open"])
    assert result.exit_code == 0, result.output
    url = logsearch.dashboards_url(logsearch.OPENSEARCH_HOST, logsearch.DASHBOARDS_PORT)
    assert url in result.output


# --- status: command wiring with the HTTP layer monkeypatched ---


def _health_get(status: str, *, read_only: bool = False, percent: str = "5"):
    def fake_get(url: str):
        if "_cluster/health" in url:
            return {"status": status}
        if "_cat/allocation" in url:
            return [
                {
                    "node": "logsearch",
                    "disk.percent": percent,
                    "disk.used": "1gb",
                    "disk.total": "20gb",
                }
            ]
        if "_settings" in url:
            if read_only:
                return {"logs-a": {"settings": {"index.blocks.read_only_allow_delete": "true"}}}
            return {"logs-a": {"settings": {}}}
        raise AssertionError(f"unexpected url {url}")

    return fake_get


def test_status_green_reports_and_exits_zero(monkeypatch) -> None:
    monkeypatch.setattr(logsearch, "_get_json", _health_get("green"))
    result = runner.invoke(cli.app, ["logsearch", "status"])
    assert result.exit_code == 0, result.output
    assert "green" in result.output
    assert "healthy" in result.output


def test_status_yellow_exits_zero(monkeypatch) -> None:
    monkeypatch.setattr(logsearch, "_get_json", _health_get("yellow"))
    result = runner.invoke(cli.app, ["logsearch", "status"])
    assert result.exit_code == 0, result.output


def test_status_red_exits_nonzero(monkeypatch) -> None:
    monkeypatch.setattr(logsearch, "_get_json", _health_get("red"))
    result = runner.invoke(cli.app, ["logsearch", "status"])
    assert result.exit_code != 0


def test_status_loud_on_read_only_full(monkeypatch) -> None:
    # Full/read-only must be LOUD and fail — never a silent skip (plan Task 11 Step 3).
    monkeypatch.setattr(logsearch, "_get_json", _health_get("green", read_only=True, percent="98"))
    result = runner.invoke(cli.app, ["logsearch", "status"])
    assert result.exit_code != 0
    lowered = result.output.lower()
    assert "read-only" in lowered or "full" in lowered
    assert "logs-a" in result.output


def test_status_unreachable_is_loud_mqlab_scoped_error(monkeypatch) -> None:
    def boom(url: str):
        raise logsearch.LogsearchError(f"mqlab logsearch status: cannot reach OpenSearch at {url}")

    monkeypatch.setattr(logsearch, "_get_json", boom)
    result = runner.invoke(cli.app, ["logsearch", "status"])
    assert result.exit_code != 0
    assert "mqlab logsearch" in result.output


# --- snapshot / restore: wiring exercised with HTTP + step execution stubbed ---


def test_snapshot_triggers_api_then_fetches_to_state(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    calls: dict[str, object] = {}

    def fake_put(url: str, body: dict | None = None):
        calls["put"] = url
        return {"snapshot": {"snapshot": "snap-x", "state": "SUCCESS"}}

    executed: list[list[str]] = []

    def fake_exec(verb: str, steps) -> None:
        executed.extend(step.command.argv for step in steps)

    monkeypatch.setattr(logsearch, "_put_json", fake_put)
    monkeypatch.setattr(logsearch, "_execute_steps", fake_exec)

    result = runner.invoke(cli.app, ["logsearch", "snapshot"])
    assert result.exit_code == 0, result.output
    # the OpenSearch snapshot API was invoked with wait_for_completion
    assert "_snapshot/" in str(calls["put"])
    assert "wait_for_completion" in str(calls["put"])
    # and the repo was tarred + fetched to the host state bucket
    flat = " ".join(" ".join(a) for a in executed)
    assert "ansible.builtin.fetch" in flat
    assert str(logsearch._snapshot_state_dir()) in flat


def test_restore_uses_latest_when_no_snapshot_given(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    store = logsearch._snapshot_state_dir()
    store.mkdir(parents=True, exist_ok=True)
    (store / "snap-20260101T000000Z.tar.gz").write_bytes(b"a")
    (store / "snap-20260806T120000Z.tar.gz").write_bytes(b"b")

    posted: dict[str, object] = {}

    def fake_post(url: str, body: dict | None = None):
        posted["url"] = url
        return {"accepted": True}

    monkeypatch.setattr(logsearch, "_post_json", fake_post)
    monkeypatch.setattr(logsearch, "_execute_steps", lambda verb, steps: None)

    result = runner.invoke(cli.app, ["logsearch", "restore"])
    assert result.exit_code == 0, result.output
    # the latest snapshot name is the one restored
    assert "snap-20260806T120000Z/_restore" in str(posted["url"])


def test_restore_no_snapshot_present_is_loud_not_silent(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    # no snapshot store on the host at all
    monkeypatch.setattr(logsearch, "_post_json", lambda url, body=None: {})
    monkeypatch.setattr(logsearch, "_execute_steps", lambda verb, steps: None)

    result = runner.invoke(cli.app, ["logsearch", "restore"])
    assert result.exit_code != 0
    assert "mqlab logsearch restore" in result.output


# --- disk_full: threshold + defensive parsing ---


def test_disk_full_detects_high_node() -> None:
    assert logsearch.disk_full([{"node": "logsearch", "disk.percent": "95"}]) is True


def test_disk_full_below_threshold() -> None:
    assert logsearch.disk_full([{"node": "logsearch", "disk.percent": "10"}]) is False


def test_disk_full_skips_unassigned_and_nonnumeric() -> None:
    rows = [
        {"node": None, "disk.percent": "99"},  # UNASSIGNED — skipped
        {"node": "logsearch", "disk.percent": "n/a"},  # non-numeric — skipped
    ]
    assert logsearch.disk_full(rows) is False


def test_status_loud_when_disk_near_full_but_writable(monkeypatch) -> None:
    # High disk but not yet read-only: warn loudly, but do not fail (still writable).
    monkeypatch.setattr(logsearch, "_get_json", _health_get("green", read_only=False, percent="95"))
    result = runner.invoke(cli.app, ["logsearch", "status"])
    assert result.exit_code == 0, result.output
    assert "near full" in result.output.lower()


# --- the HTTP seam (_request and its GET/PUT/POST wrappers) ---


class _FakeResp:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def read(self) -> bytes:
        return self._raw


def test_get_json_parses_body(monkeypatch) -> None:
    monkeypatch.setattr(
        logsearch.urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeResp(b'{"status": "green"}'),
    )
    assert logsearch._get_json("http://x/_cluster/health") == {"status": "green"}


def test_request_empty_body_is_empty_dict(monkeypatch) -> None:
    monkeypatch.setattr(
        logsearch.urllib.request, "urlopen", lambda req, timeout=None: _FakeResp(b"")
    )
    assert logsearch._post_json("http://x/_snapshot/logsearch-fs/s/_restore", {}) == {}


def test_put_json_sends_body(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_urlopen(req, timeout=None):
        seen["data"] = req.data
        seen["method"] = req.get_method()
        return _FakeResp(b'{"snapshot": {"state": "SUCCESS"}}')

    monkeypatch.setattr(logsearch.urllib.request, "urlopen", fake_urlopen)
    out = logsearch._put_json("http://x/_snapshot/logsearch-fs/s", {"indices": "logs-*"})
    assert out["snapshot"]["state"] == "SUCCESS"
    assert seen["method"] == "PUT"
    data = seen["data"]
    assert isinstance(data, (bytes, bytearray))
    assert b"logs-*" in data


def test_request_unreachable_raises_loud_mqlab_error(monkeypatch) -> None:
    def boom(req, timeout=None):
        raise logsearch.urllib.error.URLError("connection refused")

    monkeypatch.setattr(logsearch.urllib.request, "urlopen", boom)
    with pytest.raises(logsearch.LogsearchError) as excinfo:
        logsearch._get_json("http://10.50.0.4:9200/_cluster/health")
    assert "mqlab logsearch" in str(excinfo.value)


# --- _execute_steps runs steps through the CommandRunner seam ---


def test_execute_steps_runs_each_step(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    ran: list[str] = []

    class _FakeRunner:
        def run(self, command, on_line) -> int:
            ran.append(command.display())
            on_line("ok")
            return 0

    monkeypatch.setattr(logsearch, "SubprocessRunner", _FakeRunner)

    logsearch._execute_steps("logsearch-test", [CommandStep("echo", Command(["echo", "hi"]))])
    assert ran == ["echo hi"]


# --- snapshot: failure branches ---


def test_snapshot_api_failure_is_loud_no_fetch(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(
        logsearch, "_put_json", lambda url, body=None: {"snapshot": {"state": "PARTIAL"}}
    )
    fetched = []
    monkeypatch.setattr(logsearch, "_execute_steps", lambda v, s: fetched.append(s))
    result = runner.invoke(cli.app, ["logsearch", "snapshot"])
    assert result.exit_code == 1
    assert "PARTIAL" in result.output
    assert fetched == []  # never fetched an inconsistent set


def test_snapshot_unreachable_exits_two(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))

    def boom(url, body=None):
        raise logsearch.LogsearchError("mqlab logsearch: cannot reach OpenSearch")

    monkeypatch.setattr(logsearch, "_put_json", boom)
    monkeypatch.setattr(logsearch, "_execute_steps", lambda v, s: None)
    result = runner.invoke(cli.app, ["logsearch", "snapshot"])
    assert result.exit_code == 2


def test_snapshot_transport_failure_is_loud(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(
        logsearch, "_put_json", lambda url, body=None: {"snapshot": {"state": "SUCCESS"}}
    )

    def fail(verb, steps):
        raise StepFailedError("fetch", 4)

    monkeypatch.setattr(logsearch, "_execute_steps", fail)
    result = runner.invoke(cli.app, ["logsearch", "snapshot"])
    assert result.exit_code == 4
    assert "not durable on the host" in result.output


# --- restore: explicit-name + failure branches ---


def test_restore_explicit_snapshot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    store = logsearch._snapshot_state_dir()
    store.mkdir(parents=True, exist_ok=True)
    (store / "snap-20260101T000000Z.tar.gz").write_bytes(b"a")
    posted: dict[str, object] = {}
    monkeypatch.setattr(
        logsearch, "_post_json", lambda url, body=None: posted.update(url=url) or {}
    )
    monkeypatch.setattr(logsearch, "_execute_steps", lambda v, s: None)
    result = runner.invoke(cli.app, ["logsearch", "restore", "--snapshot", "snap-20260101T000000Z"])
    assert result.exit_code == 0, result.output
    assert "snap-20260101T000000Z/_restore" in str(posted["url"])


def test_restore_explicit_missing_artifact_is_loud(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    store = logsearch._snapshot_state_dir()
    store.mkdir(parents=True, exist_ok=True)
    (store / "snap-20260101T000000Z.tar.gz").write_bytes(b"a")
    monkeypatch.setattr(logsearch, "_post_json", lambda url, body=None: {})
    monkeypatch.setattr(logsearch, "_execute_steps", lambda v, s: None)
    result = runner.invoke(cli.app, ["logsearch", "restore", "--snapshot", "snap-nope"])
    assert result.exit_code == 1
    assert "no artifact" in result.output


def test_restore_transport_failure_is_loud(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    store = logsearch._snapshot_state_dir()
    store.mkdir(parents=True, exist_ok=True)
    (store / "snap-20260101T000000Z.tar.gz").write_bytes(b"a")
    monkeypatch.setattr(logsearch, "_post_json", lambda url, body=None: {})

    def fail(verb, steps):
        raise StepFailedError("stage", 5)

    monkeypatch.setattr(logsearch, "_execute_steps", fail)
    result = runner.invoke(cli.app, ["logsearch", "restore"])
    assert result.exit_code == 5
    assert "staging snapshot" in result.output


def test_restore_api_unreachable_exits_two(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    store = logsearch._snapshot_state_dir()
    store.mkdir(parents=True, exist_ok=True)
    (store / "snap-20260101T000000Z.tar.gz").write_bytes(b"a")

    def boom(url, body=None):
        raise logsearch.LogsearchError("mqlab logsearch: cannot reach OpenSearch")

    monkeypatch.setattr(logsearch, "_post_json", boom)
    monkeypatch.setattr(logsearch, "_execute_steps", lambda v, s: None)
    result = runner.invoke(cli.app, ["logsearch", "restore"])
    assert result.exit_code == 2
