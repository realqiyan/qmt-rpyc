# 类型化 QMT 桥接架构

目标是固定公共操作名称、请求参数、返回类型和错误语义，并允许替换底层实现。
OpenAPI 是描述接口的参考；当前传输使用 RPyC，不提供 HTTP 服务。

## 模块与依赖

- `contracts/`：按 market、instruments、options、reference、financials、trading、downloads、system 组织冻结 dataclass；同一业务请求和结果在一起。`common.py` 定义批量项与错误；`operations.py` 是唯一操作注册表，`schema.py` 推导描述，`validation.py` 校验请求和结果的对应关系。
- `client/`：公共 `QmtClient` 组合能力入口；下载句柄只查询任务、不重放创建请求。连接、profile、关闭、自检属于客户端使用体验。
- `transport/`：`auth.py` 负责 socket HMAC；`rpyc.py` 负责连接和 TLS；`codec.py` 只做通用类型编解码；`messages.py` 负责协商、请求关联和执行结果判定。
- `server/`：RPC 认证入口、固定操作路由、下载任务管理、健康报告和进程生命周期。
- `adapters/interfaces.py`：类型化能力接口。`adapters/xtquant_2_0_6_1/` 独立拥有 SDK 签名探针、原始值转换、行情/资料/交易实现、连接重试及回调事件设施。
- `cli/`：配置管理与公共操作调用；Windows 本机诊断可导出 SDK 接口面。

`contracts` 不依赖客户端、服务端、传输和第三方 SDK；客户端不依赖服务端或适配器。
服务端调度只面向 Providers，具体 xtquant 装配位于启动入口。替代实现返回同样的模型，
无需模拟 xtquant 的原始函数或字典。适配器内部可调用通用类型编解码器验证模型，不能依赖 RPyC 连接。
`__init__.py` 仅负责导出，顶层 `QmtClient` 延迟加载，导入模型不会加载网络栈。

## 契约与协商

当前契约标识为整数 `2`；它与包版本、socket 认证协议版本分别管理，不影响 Python 导入路径。
操作表含 28 个操作，覆盖板块、标的、合约、期权、行情、日历、分红、指数、八类财务表、
四种下载、资产、持仓、委托、下单和两类撤单等业务能力。

客户端认证后调用 `negotiate(contract_hash)`，服务端返回 `contract_version`、`contract_hash`
及每个操作的 `Capability(available, adapter_id, reason)`。指纹涵盖模型字段、默认参数、
操作集合和行为修订；不匹配立即拒绝，不猜测降级。SDK 可用性与契约协商分离，
某个方法签名不匹配只禁用依赖它的操作。探针只读取定义，不等待 Trader 登录。

之后唯一稳定业务 RPC 是 `call(payload_json)`。请求字段固定为：

```json
{"contract_version":2,"request_id":"unique-id","operation":"market.get_ticks","payload":{"codes":["510050.SH"]}}
```

成功响应携带相同的版本、请求 ID、操作名，以及 `status="ok"` 和 `data`；失败携带
`status="error"` 和 `OperationError`。客户端严格检查关联字段、错误阶段及返回模型。
RPC 只传 JSON 字符串，禁用 pickle 与通用 public attribute 访问。

## 类型、时间、身份

