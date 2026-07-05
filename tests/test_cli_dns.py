from __future__ import annotations

import json

from typer.testing import CliRunner

from mqlab import cli


def _seed_topo(tmp_path):
    (tmp_path / "lab").mkdir(parents=True, exist_ok=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n"
        "  infra-client: {nics: {net-mgmt: 10.50.0.8, net-data-a: 10.10.1.8, net-ext: 10.60.0.8}}\n"
        "  infra-svc: {org: service, nics: {net-mgmt: 10.50.0.9, net-ext: 10.60.0.9}}\n"
        "groups:\n"
        "  infra: [infra-client, infra-svc]\n"
    )


def test_dns_render_writes_zone_and_config_files(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topo(tmp_path)

    result = CliRunner().invoke(cli.app, ["dns", "render"])

    assert result.exit_code == 0, result.output
    dns_dir = tmp_path / "build" / "work" / "dns"
    assert (dns_dir / "client.com.zone").read_text().startswith("; client.com")
    assert (dns_dir / "service.com.zone").exists()
    # the client infra masters the reverse zones and forwards service.com to its peer
    local = (dns_dir / "named.conf.local.infra-client").read_text()
    assert 'zone "1.10.10.in-addr.arpa"' in local
    opts = (dns_dir / "named.conf.options.infra-client").read_text()
    assert "forwarders { 192.168.121.1; };" in opts  # public -> base-VM egress
    # host resolver facts for the host-resolver role (#477)
    facts = json.loads((dns_dir / "hostfacts.json").read_text())
    assert facts["infra-svc"]["resolver"] == "127.0.0.1"
    assert facts["infra-client"]["fqdn"] == "infra-client.client.com"
