# qmt-rpyc

通过固定的 Python 模型和操作连接 QMT/MiniQMT。Windows 服务端对接券商定制 xtquant；客户端支持 Linux、macOS 和 Windows。

当前源码版本 **0.8.1rc1**。建议客户端与服务端安装同一份构建；连接时按契约版本和指纹检查兼容性。
[English](README.en.md) · [架构](docs/design/architecture.md) · [完整接口及字段](docs/api/contract.md)

## 安装与启动

客户端要求 Python 3.9+；服务端要求 64 位 Python 3.10 或 3.11，并具备券商 xtquant 和 MiniQMT 环境。

```bash
# 源码安装客户端
scripts/setup.sh
source .venv/bin/activate
qmt-rpyc-client init --profile office
qmt-rpyc-client check --profile office
```

```bat
REM Windows 源码安装服务端
scripts\setup.bat
.venv\Scripts\qmt-rpyc-server.exe --config .env start
```

源码脚本创建 `.venv` 并使用仓库 `.env`。服务端 `start` 默认在当前用户下后台运行；调试使用 `start --foreground`。一个 Python 环境管理一个服务实例，多实例使用不同虚拟环境。

Windows 首次安装运行 `install-server.bat`；BigQMT 使用 `install-server.bat --adapter bigqmt`，适配器选择会保存到配置。同目录唯一 wheel 优先，否则安装脚本固定的版本（当前 0.8.1rc1，从 TestPyPI 获取目标包）。安装器仅用于首次安装，将包安装到 `%LOCALAPPDATA%\qmt-rpyc\venv`，保留已有配置，输出后续命令。已安装环境使用以下入口，不再重复运行安装器：

```bat
"%LOCALAPPDATA%\qmt-rpyc\venv\Scripts\qmt-rpyc-server.exe" start
"%LOCALAPPDATA%\qmt-rpyc\venv\Scripts\qmt-rpyc-server.exe" status
"%LOCALAPPDATA%\qmt-rpyc\venv\Scripts\qmt-rpyc-server.exe" stop
"%LOCALAPPDATA%\qmt-rpyc\venv\Scripts\qmt-rpyc-server.exe" restart
```

`status` 区分本机进程状态、RPC 就绪和 QMT 连接；RPC 不可用时仍可查询本机状态。`restart` 沿用原配置及启动环境覆盖值。后台日志路径由 `start/status` 输出。本期不提供系统服务、开机自启或崩溃自动拉起。

### 本机升级

```bash
qmt-rpyc-client update
qmt-rpyc-server update
qmt-rpyc-server update --pre
qmt-rpyc-server update --version 0.8.1rc1
```

默认升级到官方 PyPI 最新稳定版；`--pre` 或明确指定预发布版本时，从官方 TestPyPI 获取目标包，第三方依赖始终从官方 PyPI 获取。允许指定旧版本降级，不因旧版本缺少后台管理能力而拒绝。默认不会降级，版本无变化时不停止服务。editable 源码安装应通过 Git 更新，`update` 会拒绝覆盖它。

两个命令升级各自本机的当前 Python 环境，不升级远端。客户端命令若发现同环境的服务实例，也会协调停机。下载和依赖准备完成后，停止接收新业务请求，最多等待 60 秒让请求及下载任务结束；超时取消升级并恢复接收请求，不强杀。升级保留配置并备份服务端配置文件；新进程验证版本和命令加载，**不会自动启动服务**，只输出下一步推荐命令。QMT 断线不影响安装成功判定，安装/验证失败明确报告阶段且不自动回滚。

Windows 为释放运行中的 CLI 可执行文件，命令先返回 `scheduled` 和日志路径，独立辅助进程等原命令退出后执行升级；`scheduled` 不代表安装成功。按输出的 PowerShell `Get-Content -LiteralPath "日志路径" -Wait` 查看最终结果和启动建议，看到最终 JSON 后按 Ctrl-C 结束日志查看。也可在 CMD 中用 `type "日志路径"` 查看。跨 MINOR 升降级需要配套更新客户端、服务端及 BigQMT 策略；Python 包更新不会替换 QMT 内的策略。

安装包服务端默认配置位于 `%LOCALAPPDATA%\qmt-rpyc\config.env`。配置向导可探测 MiniQMT、SDK、账户和本地网络；默认生成认证密钥。客户端 profile 使用系统配置目录，密钥优先放入系统 keyring，也可通过 `QMT_RPYC_AUTH_KEY` 提供。客户端配置优先级为命令行、环境变量、profile、默认值。服务端使用 `--config` 指定的文件或默认配置文件，环境变量覆盖文件值。非交互初始化导入已有 `.env` 时需同时提供 `--non-interactive --yes`。

