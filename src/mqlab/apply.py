"""Apply declarative MQ object definitions through pymqrest ensure_*.

Usage: uv run python -m mqlab.apply content/qm-main.yaml https://10.30.0.10:9443
Credentials from MQWEB_ADMIN_USER / MQWEB_ADMIN_PASSWORD (never committed).
"""

import os
import sys
from pathlib import Path

import urllib3
import yaml
from pymqrest import BasicAuth, MQRESTSession

# Lab QMs use self-signed certs and verify_tls=False (security out of
# scope, spec 1) - drop the per-request InsecureRequestWarning noise.
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
        credentials=BasicAuth(
            os.environ["MQWEB_ADMIN_USER"], os.environ["MQWEB_ADMIN_PASSWORD"]
        ),
        verify_tls=False,  # lab self-signed; security out of scope (spec 1)
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
