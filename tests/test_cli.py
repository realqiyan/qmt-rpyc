import json
import argparse

import pytest

from qmt_rpyc.cli import client as cli


class _NeverCalled:
    def __getattr__(self, name):
        raise AssertionError("RPC should not be sent without confirmation")


class _FakeClient:
    def __init__(self, surface):
        self._surface = surface
        self.xtdata = _NeverCalled()
        self.trader = _NeverCalled()


def _args(**overrides):
    values = {
        "confirm_trading": False,
        "confirm_write": False,
        "download_mode": False,
        "compact": True,
    }
    values.update(overrides)
    return type("Args", (), values)()


def test_trader_write_requires_confirmation(capsys):
    client = _FakeClient({
        "XtQuantTrader": {
            "methods": {
                "order_stock": {
                    "signature": "(account, code, order_type, volume)"
                }
            }
        }
    })

    status = cli._execute_call(
        client, "trader", "order_stock", ["A", "600000.SH"], {},
        _args(),
    )

    assert status == cli.EXIT_CONFIRMATION
    payload = json.loads(capsys.readouterr().err)
    assert payload["risk"] == "trading"


def test_unknown_xtdata_write_requires_confirmation(capsys):
    client = _FakeClient({
        "xtdata": {
            "functions": {
                "add_sector": {"signature": "(name, stocks)"}
            }
        }
    })

    status = cli._execute_call(
        client, "xtdata", "add_sector", ["watch", []], {}, _args()
    )

    assert status == cli.EXIT_CONFIRMATION
    assert json.loads(capsys.readouterr().err)["risk"] == "write"


def test_callback_api_is_rejected():
    client = _FakeClient({
        "XtQuantTrader": {
            "methods": {
                "query_async": {
                    "signature": "(account, callback)"
                }
            }
        }
    })

    try:
        cli._execute_call(
            client, "trader", "query_async", ["A", None], {}, _args()
        )
    except ValueError as e:
        assert "callback" in str(e)
    else:
        raise AssertionError("callback API was not rejected")


def test_request_file_must_use_json(tmp_path):
    path = tmp_path / "request.json"
    path.write_text(
        json.dumps({
            "surface": "xtdata",
            "name": "get_full_tick",
            "args": [["600000.SH"]],
            "kwargs": {},
        }),
        encoding="utf-8",
    )
    args = type("Args", (), {
        "request": str(path),
        "surface": None,
        "name": None,
        "args": "[]",
        "kwargs": "{}",
    })()

    assert cli._read_request(args) == (
        "xtdata", "get_full_tick", [["600000.SH"]], {}
    )


def test_non_interactive_init_never_prompts(monkeypatch):
    monkeypatch.setenv("QMT_RPYC_AUTH_KEY", "secret-key-123456")
    monkeypatch.setattr(cli, "load_profiles", lambda: {})
    monkeypatch.setattr(cli, "probe_keyring", lambda: (True, ""))
    monkeypatch.setattr(
        cli, "save_profile", lambda *args, **kwargs: "/tmp/client.toml"
    )
    monkeypatch.setattr(
        "builtins.input",
        lambda *args: pytest.fail("input() must not be called"),
    )
    monkeypatch.setattr(
        cli.getpass,
        "getpass",
        lambda *args: pytest.fail("getpass() must not be called"),
    )
    args = cli.build_parser().parse_args([
        "init", "--non-interactive", "--host", "10.0.0.2",
    ])

    cli._cmd_init(args)


def test_client_init_accepts_short_nonempty_key(monkeypatch):
    monkeypatch.setenv("QMT_RPYC_AUTH_KEY", "short")
    monkeypatch.setattr(cli, "load_profiles", lambda: {})
    monkeypatch.setattr(cli, "probe_keyring", lambda: (True, ""))
    saved = {}

    def save_profile(name, values, **kwargs):
        saved.update(kwargs)
        return "/tmp/client.toml"

    monkeypatch.setattr(cli, "save_profile", save_profile)
    args = cli.build_parser().parse_args([
        "init", "--non-interactive", "--host", "10.0.0.2",
    ])

    cli._cmd_init(args)

    assert saved["secret"] == "short"


@pytest.mark.parametrize("secret", ["   ", "your-secret-key-here"])
def test_client_init_rejects_unusable_key(secret, monkeypatch):
    monkeypatch.setenv("QMT_RPYC_AUTH_KEY", secret)
    monkeypatch.setattr(cli, "load_profiles", lambda: {})
    args = cli.build_parser().parse_args([
        "init", "--non-interactive", "--host", "10.0.0.2",
    ])

    with pytest.raises(ValueError, match="must not be empty"):
        cli._cmd_init(args)


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


def test_all_client_command_levels_have_help():
    _assert_all_commands_described(cli.build_parser())


def test_client_no_command_prints_getting_started(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])

    assert exc.value.code == cli.EXIT_USAGE
    stderr = capsys.readouterr().err
    assert "Getting started:" in stderr
    assert "qmt-rpyc-client init" in stderr


def test_client_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])

    assert exc.value.code == 0
    assert "qmt-rpyc-client " in capsys.readouterr().out


def test_client_ctrl_c_is_clean(monkeypatch, capsys):
    def interrupt(_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_cmd_check", interrupt)

    assert cli.main(["check"]) == 130
    stderr = capsys.readouterr().err
    assert json.loads(stderr)["status"] == "interrupted"
    assert "Traceback" not in stderr
