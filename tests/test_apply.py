import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

import mqlab.apply as apply_mod
from mqlab.apply import KIND_TO_METHOD


def test_every_content_kind_is_mapped():
    for spec_file in Path("content").glob("*.yaml"):
        spec = yaml.safe_load(spec_file.read_text())
        for obj in spec["objects"]:
            assert obj["kind"] in KIND_TO_METHOD, f"{spec_file}: unmapped kind {obj['kind']}"


def test_mapped_methods_exist_on_session():
    from pymqrest import MQRESTSession

    for method in KIND_TO_METHOD.values():
        assert hasattr(MQRESTSession, method)


def test_apply_spec_invokes_mapped_methods(monkeypatch, tmp_path, capsys):
    calls = []

    class FakeSession:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs["qmgr_name"], kwargs["rest_base_url"]))

        def ensure_qlocal(self, name, request_parameters=None):
            calls.append(("qlocal", name, request_parameters))
            return SimpleNamespace(action=SimpleNamespace(name="CREATED"))

    monkeypatch.setenv("MQWEB_ADMIN_USER", "u")
    monkeypatch.setenv("MQWEB_ADMIN_PASSWORD", "p")
    monkeypatch.setattr(apply_mod, "MQRESTSession", FakeSession)
    spec = tmp_path / "spec.yaml"
    spec.write_text("qmgr: QX\nobjects:\n  - { kind: qlocal, name: Q1, attrs: { usage: XMITQ } }\n")

    rc = apply_mod.apply_spec(str(spec), "https://h:9443")

    assert rc == 0
    assert ("init", "QX", "https://h:9443/ibmmq/rest/v2") in calls
    assert ("qlocal", "Q1", {"usage": "XMITQ"}) in calls
    assert "CREATED" in capsys.readouterr().out


def test_main_delegates_to_apply_spec(monkeypatch):
    seen = []
    monkeypatch.setattr(apply_mod, "apply_spec", lambda a, b: seen.append((a, b)) or 0)
    monkeypatch.setattr(sys, "argv", ["apply", "spec.yaml", "https://h:9443"])

    assert apply_mod.main() == 0
    assert seen == [("spec.yaml", "https://h:9443")]


def test_verify_tls_defaults_off_and_is_opt_in(monkeypatch):
    """MQLAB_REST_VERIFY_TLS gates REST cert verification (#250). Default off;
    set truthy to opt in. Reloads the module to re-evaluate the import-time flag."""
    import importlib

    monkeypatch.delenv("MQLAB_REST_VERIFY_TLS", raising=False)
    importlib.reload(apply_mod)
    assert apply_mod.VERIFY_TLS is False

    monkeypatch.setenv("MQLAB_REST_VERIFY_TLS", "true")
    importlib.reload(apply_mod)
    assert apply_mod.VERIFY_TLS is True

    # Restore the default-off module state for any later tests.
    monkeypatch.delenv("MQLAB_REST_VERIFY_TLS", raising=False)
    importlib.reload(apply_mod)
