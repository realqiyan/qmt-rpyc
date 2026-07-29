# Migrating to qmt-rpyc 0.3

Version 0.3 uses a clean package namespace and protocol handshake. Client and
server must both be upgraded.

Replace imports:

```python
from client import QmtClient
from client.exceptions import QmtError
```

with:

```python
from qmt_rpyc import QmtClient
from qmt_rpyc.exceptions import QmtError
```

The SDK call semantics are otherwise unchanged.

For an existing Windows source deployment, run the new installer from the
directory containing the old `.env`, then run:

```bat
qmt-rpyc-server init
```

The initializer offers to import the current `.env` into
`%LOCALAPPDATA%\qmt-rpyc\config.env`. It copies the configuration and does not
delete the original.

Old clients cannot connect to 0.3 servers because authentication now completes
before RPyC protocol startup. The failure is intentional and reported as a
protocol/authentication error.
