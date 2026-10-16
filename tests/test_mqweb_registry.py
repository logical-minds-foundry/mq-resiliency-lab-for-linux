"""mqwebuser.xml registry and roles (epic .github#341 spec §2.2, #1500).

Pins the security model the mqweb role renders:

- IBM's three roles (S8), one sample user each, bound on BOTH the Console and the REST
  application; no anonymous (`EVERYONE`) binding anywhere;
- IBM S16 to the letter: the server's one AES key file is included, and EVERY password in
  the file (the three users and the keystore's) is an encoded string, never plaintext;
- the encode-once machinery (tasks/encode.yml) that keeps a re-render byte-identical, so a
  re-provision never restarts mqweb (Review Focus 1);
- the retired admin plumbing (`mqweb_admin_*`, `mqweb_console_anonymous`) stays gone.
"""

from __future__ import annotations

from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ANSIBLE = REPO_ROOT / "ansible"
ROLE = ANSIBLE / "roles" / "mqweb"
TEMPLATE = ROLE / "templates" / "mqwebuser.xml.j2"
DEFAULTS = ROLE / "defaults" / "main.yml"
MAIN_TASKS = ROLE / "tasks" / "main.yml"
ENCODE_TASKS = ROLE / "tasks" / "encode.yml"
GROUP_VARS = ANSIBLE / "group_vars" / "all"
SERVER_DIR = "/var/mqm/web/installations/Installation1/servers/mqweb"
_TRUTHY = {"true", "yes", "1"}
ENCODED = {"mqadmin": "{aes}AAA", "mqreadonly": "{aes}BBB", "mqoperator": "{aes}CCC"}


def _defaults() -> dict:
    return yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))


def _render(**overrides: object) -> str:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE.parent)),
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        autoescape=True,  # no-op for these markup-free fixture values; satisfies CodeQL
    )
    # Ansible's `bool` filter, which the template uses on its toggles.
    env.filters["bool"] = lambda v: v if isinstance(v, bool) else str(v).lower() in _TRUTHY
    ctx = {
        **_defaults(),
        "mqweb_encoded_passwords": {**ENCODED, "keystore": "{aes}KEY"},
        "mqweb_server_dir": SERVER_DIR,
        **overrides,
    }
    return env.get_template(TEMPLATE.name).render(**ctx)


def _tasks(path: Path) -> list[dict]:
    tasks = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(tasks, list) and tasks, f"{path} did not parse to a non-empty task list"
    return tasks


def _index_of(tasks: list[dict], name_substr: str) -> int:
    for i, task in enumerate(tasks):
        if name_substr in (task.get("name") or ""):
            return i
    raise AssertionError(f"no task whose name contains {name_substr!r}")


# --- the rendered registry --------------------------------------------------------------


def test_includes_the_aes_key_file() -> None:
    # IBM S16: the key file is included from mqwebuser.xml.
    assert f'<include location="{SERVER_DIR}/aes-key.xml"/>' in _render()


def test_keystore_password_is_encoded_not_plaintext() -> None:
    # IBM S16 applies to EVERY password in the file, including the keystore's.
    out = _render(mqweb_keyrpwd="plaintext-keystore-password")
    assert 'type="PKCS12" password="{aes}KEY"' in out
    assert "plaintext-keystore-password" not in out
    assert "mqweb_keyrpwd" not in TEMPLATE.read_text(encoding="utf-8")


def test_no_anonymous_binding() -> None:
    out = _render()
    assert "EVERYONE" not in out
    assert "special-subject" not in out


def test_each_user_has_its_ibm_role_on_console_and_rest() -> None:
    out = _render()
    for app in ("com.ibm.mq.console", "com.ibm.mq.rest"):
        block = out.split(f'id="{app}"', 1)[1].split("</enterpriseApplication>", 1)[0]
        assert '<security-role name="MQWebAdmin">' in block
        assert '<user name="mqadmin" realm="defaultRealm"/>' in block
        assert '<security-role name="MQWebAdminRO">' in block
        assert '<user name="mqreadonly" realm="defaultRealm"/>' in block
        assert '<security-role name="MQWebUser">' in block
        assert '<user name="mqoperator" realm="defaultRealm"/>' in block


def test_registry_carries_only_encoded_passwords() -> None:
    out = _render()
    for user, enc in ENCODED.items():
        assert f'<user name="{user}" password="{enc}"/>' in out
    for user in ENCODED:
        assert f'password="{user}"' not in out
    # Every password attribute in the file is an encoded string (IBM S16).
    values = [chunk.split('"', 1)[0] for chunk in out.split('password="')[1:]]
    assert len(values) == len(ENCODED) + 1  # three users + the keystore
    assert all(v.startswith("{aes}") for v in values), values


def test_tls_off_has_no_keystore_password() -> None:
    out = _render(mqweb_tls=False)
    assert "keyStore" not in out
    assert '<sslDefault sslRef="mqDefaultSSLConfig"/>' in out


def test_render_is_byte_identical_across_runs() -> None:
    # Review Focus 1: a re-provision must not change the file (it would restart mqweb).
    assert _render() == _render()


def test_role_defaults_drop_console_anonymous() -> None:
    defaults = _defaults()
    assert "mqweb_console_anonymous" not in defaults
    assert [u["name"] for u in defaults["mqweb_users"]] == ["mqadmin", "mqreadonly", "mqoperator"]
    assert [u["role"] for u in defaults["mqweb_users"]] == [
        "MQWebAdmin",
        "MQWebAdminRO",
        "MQWebUser",
    ]
    # IBM S8: a registry user ID is at most 12 characters.
    assert all(len(u["name"]) <= 12 for u in defaults["mqweb_users"])