安装 `.[dev]` 后可用 `python -m build` 构建 wheel/sdist，在两端安装同一个 wheel；该命令不生成 Windows ZIP。ZIP 由发布工作流组装。当前 RC 仅发布到 TestPyPI。首次使用 pip 安装时，先下载目标包，再从正式 PyPI 解析依赖：

```bat
py -3.11 -m pip download --no-deps --only-binary=:all: --index-url https://test.pypi.org/simple "qmt-rpyc==0.8.1rc1"
py -3.11 -m pip install --index-url https://pypi.org/simple "qmt_rpyc-0.8.1rc1-py3-none-any.whl[server]"
```

客户端安装同一个 wheel 时去掉 `[server]`；也可使用已支持新版升级命令的 `qmt-rpyc-client update --pre`。

启动日志中的 `SDK module` 行记录实际加载的 xtquant、xtdata、xttrader、xttype 和已加载原生扩展的文件路径；`resolved` 是解析目录联接后的真实路径。排查 SDK 升级时以这些路径为准，适配器名称不代表实际加载的 SDK 版本。

可在服务端配置文件或环境变量中指定 `QMT_XTQUANT_PATH`，值为包含 `__init__.py` 的 **xtquant 包目录**，不是其父级 site-packages。例如：

```dotenv
QMT_PATH=C:\MiniQMT\userdata_mini
QMT_XTQUANT_PATH=C:\MiniQMT\bin.x64\Lib\site-packages\xtquant
```

环境变量优先于配置文件；留空保留默认导入方式。显式路径无效或加载失败时直接报错，不回退旧 SDK；切换后必须重启。`init --xtquant-path PATH` 可保存路径，已有配置再次初始化时会保留该值；显式配置时不修改旧目录联接。`start`、`check`、`xtquant check`、`api dump` 使用同一路径选择规则；独立 `scripts/dump_api_surface.py` 读取进程环境变量中的该设置。此时 `xtquant repair` 不修改联接，应直接更改配置路径。

