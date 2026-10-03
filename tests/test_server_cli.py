import argparse
import json
from pathlib import Path

import pytest

from qmt_rpyc.cli import server as cli


def test_default_server_dir_uses_local_app_data(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\test\AppData\Local")

    assert cli.server_dir() == (
        Path(r"C:\Users\test\AppData\Local") / "qmt-rpyc"
    )


def test_bigqmt_init_needs_no_sdk_path_account_or_miniqmt(tmp_path, monkeypatch, capsys):
    target = tmp_path / 'config.env'
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('QMT_RPYC_ADAPTER', 'bigqmt')
    monkeypatch.delenv('QMT_RPYC_AUTH_KEY', raising=False)
    def forbidden(*args):
        pytest.fail('independent BigQMT init must not inspect MiniQMT or wire an SDK')
    monkeypatch.setattr(cli, 'detect_environment', forbidden)
    monkeypatch.setattr(cli, 'wire_xtquant', forbidden)
    monkeypatch.setattr(cli, 'private_ipv4_addresses', lambda: [])
    args = cli.build_parser().parse_args(['--config', str(target), 'init', '--non-interactive'])
    cli._cmd_init(args)
    values = cli._read_env(target)
    assert values['QMT_RPYC_ADAPTER'] == 'bigqmt'
    assert not values['QMT_PATH'] and not values['QMT_ACCOUNT_ID']
    assert not cli._validate_values(values)


def test_bigqmt_check_probes_pipe_without_loading_sdk(tmp_path, monkeypatch, capsys):
    from qmt_rpyc.adapters.bigqmt.connection import ConnectionManager
    target = tmp_path / 'config.env'
    target.write_text('QMT_RPYC_ADAPTER=bigqmt\nQMT_RPYC_AUTH_KEY=test-fixture-only\n', encoding='utf-8')
    def forbidden(*args):
        pytest.fail('BigQMT check must not inspect MiniQMT or load the SDK')
    monkeypatch.setattr(cli, 'detect_environment', forbidden)
    monkeypatch.setattr(cli, 'load_sdk', forbidden)
    calls = []
    monkeypatch.setattr(ConnectionManager, 'probe', lambda self: calls.append('probe') or True)
    args = cli.build_parser().parse_args(['--config', str(target), 'check'])
    cli._cmd_check(args)  # Python 3.13 on CI still fails the deployment version check.
    result = json.loads(capsys.readouterr().out)
    assert calls == ['probe']
    assert next(check for check in result['checks'] if check['name'] == 'qmt_connection')['ok']


def test_validate_values_accepts_short_key_and_rejects_invalid_port():
    errors = cli._validate_values({
        "QMT_RPYC_AUTH_KEY": "short",
        "QMT_PATH": "userdata_mini",
        "QMT_RPYC_PORT": "70000",
    })

    assert not any("QMT_RPYC_AUTH_KEY" in error for error in errors)
    assert any("between 1 and 65535" in error for error in errors)


@pytest.mark.parametrize("auth_key", ["", "   ", "your-secret-key-here"])
def test_validate_values_rejects_unusable_key(auth_key):
    errors = cli._validate_values({
        "QMT_RPYC_AUTH_KEY": auth_key,
        "QMT_PATH": "userdata_mini",
        "QMT_RPYC_PORT": "18812",
    })

    assert any("QMT_RPYC_AUTH_KEY" in error for error in errors)


def test_non_interactive_init_generates_key_without_prompt(
        tmp_path, monkeypatch, capsys):
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
    result = json.loads(capsys.readouterr().out)
    assert str(target) in result["next_steps"][0]
    assert result["next_steps"][0].endswith(" check")
    assert str(target) in result["next_steps"][1]
    assert result["next_steps"][1].endswith(" start")


def _assert_all_commands_described(parser):
    assert parser.description
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        visible_help = {
            choice.dest: choice.help for choice in action._choices_actions
        }
        for name, child in action.choices.items():
            assert visible_help[name]
            assert child.description
            _assert_all_commands_described(child)


def test_check_reports_failed_qmt_connection_from_blocking_probe(
        monkeypatch, tmp_path, capsys):
    import sys
    import types

    from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager

    config = tmp_path / "config.env"
    config.write_text(
        "QMT_PATH=C:\\qmt\\userdata_mini\n"
        "QMT_RPYC_AUTH_KEY=test-only-key-not-a-real-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "detect_environment", lambda: {"miniqmt": True})
    monkeypatch.setattr(cli, "managed_xtquant_path", lambda: "xtquant")
    monkeypatch.setitem(sys.modules, "xtquant", types.ModuleType("xtquant"))
    calls = []
    monkeypatch.setattr(ConnectionManager, "probe",
                        lambda self: calls.append("probe") or False)
    monkeypatch.setattr(ConnectionManager, "start",
                        lambda self: pytest.fail("check must not schedule "
                                                 "a background connection"))

    args = cli.build_parser().parse_args(["--config", str(config), "check"])
    assert cli._cmd_check(args) == 1

    payload = json.loads(capsys.readouterr().out)
    connection = [c for c in payload["checks"] if c["name"] == "qmt_connection"]
    assert calls == ["probe"]
    assert connection == [{
        "name": "qmt_connection",
        "ok": False,
        "detail": "initial QMT connection failed",
    }]


def test_all_server_command_levels_have_help():
    _assert_all_commands_described(cli.build_parser())


def test_server_no_command_prints_getting_started(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])

    assert exc.value.code == 2
    stderr = capsys.readouterr().err
    assert "Getting started" in stderr
    assert "qmt-rpyc-server init" in stderr


def test_server_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])

    assert exc.value.code == 0
    assert "qmt-rpyc-server " in capsys.readouterr().out


