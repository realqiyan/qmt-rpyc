import json

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