服务端默认使用 `QMT_RPYC_ADAPTER=xtquant_2.0.6.1`，切换与升级流程见[适配版本设计](docs/design/architecture.md#sdk-适配版本选择)。

完整 QMT 可选 `QMT_RPYC_ADAPTER=bigqmt`，使用本项目独立维护的策略桥，无需 xtquant。
支持行情与股票账户交易接口，交易联调仍待新版实机验收；部署步骤见[桥联调](docs/design/bigqmt-readonly-validation.md)，交易语义见[交易说明](docs/design/bigqmt-trading-probe.md)。

## 服务端持久数据

服务端使用本机 SQLite 保存业务数据，八个读取接口支持 `refresh=True` 同步回源。
有效且覆盖已确认的数据可离线复用；未知覆盖不会因为调用成功就变成缓存命中。
财报与部分来源的复权/停牌补齐仍受验证边界限制，详见[配置与实现状态](docs/design/persistent-cache.md)。
公共契约及 BigQMT 桥编号均为 8；客户端、服务端及策略必须配套升级。已移除板块列表、板块成员和板块下载三个公共操作。

## Python 使用

```python
from qmt_rpyc import QmtClient

with QmtClient.connect_profile("office") as client:
    ticks = client.market.get_ticks(["510050.SH"]).require_all()
    print(ticks["510050.SH"].last_price)
    dates = client.options.get_expiry_dates("510050.SH")
    if dates.dates:
        chain = client.options.get_option_chain("510050.SH", dates.dates[0])
        details = client.options.get_contract_details(chain.contract_codes[:500])
        print(details.require_all())
    print(client.system.get_health())
```

模型从 `qmt_rpyc.contracts.options`、`market`、`trading` 等业务模块导入；异常位于 `qmt_rpyc.contracts.errors`。批量上限 500，每个输入都有对应结果，`require_all()` 在任一失败时抛错。期权发现只覆盖当前尚未到期合约。

## CLI

```bash
qmt-rpyc-client api list
qmt-rpyc-client api describe market.get_ticks
qmt-rpyc-client call --profile office market.get_ticks --payload '{"codes":["510050.SH"]}'
qmt-rpyc-client download --profile office start history --payload '{"code":"510050.SH","period":"1d"}'
qmt-rpyc-client download --profile office status TASK_ID
qmt-rpyc-client download --profile office wait TASK_ID
qmt-rpyc-client self-test --profile office
```

`api list` 默认只显示名称和用途；`api describe` 显示参数、默认值、返回模型及字段含义。加 `--json` 输出机器可读说明。

CLI 交易操作要求 `--confirm-trading`。请求文件使用 `{"operation":"...","payload":{...}}`，通过 `--request` 读取。`api` 根据本地权威契约工作；`check` 报告远端健康和可用性。

## 原始 SDK 调试

服务端设置 `QMT_RPYC_DEBUG=1` 并重启后，可使用独立调试入口查看真实签名和直接调用 SDK：

```bash
qmt-rpyc-client debug --profile office describe xtdata.get_option_detail_data
qmt-rpyc-client debug --profile office call xtdata.get_full_tick --args '[["510050.SH"]]'
```

默认关闭、沿用认证、不参与稳定业务契约。参数直接转发，返回 SDK 字段的 JSON 表示，不自动重试。
Python 使用 `qmt_rpyc.client.debug.DebugClient`；完整限制和示例见[调试接口](docs/api/debug.md)。

## 运行与可靠性

RPC 独立启动，QMT 连接在后台重试；维护期间仍可协商和读取健康状态。交易连接未就绪时明确拒绝，不排队重放。SDK 导入失败、认证配置无效或端口占用仍直接导致启动失败。

日期和时刻分别为 `date` 与带时区的 `datetime`，传输时刻统一 UTC。价格和金额保持来源精度；不会补零、自动四舍五入或把无效记录静默丢弃。

下单、撤单及下载创建在结果未知时禁止自动重试。下载完成只表示 SDK 调用正常结束，不保证数据完整或最新。历史数据覆盖、源量纲及维护期间的行为限制见[架构](docs/design/architecture.md#启动安全与验收)。

## 安全

HMAC 在 RPyC 建连前完成认证，业务消息仅使用 JSON，禁用 pickle。共享密钥持有者对服务实例及其账户数据拥有完整信任。HMAC 不加密流量，部署限定在受信任内部网络；不可信网络需要 TLS 或 VPN。每实例共享一个 Trader，不提供多租户隔离。详见 [SECURITY.md](SECURITY.md)。

## 开发验证

```bash
python -m pip install -e ".[dev]"
bash scripts/test.sh
# 全量合成 SDK 测试：使用 Python 3.10/3.11
python -m pip install -e ".[server,dev]"
python -m pytest tests/ -v
python scripts/dump_contract.py
```

合成 SDK 和本地真实 RPC 测试可跨平台运行。实际部署只读验收需配置客户端 profile，并设置 `QMT_RPYC_LIVE=1` 后运行 `tests/test_live_integration.py`。升级 SDK 或适配器时需重新执行部署验收。

本项目不分发 xtquant、QMT 或 MiniQMT，不提供投资建议。许可证为 [MIT](LICENSE)。


### 0.8.1rc1 完整 QMT 部署

客户端与服务端同步升级（公共契约 v8）。Windows 发布 ZIP 包含 GBK 编码的
`bigqmt_strategy.py`：在完整 QMT 加载运行，保持 GBK 编码；私有桥协议为 8。
首次使用 `install-server.bat --adapter bigqmt` 安装，再执行已安装的 `qmt-rpyc-server start`；现有配置保留。
MiniQMT 使用相同安装器的默认适配器。BigQMT 交易使用每次请求指定的 STOCK 账户；
安装和启动不会自动下单。debug 仍只开放只读方法。

期权标的列表只复用上海当天的有效缓存；过期后同步回源，失败不返回过期值。
两个交易参考字段缺失时为 `None`。下单返回不代表成交，撤单返回只代表发出信号，最终以柜台回报为准。

### Windows 更新与排错

首次安装时完整解压 Windows Release ZIP，保留安装器、`verify-install.py`、wheel 和策略文件。后续通过已安装的命令升级，例如 PowerShell：

```powershell
& "$env:LOCALAPPDATA\qmt-rpyc\venv\Scripts\qmt-rpyc-server.exe" update
# 根据输出查看升级日志，确认最终结果。
# 按目标发行版本在 QMT 内停止旧策略，替换并启动 bigqmt_strategy.py（GBK）。
& "$env:LOCALAPPDATA\qmt-rpyc\venv\Scripts\qmt-rpyc-server.exe" start
```

安装器中的 `verify-install.py` 用于首次安装及发布包验收；日常启动和升级不依赖旧 ZIP 附带的策略文件。环境变量仍优先于配置文件。旧版本的前台服务须先手动停止，才能首次迁移到新管理机制；后台管理不会接管或强杀无法确认归属的旧进程。PowerShell 调用带引号的路径需要 `&`，环境变量写作 `$env:LOCALAPPDATA`；`%LOCALAPPDATA%` 是 CMD 语法。