Python 模型使用冻结 dataclass；返回序列为 tuple，解码后的 Mapping 不可写。
解码拒绝多余字段、缺失必需字段、无效联合判别、重复 JSON 键、bool 充当数字、非有限浮点。
日期为 `date` / `YYYY-MM-DD`；时刻为 aware datetime / UTC 六位微秒 `...000000Z`。
日线用交易日，分钟线用时刻；源时间另存 `source_time`，不混同 bar 标识。
服务与 SDK 诊断入口不全局替换标准库 `datetime.datetime`，避免导入顺序导致模型和严格 codec
持有不同类型。源端数值时间戳在适配器中以 epoch + timedelta 转换。
CPython [3.10](https://github.com/python/cpython/blob/v3.10.0/Python/pytime.c#L127-L155) 和
[3.11](https://github.com/python/cpython/blob/v3.11.9/Python/pytime.c#L260-L301) 已处理小数微秒进位；
[bpo-44831](https://github.com/python/cpython/issues/88994) 是 now/fromtimestamp 的舍入差异，
不能作为“3.12 才修复毫秒时间戳断言”的依据。若特定券商原生扩展仍崩溃，须以崩溃栈和
复现样本定位，在对应版本适配器处理，不能推定 Python 模块替换会修复 C 扩展路径。

日期边界包含两端。count 必须为正数，不能与 start 同时给出，None 表示范围内全部。
当前 xtquant 适配器只接受整秒查询边界。未来交易日覆盖无法确认时明确拒绝。

标识是不透明字符串，保留券商后缀、前导零和原始名称；不把 SHO/SZO 替换为 SH/SZ。
委托编号与柜台合同编号分别建模，撤单二选一。具体 xtquant 实现需要正十进制委托编号，
该要求不扩散到公共模型。查询金额和价格保留有限 float，不替应用四舍五入或转分。

## 期权与字段归属

`options.get_expiry_dates(underlying)` 与 `get_option_chain(underlying, expiry_date)`
仅发现上海市场日期当天及以后的合约；必须实际验证归属和到期日，不直接相信 SDK 目录。
`get_contract_details(codes)` 返回条款：标的、原名称、认购认沽、到期日、行权价和合约单位。
发现所需字段少于详情所需字段；某个名称或单位缺失不能破坏可确定的到期日发现。
期权原名称从证券资料补充，禁止自行拼接。历史合约目录查询不在券商承诺范围内。

通用证券资料和占位日期在 Instrument；交易参考价在 TradingReference；盘口与观测时刻
在 Tick。资料结算价和行情结算价是不同事实，不能互相填充。完整字段见[接口规格](../api/contract.md)。
a-options 展示、计算、保存的条款、盘口、量价、昨收和日期均保留；IV、Greeks 和评分由应用计算。

## 批量、错误与副作用

代码批量上限 500，输入不重复，返回逐项对应并保持请求顺序。空批量不查询 SDK。
Success 与 Failure 为判别联合；缺失、非法、非期权、源错误均不能伪装成零值。
`require_all()` 在任一失败时抛 BatchIncompleteError。保留有界线程并发，不引入全局 SDK 串行锁。

操作错误分别描述 error_type、phase、outcome、operation、contract_version、request_id。
确定在执行前拒绝为 not_executed；读取失败为 not_applicable；有副作用请求的传输失败或
无法验证的结果为 unknown。客户端不自动重试下单、撤单或下载创建；unknown 不能解释为拒绝。
查询到的委托状态使用标准枚举，同时保留源状态码、错误文本和成交事实。

下载创建返回 TaskRef，查询返回 DownloadStatus；pending/running/completed/failed 明确分离。
完成只表示 SDK 正常结束，不保证数据完整、新鲜或覆盖请求区间。超时等待不取消任务，也不重提。
未提供进度的来源返回 None，不生成假进度。任务不存在或已淘汰返回 TASK_NOT_FOUND。

## 启动、安全与验收

RPC 与后台 QMT 连接独立启动；维护期间仍可协商和读取健康状态，交易请求明确拒绝。
共享密钥持有者对该实例拥有完整信任；生产部署为内部受信任网络。HMAC 不加密，TLS 可选。
签名与常量以实际部署 SDK 为依据，完整 discovery 仅作诊断。运行实例共享一个 Trader。

可移植验证使用合成 SDK、真实本地 RPyC、两个消费方全量测试及构建安装验证。
Windows/QMT 验收必须单独执行；源码取证和 mock 通过不能替代它。
首次连接立即尝试，失败后依次等待 10、30、60、600 秒，之后保持 600 秒；默认不限次数。
恢复要求 Trader 连接成功且已配置账户时订阅成功，恢复后重置失败计数并继续心跳。
SDK 导入失败、认证配置无效、端口占用是启动错误，不能当作维护故障忽略。
`qmt-rpyc-server check` 使用同步 probe 验证实际连接，后台任务启动不等于连接成功。

后台线程不能强制取消原生调用或隔离 SDK 进程崩溃。行情查询不以 Trader 状态统一门禁，
桥接没有通用的离线结果缓存或历史完整性判定；SDK 本地有数据也不代表查询区间完整。
下载管理器虽提供 fail_pending，但未接入独立行情连接状态检测，不能承诺断线自动终止所有排队任务。
源数量、权重与财务字段保持源端口径，不凭样本全局换算单位；分钟 bar 时间也不推定为区间起点或终点。

部署 SDK 的实际能力通过[调试入口](../api/debug.md)查询；RPC 无法启动时使用 Windows 本机
`qmt-rpyc-server api dump` 导出，不把恢复旧 RPC 作为维护修复的前提。
诊断结果按需导出到本地，不在仓库保存会过时的部署 JSON 清单或逐次验收日志。

## 显式开启的 SDK 调试

独立 `debug(payload_json)` RPC 仅在服务端 `QMT_RPYC_DEBUG=1` 时调用注入的 SDK 调试处理器，
沿用认证，默认拒绝。它不进入操作注册表或业务契约指纹；使用独立 DebugClient，无需业务协商。
参数直接转发给原始 SDK，返回 JSON 表示，不套用业务 DTO 或提供稳定结果类型。
这是供实际部署取证使用的管理入口，不是应用业务依赖。详见[调试接口](../api/debug.md)。

## SDK 适配版本选择

默认适配标识为 `xtquant_2.0.6.1`，Python 包为 `adapters/xtquant_2_0_6_1/`。
点号是 Python 模块路径分隔符，不能把带点号的版本目录直接当作普通包导入，
所以配置保留版本点号，导入路径使用下划线。版本表示适配目标，不代表运行时已自动证明 SDK 完全兼容。

`adapters/registry.py` 显式注册可安装的适配实现。服务端启动、环境检查、SDK 导出、
原始调试入口均通过同一个注册表选择；每个版本独立拥有连接管理、签名探针、
转换和能力实现，不能把一个版本的连接管理与另一个版本的业务转换混用。
导入注册表不导入 SDK，配置错误可以在 QMT 未连接时检查。

在 Windows 的 `config.env` 中设置（默认可省略）：

```dotenv
QMT_RPYC_ADAPTER=xtquant_2.0.6.1
```

环境变量优先于配置文件。运行 `qmt-rpyc-server check` 后重启服务生效；启动摘要显示
Adapter，`system.get_capabilities` 各操作的 `adapter_id` 报告实际适配标识。
未知或未注册标识直接拒绝启动，不会静默回退，也不会自动切换 SDK。
初始化向导会保留适配版本及 `QMT_RPYC_DEBUG` 设置。

升级步骤：

1. 在实际券商部署导出 SDK 签名/常量并验证数据；公开 SDK 版本号不足以证明行为兼容。
2. 新增合法 Python 版本包，实现原有 Providers 模型与连接、调试、发现入口；注册新的标识。
3. 跑契约、模拟及使用方测试，在对应 Windows SDK 上完成只读实机验收；交易写操作另行验收。
4. 部署包含新旧实现的桥接包，单独安装/配置匹配的券商 SDK，修改 `QMT_RPYC_ADAPTER` 并重启。
5. 若要回退，恢复匹配的 SDK 与旧适配选择并重启。不能只改名字来适配未验证的新 SDK。

当前仅实现并注册 `xtquant_2.0.6.1`，没有第二个生产适配版本。切换机制通过独立测试实现验证。
不按 SDK 字符串自动选择，不在运行中热切换：服务进程共享 Trader，进行中的调用及下载应在重启前结束；
任务标识不跨重启持久化。适配版本和 SDK 升级不改变公共契约，前提是同样的字段与行为仍成立。
若公共语义必须改变，则另行变更契约版本/行为修订，不能靠适配选择绕过指纹协商。