def test_server_ctrl_c_is_clean(monkeypatch, capsys):
    def interrupt(_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_cmd_start", interrupt)

    assert cli.main(["start"]) == 130
    stderr = capsys.readouterr().err
    assert json.loads(stderr)["status"] == "interrupted"
    assert "Traceback" not in stderr


def test_missing_server_extra_has_install_hint(monkeypatch, capsys):
    def missing_dependency(_args):
        raise ModuleNotFoundError(
            "No module named 'pandas'",
            name="pandas",
        )

    monkeypatch.setattr(cli, "_cmd_start", missing_dependency)

    assert cli.main(["start"]) == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["error_type"] == "ModuleNotFoundError"
    assert "qmt-rpyc[server]" in payload["hint"]
    assert "Python 3.10 or 3.11" in payload["hint"]


def test_init_config_preserves_adapter_and_debug(tmp_path):
    path = tmp_path / 'config.env'
    cli._write_env(path, {'QMT_RPYC_ADAPTER': 'xtquant_2.0.6.1', 'QMT_RPYC_DEBUG': '1'})
    values = cli._read_env(path)
    assert values['QMT_RPYC_ADAPTER'] == 'xtquant_2.0.6.1'
    assert values['QMT_RPYC_DEBUG'] == '1'


@pytest.mark.parametrize("approve_import", [False, True])
def test_non_interactive_existing_env_never_prompts(
        tmp_path, monkeypatch, approve_import):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QMT_RPYC_AUTH_KEY", raising=False)
    (tmp_path / '.env').write_text(
        'QMT_RPYC_AUTH_KEY=your-secret-key-here\n'
        'QMT_RPYC_DEBUG=1\nQMT_RPYC_ALLOW_INSECURE=1\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(cli, 'detect_environment', lambda: {})
    monkeypatch.setattr(cli, 'private_ipv4_addresses', lambda: [])
    monkeypatch.setattr('builtins.input', lambda *args: pytest.fail('unexpected prompt'))
    target = tmp_path / 'config.env'
    argv = ['--config', str(target), 'init', '--non-interactive']
    if approve_import:
        argv.append('--yes')
    args = cli.build_parser().parse_args(argv)
    if not approve_import:
        with pytest.raises(ValueError, match='requires --yes'):
            cli._cmd_init(args)
        assert not target.exists()
        return
    cli._cmd_init(args)
    values = cli._read_env(target)
    assert values['QMT_RPYC_AUTH_KEY'] != 'your-secret-key-here'
    assert len(values['QMT_RPYC_AUTH_KEY']) >= 32
    assert values['QMT_RPYC_DEBUG'] == '1'
    assert values['QMT_RPYC_ALLOW_INSECURE'] == '1'


def test_bigqmt_init_persists_adapter_without_wrapper(tmp_path, monkeypatch):
    target = tmp_path / 'config.env'
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('QMT_RPYC_ADAPTER', raising=False)
    monkeypatch.setattr(cli, 'detect_environment', lambda: pytest.fail('must not probe MiniQMT'))
    monkeypatch.setattr(cli, 'private_ipv4_addresses', lambda: [])
    assert cli.main(['--config', str(target), 'init', '--adapter', 'bigqmt', '--non-interactive']) == 0
    assert cli._read_env(target)['QMT_RPYC_ADAPTER'] == 'bigqmt'


def test_update_version_is_not_global_version_flag():
    parsed = cli.build_parser().parse_args(['update', '--version', '0.7.0'])
    assert parsed.target_version == '0.7.0'


@pytest.mark.parametrize('origin', ['default', 'config', 'environment'])
def test_qmt_generate_uses_configured_pipe_without_sdk(tmp_path, monkeypatch, capsys, origin):
    from qmt_rpyc.adapters.bigqmt.winpipe import DEFAULT_PIPE
    from qmt_rpyc.adapters.bigqmt.strategy import build
    config = tmp_path / 'config.env'
    monkeypatch.setattr(cli, 'server_dir', lambda: tmp_path)
    monkeypatch.delenv('QMT_RPYC_BIGQMT_PIPE', raising=False)
    monkeypatch.delenv('QMT_RPYC_AUTH_KEY', raising=False)
    monkeypatch.setattr(cli, 'load_sdk', lambda *args: pytest.fail('SDK access'))
    monkeypatch.setattr(cli, 'detect_environment', lambda: pytest.fail('environment probing'))
    pipe = DEFAULT_PIPE
    if origin != 'default':
        config.write_text('QMT_RPYC_BIGQMT_PIPE=config_pipe\nQMT_RPYC_AUTH_KEY=synthetic-secret\n')
        pipe = 'config_pipe'
    if origin == 'environment':
        monkeypatch.setenv('QMT_RPYC_BIGQMT_PIPE', 'environment_pipe')
        pipe = 'environment_pipe'
    output = tmp_path / 'nested' / 'strategy.py'
    assert cli.main(['qmt', 'generate', '--output', str(output)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['pipe_name'] == pipe
    assert output.read_bytes() == build(pipe).encode('gbk')
    assert b'synthetic-secret' not in output.read_bytes()
    if origin != 'default':
        assert build(pipe) != build()


def test_qmt_generate_explicit_config_and_overwrite_protection(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv('QMT_RPYC_BIGQMT_PIPE', raising=False)
    config = tmp_path / 'custom.env'
    config.write_text('QMT_RPYC_BIGQMT_PIPE=custom_pipe\n')
    target = tmp_path / 'strategy.py'
    target.write_bytes(b'original')
    args = ['--config', str(config), 'qmt', 'generate', '--output', str(target)]
    assert cli.main(args) == 1
    assert json.loads(capsys.readouterr().err)['error_type'] == 'FileExistsError'
    assert target.read_bytes() == b'original'
    assert cli.main(args + ['--force']) == 0
    assert json.loads(capsys.readouterr().out)['pipe_name'] == 'custom_pipe'
    assert b"PIPE_NAME = 'custom_pipe'" in target.read_bytes()
    original = target.read_bytes()
    monkeypatch.setenv('QMT_RPYC_BIGQMT_PIPE', '../invalid')
    assert cli.main(args + ['--force']) == 1
    assert target.read_bytes() == original
    assert 'invalid local pipe name' in capsys.readouterr().err
