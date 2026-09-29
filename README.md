# qmt-rpyc

通过固定的 Python 模型和操作连接 QMT/MiniQMT。Windows 服务端对接券商定制 xtquant；客户端支持 Linux、macOS 和 Windows。

当前源码版本 **0.8.0**。建议客户端与服务端安装同一份构建；连接时按契约版本和指纹检查兼容性。
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
start-rpyc.bat
```

源码脚本创建 `.venv`，安装开发及服务端依赖，并明确使用仓库 `.env`。`start-rpyc.bat` 优先使用已初始化的源码环境；源码环境不存在时，使用 `install-server.bat` 安装的托管环境及其配置。

Windows 安装或升级：运行 `install-server.bat`，完成初始化和检查后运行 `start-rpyc.bat`。同目录存在唯一 wheel 时优先安装该文件；没有 wheel 时从 PyPI 安装或升级到脚本固定的正式版本（当前 0.8.0）。升级前停止旧服务，已有配置保留。安装器将包安装到 `%LOCALAPPDATA%\qmt-rpyc\venv`；它需要联网安装第三方依赖。后续维护可使用 `"%LOCALAPPDATA%\qmt-rpyc\qmt-rpyc-server.bat" check`。

安装包服务端默认配置位于 `%LOCALAPPDATA%\qmt-rpyc\config.env`。配置向导可探测 MiniQMT、SDK、账户和本地网络；默认生成认证密钥。客户端 profile 使用系统配置目录，密钥优先放入系统 keyring，也可通过 `QMT_RPYC_AUTH_KEY` 提供。客户端配置优先级为命令行、环境变量、profile、默认值。服务端使用 `--config` 指定的文件或默认配置文件，环境变量覆盖文件值。非交互初始化导入已有 `.env` 时需同时提供 `--non-interactive --yes`。

安装 `.[dev]` 后可用 `python -m build` 构建 wheel/sdist，在两端安装同一个 wheel；该命令不生成 Windows ZIP。ZIP 由发布工作流组装。正式版可通过 `pip install qmt-rpyc==0.8.0` 安装；Windows 服务端使用 `pip install "qmt-rpyc[server]==0.8.0"`。

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


### 0.8.0 完整 QMT 部署

客户端与服务端同步升级（公共契约 v8）。Windows 发布 ZIP 包含 GBK 编码的
`bigqmt_strategy.py`：在完整 QMT 加载运行，保持 GBK 编码；私有桥协议为 8。
使用 `install-bigqmt.bat` 安装，`start-bigqmt.bat` 启动外部服务；现有配置保留。
MiniQMT 继续使用原来的安装/启动入口。BigQMT 交易使用每次请求指定的 STOCK 账户；
安装和启动不会自动下单。debug 仍只开放只读方法。

期权标的列表只复用上海当天的有效缓存；过期后同步回源，失败不返回过期值。
两个交易参考字段缺失时为 `None`。下单返回不代表成交，撤单返回只代表发出信号，最终以柜台回报为准。

### Windows 更新与排错

推荐下载并完整解压 Windows Release ZIP，保留安装脚本、`verify-install.py`、wheel
和策略文件在同一目录。在 PowerShell 进入该目录后运行：

```powershell
.\install-bigqmt.bat
# 在 QMT 内停止旧策略，替换并启动包内 bigqmt_strategy.py（GBK）
.\start-bigqmt.bat
```

更新前先停止旧 RPyC 服务。外部服务安装不会自动替换 QMT 内的策略。
新版脚本会强制替换 qmt-rpyc 本体，检查发行版本、契约与桥协议兼容编号，
并在包内存在策略时检查其版本和内容指纹；配置与认证码不随升级覆盖。
启动时会显示所选环境、配置文件、Python 和包路径。环境变量仍优先于配置文件。

同一个版本字符串不代表测试构建与正式包内容一致。发现编号不一致时，请完整解压
正式包并重新安装，不要仅执行 `pip install --upgrade`。PowerShell 调用带引号的
Python 路径需要 `&`，环境变量写作 `$env:LOCALAPPDATA`；`%LOCALAPPDATA%` 是 CMD 语法。
