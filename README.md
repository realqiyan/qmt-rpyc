# qmt-rpyc

[English](README.en.md)

qmt-rpyc 通过 RPyC 将 Windows QMT/MiniQMT 中的 broker-customized
`xtquant` SDK 暴露给 Linux、macOS 和 Windows 客户端。

> 当前版本为 `0.3.1`。请先在模拟或只读环境验证，再用于实盘。

## 安装

### 客户端

要求 Python 3.9 或更高版本。

```bash
pip install qmt-rpyc
qmt-rpyc-client init --profile office
qmt-rpyc-client check --profile office
```

Windows 服务端固定版本安装：

```bat
py -3.11 -m pip install "qmt-rpyc[server]==0.3.1"
```

后续候选版本发布在 TestPyPI 上，安装时必须以正式 PyPI 作为依赖来源：

```bat
py -3.11 -m pip install --index-url https://pypi.org/simple ^
  --extra-index-url https://test.pypi.org/simple --pre ^
  "qmt-rpyc[server]==<版本>"
```

Python SDK：

```python
from qmt_rpyc import QmtClient

with QmtClient.connect(
    "192.168.1.20",
    port=18812,
    auth_key="shared-secret-123456",
) as client:
    print(client.health())
    print(client.xtdata.get_instrument_detail("600000.SH"))
```

也可以使用初始化后的 profile：

```python
from qmt_rpyc import QmtClient

with QmtClient.connect_profile("office") as client:
    print(client.health())
```

### Windows 服务端

要求：

- MiniQMT 已安装、登录并运行。
- 64 位 Python 3.10 或 3.11，与券商 `xtquant` 扩展匹配。
- 安装期间能够访问 PyPI。

从 GitHub Release 解压 Windows 安装包后运行：

```bat
install-server.bat
```

安装器在 `%LOCALAPPDATA%\qmt-rpyc` 创建专用虚拟环境，然后进入配置
向导。也可手工安装：

```bat
py -3.11 -m venv %LOCALAPPDATA%\qmt-rpyc\venv
%LOCALAPPDATA%\qmt-rpyc\venv\Scripts\pip.exe install "qmt-rpyc[server]"
%LOCALAPPDATA%\qmt-rpyc\venv\Scripts\qmt-rpyc-server.exe init
```

日常操作：

```bat
qmt-rpyc-server check
qmt-rpyc-server start
qmt-rpyc-server status
```

`start` 在前台运行。RPC 独立启动，后台立即尝试连接 MiniQMT 并订阅
配置账户；失败后依次等待 10 秒、30 秒、1 分钟、10 分钟，此后每
10 分钟重试，默认不限次数。恢复后继续心跳，再断线从等待 10 秒开始。
SDK 无法导入、认证配置无效或端口占用等本机启动错误仍会直接失败。

`client.health()` 保留 `connected` 等字段，并提供 `connection_state`
（`connecting`、`waiting_retry`、`connected` 等）、`consecutive_failures`、
`last_connection_error` 和 `next_retry_at`。RPC 可连接不表示交易连接可用；
未连接的交易请求直接报错，不排队或重放。

当前限制（尚未实现，不要按已交付使用）：

- 本地数据完整性判据与断线降级分流未实现，xtdata 转发保持原有行为；
  数据缺失或范围不完整时不会给出可区分的错误。
- 下载任务的断线终止规则未接入连接状态检测，`fail_pending` 尚未生效。
- 10 分钟重试档位，以及运行中断线后的自动恢复，尚未在实际 QMT 上验证。

## 动态 CLI

API 列表和帮助来自实际 Windows 部署：

```bash
qmt-rpyc-client api --profile office list xtdata
qmt-rpyc-client api --profile office describe trader query_stock_asset
```

调用只接受 JSON：

```bash
qmt-rpyc-client call --profile office xtdata get_full_tick \
  --args '[["600000.SH"]]'

qmt-rpyc-client call --profile office trader query_stock_asset \
  --args '["YOUR_ACCOUNT_ID"]'
```

交易方法必须显式确认：

```bash
qmt-rpyc-client call --profile office trader order_stock \
  --args '["YOUR_ACCOUNT_ID","600000.SH",23,100,5,10.0]' \
  --confirm-trading
```

未知写操作要求 `--confirm-write`。带 `callback` 参数的方法首版不支持
CLI 调用。

事件、下载和自检：

```bash
qmt-rpyc-client events --profile office --types order,trade,disconnect
qmt-rpyc-client download --profile office start download_history_data \
  --args '["600000.SH","1d","20240101","20241231"]'
qmt-rpyc-client download --profile office status TASK_ID
qmt-rpyc-client self-test --profile office
```

## 配置

服务端默认配置：

```text
%LOCALAPPDATA%\qmt-rpyc\config.env
```

`qmt-rpyc-server init` 会：

- 尝试读取当前目录 `.env`，确认后导入。
- 探测运行中的 MiniQMT、`userdata_mini`、账户和 `xtquant`。
- 推荐私有 LAN 地址，不静默绑定所有网卡。
- 默认生成强认证密钥，也允许用户自定义。
- 自定义认证密钥没有最短长度限制；短密钥仅用于兼容，仍建议使用默认生成的
  强密钥。
- 经确认后在受管 venv 中建立指向券商 `xtquant` 的 junction。

客户端 profile 使用平台标准配置目录。认证密钥默认保存到 Windows
Credential Manager、macOS Keychain 或 Linux Secret Service。没有可用
keyring 时，可选择环境变量、每次隐藏输入或明确确认后的配置文件存储。

配置优先级：

```text
命令行 > 环境变量 > profile > 默认值
```

## 安全边界

- HMAC 在创建 RPyC Connection 之前完成，未认证连接无法进入
  RPyC/pickle 协议层。
- 认证后保留 `rpyc.classic.obtain()`，响应一次性物化为本地对象。
- 共享密钥持有者对该服务实例拥有完整信任，可以访问全部账户数据和交易
  API。
- HMAC 只认证，不加密流量。仅应部署在受信任私有网络；跨公网或不可信
  网络必须使用 TLS 或 VPN。
- 防火墙规则只能通过显式的
  `qmt-rpyc-server firewall add/remove` 管理。
- 安全问题请按 [SECURITY.md](SECURITY.md) 私密报告。

## 迁移

0.3.0 使用新的专用命名空间，不提供旧包兼容层：

```python
# 旧
from client import QmtClient

# 新
from qmt_rpyc import QmtClient
```

完整说明见 [MIGRATION.md](MIGRATION.md)。

## 开发与测试

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest tests/ -v -k "not live"
```

真实 QMT 测试：

```bat
scripts\test_server.bat
```

## 法律声明

本项目不包含、不分发 `xtquant`、QMT 或 MiniQMT。相关软件、商标、数据和
许可归其权利方所有。本项目不提供投资建议；实盘交易和部署风险由使用者
自行承担。

本项目采用 [MIT License](LICENSE)。
