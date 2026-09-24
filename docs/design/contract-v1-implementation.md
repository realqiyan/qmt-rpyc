# V1 契约实现与验收

2026-09-23。工作树实现版本为 `0.4.0.dev2`，尚未发布。引入契约需统一升级桥接客户端、服务端及两个使用方；已发布的 `0.3.1` 不提供本协议。dev1 的初次抽样验收未覆盖整条期权链，随后发现的时间显示字段问题及 dev2 验证见[兼容性修复记录](dev2-compatibility-fix.md)。下文 dev1 安装包和测试计数保留为历史记录。

## 同一契约适配多个 SDK

采用 Strategy（`SdkAdapter`）、Registry（`AdapterRegistry`）和 Factory（`create_dispatcher`）。`ContractDispatcher` 负责选择、输入绑定、输出投影和错误封装；SDK 调用和字段转换在策略中完成。

- [V1 快照](../../src/qmt_rpyc/contracts/v1.json)：22 个 API、参数顺序/默认值/允许值、16 个常量、输出模型及语义说明。发布后不可原地修改；两端协商版本并比对规范化 JSON 摘要。包版本、SDK 构建和契约版本相互独立。
- [契约运行时](../../src/qmt_rpyc/contract.py)：固定参数绑定、字段类型检查、split 表投影和错误结构。客户端只从本地快照创建代理；远端新增方法不会进入客户端。
- [适配器边界与分发器](../../src/qmt_rpyc/server/adapters/__init__.py)：同一 V1 中每个 API 独立选择策略，允许新策略只覆盖有变化的接口。优先级最高者生效；最高优先级并列时报告不可用，避免依赖注册顺序。
- [当前部署策略](../../src/qmt_rpyc/server/adapters/baseline.py)：结构化比较签名（参数名称、种类、默认值，忽略注解和 self），检查依赖常量，显式传入 V1 默认值。期权名称由完整资料补入，该资料接口是详情接口的兼容性依赖。

目前仅声明支持已有取证的 `xtquant-baseline-20260923-v1`。没有把任何未取得 SDK 的真实版本冒充为已支持。`tests/test_contract.py::test_same_v1_contract_across_two_sdk_shapes` 用合成 SDK 的方法名、参数名、字段名变化验证第二套策略可输出完全相同的 V1；这是扩展机制测试，不是第二个真实部署的验收。

增加真实 SDK 适配时：取得该部署的签名/常量/返回样本；实现新的 `SdkAdapter`，将兼容条件写入无副作用的 `probe`，在 `invoke` 内转换参数、常量、单位和返回字段；在 `create_dispatcher` 注册；复用同一 V1 回归样本验证。不要修改 V1 来迎合新 SDK，也不要放宽基线探针假装兼容。

启动只反射定义，不调用查询、下载、交易来试探兼容性。某个探针失败时记录 warning 和能力原因，RPC 仍启动，固定方法仍存在；调用该方法返回 `APIUnavailable`。整包 SDK 缺失等本地安装故障依然是启动错误。原始完整发现功能保留给离线诊断脚本，退出运行时路由。

## 输出与错误

维持 dict/list/scalar/split 表和既有字段名，仅投影快照列出的字段。默认 K 线十列与显式 time 列分开；财务默认固定八表及各自字段，下载也显式使用这八表，避免 SDK 默认增加 PerShare 等表改变范围。合法空表和 nullable 价格保留，缺必需字段或类型错误整次调用失败；批量保留原顺序、逐项错误和最多 500 项的并发限制。

时间模型保留委托 Unix 秒、行情/交易日 Unix 毫秒、分红浮点毫秒、日线八位整数索引、分钟线十四位整数索引。毫秒字段明确覆盖 1980–9999，秒字段覆盖 1970–9999，用固定范围拒绝常见的秒/毫秒漂移；适配层不根据数值长度猜测或自动转换。完整资料的日期占位值保留，普通日期和 K 线索引检查格式。单靠范围和类型不能识别所有语义漂移，仍需要 SDK 版本的针对性证据。

`order_stock` 返回正委托编号或 SDK 明确拒绝值 -1；按委托号撤单允许 0/-1/-2/-3，按柜台编号撤单允许 0/-1。V1 下单输入目前只开放 STOCK_BUY/STOCK_SELL 和 LATEST_PRICE；查询仍保留原始 price_type 数字。撤单成功不等于无成交，需继续查询委托。

错误携带 `error_type/api/contract_version/phase/outcome`。执行前拒绝为 `not_executed`；交易请求发出后的网络异常、SDK 执行异常、输出不合法为 `unknown`，客户端映射为 `OutcomeUnknownError`，绝不自动重发。只读 Trader 查询的 SDK 执行失败为 `not_applicable`，不会被误报成交易结果未知。查询类输出失败映射为 `ContractError`；批量结果可用公开 `QmtError.from_response()` 恢复相同异常分类。

