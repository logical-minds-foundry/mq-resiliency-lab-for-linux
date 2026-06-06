from pathlib import Path

import yaml

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
