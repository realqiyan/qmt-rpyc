# Security Policy

## Supported versions

Security fixes are provided for the latest stable release and the current
release candidate.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub Private
Vulnerability Reporting in the repository Security tab and include affected
versions, reproduction details, and impact.

Never include authentication keys, account IDs, private keys, certificates,
local QMT paths, or raw production logs.

## Deployment boundary

qmt-rpyc is designed for a trusted private LAN. Possession of
`QMT_RPYC_AUTH_KEY` grants full access to the server instance, its Trader, and
all available account data. HMAC authenticates but does not encrypt RPC
traffic. Use TLS or a VPN across untrusted networks.

If a shared key is exposed, stop the server, replace `QMT_RPYC_AUTH_KEY` in the active server configuration and any overriding
environment variable, update every client profile, and restart the server.
Running `init` again preserves an existing valid key; it does not rotate it.