四个下载返回现有任务句柄；`completed` 只表示 SDK 调用正常返回，结果归一为 None，不证明数据新鲜或完整。V1 不导出 SDK 生命周期、回调、原生对象和旧事件 RPC，`events` CLI 同步移除；服务端内部连接回调/EventBus 保留。`self_test()` 仅查询并跳过下载及不可用能力。

## 使用方迁移

`/home/yhb/a-trader`：公开 `contract_version/capabilities()` 替代私有 `_surface` 检查，补上持仓与期权详情能力；完整资料读取嵌套 ExtendInfo；期权详情直接使用桥接模型；下载必须提供可等待句柄；批量契约错误不转为空结果；缺资产字段不补零；研究请求只接受六种周期；执行前确定拒绝与结果未知分别处理。

`/home/yhb/a-options`：下载完成后才能读取缓存，失败/超时向上传递；查询及批量的桥接异常不会被空结果吞掉；按列名读取 split 分红表的 dr；未知期权类型/单位和缺失 OHLC 拒绝计算，不用 PUT/10000/0 猜测。

两仓库依赖和启动检查版本现已指向未发布的 `0.4.0.dev2`；新环境须先安装本地构建 wheel，不能从 PyPI 安装这个尚未发布的版本。启动检查发现缺失时会提示使用本地 wheel，不会尝试从 PyPI 获取 dev2。当前工作不包括发布或生产部署。

## 已验证与部署边界

- 既有只读 RPC 样本覆盖的模型全部通过投影测试，没有改写现有数值。
- [复权只读补采](../api/contract-v1-rpc-dividends-20260923.json)：510050.SH 两次现金分红、600000.SH 三次现金分红，1,311 根有效日线；原始 close / SDK front_ratio close 与日期之后的 dr 乘积最大误差 `2.22e-16`。这支持上述股票/ETF事件及既有除积算法，不扩展宣称所有特殊公司行为、所有市场均已验证。
- 模拟验证包括同一契约的两个 SDK 策略、签名/默认值/常量漂移、依赖传播、字段过滤、空值/空表、交易结果未知、批量隔离、真实本机 RPyC 传输和维护期间冷启动。
- Windows 新服务部署后，已通过真实 RPC 验证两端均为 `0.4.0.dev1`、V1 摘要一致、22 个接口探针全部兼容、QMT/Trader 已连接。[部署验收记录](../api/contract-v1-deployment-validation-20260923.json) 保存了 33 次成功的只读调用摘要，以及批量非法周期隔离和未导出方法隐藏检查；未保存账户数据。覆盖 12 个 xtdata 查询接口，包含行情、交易日、资料、期权、分红、财务和指数权重。
- 默认字段的 1m、1d 样本为非空；带 time 列的分钟周期补查仍为空表，5m/15m/30m/1h 非空索引及财务业务单位仍是部署验收边界。Trader 账户模型、真实下单/撤单和下载未做实盘验证。这里的错误检查不能解决原生崩溃/挂起，也不能证明签名相同就语义相同。
- 部署验收发现 a-trader、a-options 虚拟环境仍安装 `0.3.1rc1`，已各自使用本地 wheel 更新至 `0.4.0.dev1`；不重启应用进程，已有进程需重启后才会加载新包。

本地已构建 `dist/qmt_rpyc-0.4.0.dev1-py3-none-any.whl` 和同版本 sdist。wheel SHA-256：`19a28c3b9e7a2862afea6070d444b6bce5d8c5ef6725ccf927aaa0af6661ecd3`。两者通过 `twine check`；wheel 在独立虚拟环境中安装成功，两个 CLI 的 help/version 均通过，安装后的 V1 摘要与源码一致。

本次恢复开发后的本地验证：

- qmt-rpyc：`python -m pytest tests/ -q`，222 passed、59 skipped。
- a-options：使用当前桥接源码运行全量测试，396 passed、23 skipped；显式置空 `QMT_RPYC_HOST`，跳过真实 QMT。`scripts/check_source_sizes.py` 通过。
- a-trader：使用当前桥接源码运行全量测试，1,312 passed；另跑 QMT、升级、行情、期权发现和行情 HTTP 接口相关测试 111 passed。仅出现既有 Starlette/httpx 弃用警告；`scripts/check_decide_isolation.py` 通过。
- 三个仓库 `git diff --check` 通过。

以下为 Windows 安装命令留档；用户已完成安装，且上面的 RPC 验收确认新服务已运行：

```powershell
py -3.11 -m pip install '.\qmt_rpyc-0.4.0.dev1-py3-none-any.whl[server]'
qmt-rpyc-server start
```

对应客户端也安装同一 wheel，然后用既有 profile 检查：

```text
qmt-rpyc-client check --profile default
qmt-rpyc-client api list xtdata --profile default
```

`check` 包含契约版本和逐接口能力。首次部署请保留启动中的 `Contract V1 ... unavailable` 警告及 check 的能力部分；不要回传认证密钥或账户数据。验收不需要真实下单或撤单。
