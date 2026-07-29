from pathlib import Path

from qmt_rpyc.cli import server as cli


def test_default_server_dir_uses_local_app_data(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\test\AppData\Local")

    assert cli.server_dir() == (
        Path(r"C:\Users\test\AppData\Local") / "qmt-rpyc"
    )


def test_validate_values_rejects_short_key_and_invalid_port():
    errors = cli._validate_values({
        "QMT_RPYC_AUTH_KEY": "short",
        "QMT_PATH": "userdata_mini",
        "QMT_RPYC_PORT": "70000",
    })

    assert any("at least 16 bytes" in error for error in errors)
    assert any("between 1 and 65535" in error for error in errors)


def test_non_interactive_init_generates_key_without_prompt(
        tmp_path, monkeypatch):
    target = tmp_path / "config.env"
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QMT_RPYC_AUTH_KEY", raising=False)
    monkeypatch.setattr(cli, "detect_environment", lambda: {
        "miniqmt": None,
        "qmt_path": "C:/QMT/userdata_mini",
        "account_id": "123456",
        "xtquant_site": None,
    })
    monkeypatch.setattr(cli, "private_ipv4_addresses", lambda: ["10.0.0.2"])
    monkeypatch.setattr(
        "builtins.input",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("input() must not be called")
        ),
    )
    args = cli.build_parser().parse_args([
        "--config", str(target), "init", "--non-interactive",
    ])

    assert cli._cmd_init(args) is None
    values = cli._read_env(target)
    assert values["QMT_RPYC_HOST"] == "10.0.0.2"
    assert len(values["QMT_RPYC_AUTH_KEY"].encode("utf-8")) >= 16
    assert values["QMT_PATH"] == "C:/QMT/userdata_mini"
