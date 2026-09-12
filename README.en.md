# qmt-rpyc

[中文](README.md)

qmt-rpyc exposes the broker-customized `xtquant` SDK running beside
QMT/MiniQMT on Windows to clients on Linux, macOS, and Windows.

## Install

Client:

Python 3.9 or newer is required.

```bash
pip install qmt-rpyc
qmt-rpyc-client init --profile office
qmt-rpyc-client check --profile office
```

To install a Windows server RC from TestPyPI while resolving dependencies
from PyPI:

```bash
python -m pip install --index-url https://pypi.org/simple \
  --extra-index-url https://test.pypi.org/simple --pre \
  "qmt-rpyc[server]==0.3.1rc2"
```

Server, on Windows with Python 3.10 or 3.11:

```bat
pip install "qmt-rpyc[server]"
qmt-rpyc-server init
qmt-rpyc-server check
qmt-rpyc-server start
```

The RPC server starts independently of the trading connection. Its first
background connection attempt runs immediately; failures wait 10 seconds,
30 seconds, 1 minute, then 10 minutes between subsequent attempts. Retries
are unlimited by default. Recovery resumes heartbeats and resets the retry
schedule. Invalid local configuration, SDK import failures, and occupied
ports still fail startup.

`client.health()` preserves existing fields and adds `connection_state`,
`consecutive_failures`, `last_connection_error`, and `next_retry_at`.
An available RPC connection does not imply trading readiness. Disconnected
trading requests fail without queuing or replay.

Python:

```python
from qmt_rpyc import QmtClient

with QmtClient.connect_profile("office") as client:
    print(client.health())
    print(client.xtdata.get_instrument_detail("600000.SH"))
```

Dynamic CLI calls accept JSON only:

```bash
qmt-rpyc-client api --profile office list xtdata
qmt-rpyc-client call --profile office xtdata get_full_tick \
  --args '[["600000.SH"]]'
```

Trading calls require `--confirm-trading`; unknown writes require
`--confirm-write`. Callback parameters are not supported by the first CLI
release.

The server generates a strong authentication key by default. Custom keys have
no minimum length for compatibility, but the generated strong key remains the
recommended choice.

## Security

Socket HMAC authentication completes before an RPyC connection is created.
Authenticated peers are fully trusted and may access all account and trading
capabilities. HMAC does not encrypt traffic: use only a trusted private LAN,
TLS, or a VPN. See [SECURITY.md](SECURITY.md).

This project does not include or distribute xtquant, QMT, or MiniQMT and does
not provide investment advice.

See [MIGRATION.md](MIGRATION.md) for the breaking `qmt_rpyc` namespace change.
Licensed under the [MIT License](LICENSE).
