"""Apply declarative MQ object definitions through pymqrest ensure_*.

LEGACY (Phase-B). Current stacks apply QM content via Ansible runmqsc, not REST;
this module is kept as a pymqrest usage example. For a QM's real REST address, use
`mqlab rest render` (never a hardcoded IP).
Usage: python -m mqlab.apply content/<qm>.yaml <base_url>
Credentials from MQWEB_ADMIN_USER / MQWEB_ADMIN_PASSWORD (never committed).
"""

import os
import sys
from pathlib import Path

import urllib3
import yaml
from pymqrest import BasicAuth, MQRESTSession

# TLS verification of the mqweb REST endpoint (#250). mqweb now serves the org-CA
# `mqweb` cert, but pymqrest's verify_tls is a bool (no CA-path), and the cert
# carries only DNS:mqweb — no IP SANs — so verifying against the IP-based REST URLs
# still fails hostname checks. Until per-host mqweb certs with IP SANs land (a
# lab-pki enhancement), verification is opt-in via MQLAB_REST_VERIFY_TLS; when on,
# point requests at the org CA with REQUESTS_CA_BUNDLE. Default off keeps the
# working provisioning path without hardcoding insecure behaviour.
VERIFY_TLS = os.environ.get("MQLAB_REST_VERIFY_TLS", "false").lower() in ("1", "true", "yes")
if not VERIFY_TLS:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

KIND_TO_METHOD = {
    "qlocal": "ensure_qlocal",
    "qremote": "ensure_qremote",
    "qalias": "ensure_qalias",
    "channel": "ensure_channel",
    "listener": "ensure_listener",
}


def apply_spec(spec_path: str, base_url: str) -> int:
    spec = yaml.safe_load(Path(spec_path).read_text())
    session = MQRESTSession(
        rest_base_url=f"{base_url}/ibmmq/rest/v2",
        qmgr_name=spec["qmgr"],
        credentials=BasicAuth(os.environ["MQWEB_ADMIN_USER"], os.environ["MQWEB_ADMIN_PASSWORD"]),
        verify_tls=VERIFY_TLS,  # opt-in via MQLAB_REST_VERIFY_TLS (#250); see module note
    )
    for obj in spec["objects"]:
        method = getattr(session, KIND_TO_METHOD[obj["kind"]])
        result = method(obj["name"], request_parameters=obj.get("attrs"))
        print(f"{spec['qmgr']} {obj['kind']:<10} {obj['name']:<20} {result.action.name}")
    return 0


def main() -> int:
    return apply_spec(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    sys.exit(main())