def test_secrets_come_from_mqlab_exports_never_a_build_path() -> None:
    defaults = _defaults()
    for var in ("mqweb_aes_passphrase", "mqweb_state_dir"):
        assert defaults[var] == "{{ lookup('env', '" + var.upper() + "') }}", var
    assert "build/" not in DEFAULTS.read_text(encoding="utf-8")
    assert "build/" not in ENCODE_TASKS.read_text(encoding="utf-8")


# --- the retired admin plumbing ----------------------------------------------------------


def test_group_vars_drop_the_retired_admin_and_anonymous_vars() -> None:
    for path in sorted(GROUP_VARS.glob("*.yml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for retired in ("mqweb_console_anonymous", "mqweb_admin_user", "mqweb_admin_password"):
            assert retired not in data, f"{path.name} still sets {retired}"


def test_no_ansible_reference_to_the_retired_admin_secret() -> None:
    for path in sorted(ANSIBLE.rglob("*")):
        if path.is_file() and path.suffix in {".yml", ".yaml", ".j2"}:
            text = path.read_text(encoding="utf-8")
            for retired in ("mqweb_admin_password", "MQWEB_ADMIN_PASSWORD", "mqweb_admin_user"):
                assert retired not in text, f"{path.relative_to(REPO_ROOT)} mentions {retired}"


# --- the encode-once machinery (IBM S16) -------------------------------------------------


def test_encode_runs_after_the_keystore_stash_and_before_the_registry() -> None:
    tasks = _tasks(MAIN_TASKS)
    stash = _index_of(tasks, "stash the mqweb keystore password")
    encode = _index_of(tasks, "encode the registry passwords once")
    registry = _index_of(tasks, "mqweb basic registry")
    assert stash < encode < registry
    assert tasks[encode]["ansible.builtin.include_tasks"] == "encode.yml"
    dest = tasks[registry]["ansible.builtin.template"]["dest"]
    assert dest == "{{ mqweb_server_dir }}/mqwebuser.xml"


def _security_utility_tasks() -> list[dict]:
    return [
        t
        for t in _tasks(ENCODE_TASKS)
        if "{{ mqweb_security_utility }}"
        in (t.get("ansible.builtin.command") or {}).get("argv", [None])[:1]
    ]


def test_security_utility_calls_follow_ibm_s16() -> None:
    calls = _security_utility_tasks()
    argvs = [t["ansible.builtin.command"]["argv"] for t in calls]
    generate = [a for a in argvs if a[1] == "generateAESKey"]
    encode = [a for a in argvs if a[1] == "encode"]
    assert len(generate) == 1
    assert generate[0][2:] == [
        "--createConfigFile={{ mqweb_server_dir }}/aes-key.xml",
        "--key={{ mqweb_aes_passphrase }}",
    ]
    assert len(encode) == 2  # the user passwords and the keystore password
    for argv in encode:
        assert argv[2:4] == ["--encoding=aes", "--aesConfigFile={{ mqweb_server_dir }}/aes-key.xml"]


def test_security_utility_runs_as_mqm_with_mqs_jre_and_never_logs() -> None:
    # securityUtility is a Liberty script needing JAVA_HOME (MQ's JRE is not on PATH), and
    # its arguments are the passphrase and the plaintext passwords.
    for task in _security_utility_tasks():
        assert task["environment"] == {"JAVA_HOME": "{{ mqweb_java_home }}"}, task["name"]
        assert task["become"] is True and task["become_user"] == "mqm", task["name"]
        assert task["no_log"] is True, task["name"]
        assert task["run_once"] is True, task["name"]


def test_encoded_output_is_validated_fail_loud() -> None:
    for task in _security_utility_tasks():
        if task["ansible.builtin.command"]["argv"][1] == "encode":
            assert "{aes}" in task["failed_when"].replace("[{]", "{").replace("[}]", "}")


def test_key_is_generated_only_when_the_controller_has_none() -> None:
    tasks = _tasks(ENCODE_TASKS)
    gen = tasks[_index_of(tasks, "generate the server's AES key ONCE")]
    assert gen["when"] == "mqweb_key_is_new | bool"
    # generateAESKey refuses to overwrite, so a stale node key is removed first.
    stale = _index_of(tasks, "remove a stale node-local key")
    assert stale < _index_of(tasks, "generate the server's AES key ONCE")
    # The controller copy is the one placed on every node.
    place = tasks[_index_of(tasks, "place the same key on THIS node")]["ansible.builtin.copy"]
    assert place["src"] == "{{ mqweb_state_dir }}/aes-key.xml"
    assert (place["owner"], place["mode"]) == ("mqm", "0600")


def test_every_persisted_secret_is_0600_and_never_logged() -> None:
    for task in _tasks(ENCODE_TASKS):
        copy = task.get("ansible.builtin.copy")
        if copy is not None:
            assert copy["mode"] == "0600", task["name"]
            assert task["no_log"] is True, task["name"]


def test_user_passwords_are_username_encoded_once() -> None:
    tasks = _tasks(ENCODE_TASKS)
    enc = tasks[_index_of(tasks, "encode user passwords not yet encoded")]
    # password = username (spec §2.2, LAB ONLY, §8 D2)
    assert enc["ansible.builtin.command"]["argv"][-1] == "{{ item.item.name }}"
    # encoded only when missing, or when a new key invalidates every earlier string
    assert enc["when"] == "mqweb_key_is_new | bool or not item.stat.exists"
